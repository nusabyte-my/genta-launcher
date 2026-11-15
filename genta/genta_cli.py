#!/usr/bin/env python3
"""Genta command-line actions and local stdio MCP server for Claude/Cursor."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import sys

from store import Store


def focus_action(action, **arguments):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtNetwork import QLocalSocket
    application = QCoreApplication.instance() or QCoreApplication(['genta-action'])
    from platform_support import ipc_address
    socket = QLocalSocket()
    socket.connectToServer(ipc_address())
    if not socket.waitForConnected(2000):
        raise ValueError('Start the Genta desktop app before controlling focus')
    socket.write(json.dumps({'action': action, **arguments}).encode() + b'\n')
    socket.waitForBytesWritten(2000)
    if not socket.waitForReadyRead(3000):
        raise ValueError('Genta did not acknowledge the action')
    reply = json.loads(bytes(socket.readAll()))
    socket.disconnectFromServer()
    if 'error' in reply:
        raise ValueError(reply['error'])
    return reply


TOOLS = [
    ('tasks_list', 'List tasks, including their stable synchronization identifiers.', {'day': {'type': ['string', 'null']}, 'include_done': {'type': 'boolean'}}),
    ('task_add', 'Add a task. Trailing English date/time phrases are recognized.', {'text': {'type': 'string'}, 'day': {'type': ['string', 'null']}, 'priority': {'type': 'integer', 'minimum': 0, 'maximum': 2}, 'project': {'type': 'string'}, 'repeat_rule': {'type': 'string', 'enum': ['none', 'daily', 'weekdays', 'weekly', 'monthly']}, 'deadline': {'type': ['string', 'null']}, 'estimate_minutes': {'type': 'integer', 'minimum': 1, 'maximum': 480}}),
    ('task_update', 'Update a task by its local numeric ID.', {'task_id': {'type': 'integer'}, 'changes': {'type': 'object'}}),
    ('note_read', 'Read one daily note.', {'day': {'type': 'string'}}),
    ('note_write', 'Save a daily note; earlier versions remain available in history.', {'day': {'type': 'string'}, 'text': {'type': 'string'}}),
    ('insights_read', 'Read actual task completions, active focus time, streak, and best hours.', {}),
    ('focus_start', 'Start a desktop focus timer; zero seconds selects stopwatch.', {'seconds': {'type': 'integer', 'minimum': 0, 'maximum': 10800}}),
    ('focus_pause', 'Pause the running desktop focus session.', {}),
    ('focus_end', 'Finish and save the desktop focus session.', {}),
    ('plan_day', 'Schedule untimed tasks around cached read-only calendar events.', {'end_hour': {'type': 'integer', 'minimum': 0, 'maximum': 23}}),
    ('wrap_up', 'Move unfinished tasks to tomorrow or Inbox, clearing old reminder times.', {'destination': {'type': 'string', 'enum': ['tomorrow', 'inbox']}}),
]
REQUIRED = {'task_add': ['text'], 'task_update': ['task_id', 'changes'], 'note_read': ['day'], 'note_write': ['day', 'text'], 'focus_start': ['seconds'], 'wrap_up': ['destination']}


def dispatch(name, arguments):
    if name.startswith('focus_'):
        return focus_action(name, **arguments)
    store = Store()
    try:
        if name == 'tasks_list':
            return store.tasks(arguments.get('day', 'all'), arguments.get('include_done', True))
        if name == 'task_add':
            return store.add_task(**arguments)
        if name == 'task_update':
            return store.update_task(arguments['task_id'], **arguments['changes'])
        if name == 'note_read':
            return {'day': arguments['day'], 'text': store.note(arguments['day'])}
        if name == 'note_write':
            store.save_note(arguments['day'], arguments['text'])
            return {'saved': True}
        if name == 'insights_read':
            return store.insights()
        if name == 'plan_day':
            from calendar_service import Calendars
            return store.plan_day(Calendars(store).events(), end_hour=arguments.get('end_hour', 18))
        if name == 'wrap_up':
            return {'moved': store.wrap_up(arguments['destination'])}
        raise ValueError('Unknown tool')
    finally:
        store.close()


def mcp():
    for line in sys.stdin:
        if len(line) > 1024 * 1024:
            raise ValueError('MCP request exceeds 1 MiB')
        request_id = None
        try:
            request = json.loads(line)
            request_id = request.get('id')
            method = request.get('method')
            if request_id is None:
                continue
            if method == 'initialize':
                offered = request.get('params', {}).get('protocolVersion', '2024-11-05')
                version = offered if offered in ('2024-11-05', '2025-03-26', '2025-06-18') else '2024-11-05'
                result = {'protocolVersion': version, 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'genta', 'version': '0.2.0'}}
            elif method == 'ping':
                result = {}
            elif method == 'tools/list':
                result = {'tools': [{'name': name, 'description': description, 'inputSchema': {'type': 'object', 'properties': properties, 'required': REQUIRED.get(name, []), 'additionalProperties': False}} for name, description, properties in TOOLS]}
            elif method == 'tools/call':
                params = request.get('params', {})
                name, arguments = params.get('name'), params.get('arguments', {})
                known = {entry[0] for entry in TOOLS}
                if name not in known or not isinstance(arguments, dict):
                    raise ValueError('Unknown tool or invalid arguments')
                allowed = next(properties for tool, description, properties in TOOLS if tool == name)
                if not arguments.keys() <= allowed.keys() or not all(key in arguments for key in REQUIRED.get(name, [])):
                    raise ValueError('Unexpected or missing tool arguments')
                try:
                    output = dispatch(name, arguments)
                    result = {'content': [{'type': 'text', 'text': json.dumps(output, ensure_ascii=False)}]}
                except (ValueError, KeyError, TypeError, OSError) as exc:
                    result = {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
            else:
                raise ValueError('Method not found')
            response = {'jsonrpc': '2.0', 'id': request_id, 'result': result}
        except (ValueError, KeyError, TypeError) as exc:
            response = {'jsonrpc': '2.0', 'id': request_id, 'error': {'code': -32602, 'message': str(exc)}}
        print(json.dumps(response, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['mcp', 'add', 'tasks', 'focus-start', 'focus-pause', 'focus-end'])
    parser.add_argument('text', nargs='?')
    parser.add_argument('--seconds', type=int, default=1500)
    args = parser.parse_args()
    if args.action == 'mcp':
        mcp()
    elif args.action == 'add':
        print(json.dumps(dispatch('task_add', {'text': args.text or '', 'day': date.today().isoformat()}), ensure_ascii=False))
    elif args.action == 'tasks':
        print(json.dumps(dispatch('tasks_list', {}), ensure_ascii=False))
    else:
        arguments = {'seconds': args.seconds} if args.action == 'focus-start' else {}
        print(json.dumps(focus_action(args.action.replace('-', '_'), **arguments)))


if __name__ == '__main__':
    main()
