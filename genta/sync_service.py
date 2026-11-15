"""Opt-in TLS pairing and event synchronization for desktop and native Android."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import secrets
import ssl
import threading
from urllib.parse import urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from calendar_service import secret, set_secret
from store import Store


def certificate(folder):
    folder = Path(folder)
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    cert_path, key_path = folder / 'sync-cert.pem', folder / 'sync-key.pem'
    if not cert_path.exists() or not key_path.exists():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Genta paired device')])
        now = datetime.now(timezone.utc)
        cert = x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True).sign(key, hashes.SHA256())
        key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key_path.chmod(0o600)
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    der = ssl.PEM_cert_to_DER_cert(cert_path.read_text())
    return cert_path, key_path, hashlib.sha256(der).hexdigest()


class SyncHub:
    def __init__(self, store_path, host, port=8784):
        ipaddress.ip_address(host)
        if host in ('0.0.0.0', '::'):
            raise ValueError('Choose one explicit local interface instead of exposing all interfaces')
        self.store_path = Path(store_path)
        self.host, self.port = host, port
        self.token = secret('sync_hub')
        if not self.token:
            self.token = secrets.token_urlsafe(32)
            set_secret('sync_hub', self.token)
        cert_path, key_path, self.fingerprint = certificate(self.store_path.parent)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def log_message(self, format, *args):
                pass  # Never log pairing tokens or private planner payloads.

            def setup(self):
                super().setup()
                self.connection.settimeout(15)

            def send_json(self, status, body):
                raw = json.dumps(body, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(raw)
                self.close_connection = True

            def do_POST(self):
                if self.path != '/v1/sync':
                    self.send_json(404, {'error': 'Not found'})
                    return
                if self.headers.get('Origin') or not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + owner.token):
                    self.send_json(401, {'error': 'Pair this device first'})
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 2 * 1024 * 1024:
                        raise ValueError('Invalid request size')
                    request = json.loads(self.rfile.read(size))
                    cursor = request.get('cursor', 0)
                    if type(cursor) is not int or cursor < 0:
                        raise ValueError('Invalid sync cursor')
                    store = Store(owner.store_path)
                    try:
                        store.ingest_events(request.get('events', []))
                        events = store.sync_events(cursor)
                    finally:
                        store.close()
                    self.send_json(200, {'events': events, 'cursor': events[-1]['seq'] if events else cursor, 'more': len(events) == 500})
                except (ValueError, KeyError, TypeError):
                    self.send_json(400, {'error': 'Invalid synchronization data'})

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.server.daemon_threads = True
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(cert_path, key_path)
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, name='GentaSyncHub', daemon=True)

    def start(self):
        self.thread.start()
        return self.pairing()

    def pairing(self):
        host = '[' + self.host + ']' if ':' in self.host else self.host
        return {'version': 1, 'endpoint': f'https://{host}:{self.server.server_port}', 'token': self.token, 'fingerprint': self.fingerprint}

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)


def paired_request(pairing, body):
    parsed = urlparse(pairing['endpoint'])
    if parsed.scheme != 'https' or not parsed.hostname or parsed.path not in ('', '/') or parsed.username or parsed.password:
        raise ValueError('Pairing endpoint must be an HTTPS origin')
    fingerprint = pairing['fingerprint'].replace(':', '').lower()
    if len(fingerprint) != 64 or any(c not in '0123456789abcdef' for c in fingerprint):
        raise ValueError('Pairing needs a SHA-256 certificate fingerprint')
    # Paired self-signed certificates are authenticated by an out-of-band pin.
    # Verify the pin BEFORE sending Authorization or planner content.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    connection = http.client.HTTPSConnection(parsed.hostname, parsed.port or 443, timeout=20, context=context)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if not hmac.compare_digest(actual, fingerprint):
            raise ValueError('Peer certificate changed. Re-pair this device; no planner data was sent.')
        connection.request('POST', '/v1/sync', json.dumps(body).encode(), {'Authorization': 'Bearer ' + pairing['token'], 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            raise ValueError('Synchronization response is too large')
        if response.status != 200:
            raise ValueError(f'Peer refused synchronization (HTTP {response.status})')
        return json.loads(raw)
    finally:
        connection.close()


def pair_device(store, pairing):
    # Confirm identity and credentials before persisting the peer configuration.
    paired_request(pairing, {'events': [], 'cursor': 0})
    set_secret('sync_peer', pairing['token'])
    store.set_setting('sync_endpoint', pairing['endpoint'])
    store.set_setting('sync_fingerprint', pairing['fingerprint'])
    store.set_setting('sync_push_cursor', 0)
    store.set_setting('sync_pull_cursor', 0)


def synchronize(store_path):
    store = Store(store_path)
    try:
        endpoint = store.get_setting('sync_endpoint')
        if not endpoint:
            raise ValueError('Pair this device with a Genta hub first')
        pairing = {'endpoint': endpoint, 'fingerprint': store.get_setting('sync_fingerprint'), 'token': secret('sync_peer')}
        if not pairing['token']:
            raise ValueError('The peer token is unavailable in the system keyring. Re-pair this device.')
        sent = received = 0
        for _ in range(100):
            outgoing = store.sync_events(store.get_setting('sync_push_cursor', 0))
            cursor = store.get_setting('sync_pull_cursor', 0)
            response = paired_request(pairing, {'events': outgoing, 'cursor': cursor})
            incoming = response['events']
            store.ingest_events(incoming)
            if outgoing:
                store.set_setting('sync_push_cursor', outgoing[-1]['seq'])
            store.set_setting('sync_pull_cursor', response['cursor'])
            sent += len(outgoing)
            received += len(incoming)
            if not response.get('more') and len(outgoing) < 500:
                return {'sent': sent, 'received': received}
        raise ValueError('Synchronization exceeded 50,000 events; reconnect to continue')
    finally:
        store.close()
