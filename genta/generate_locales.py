"""Build validated shared UI catalogs from static source strings only.

Preserves valid existing Hermes-generated translations. Missing strings use
small numbered batches through Google's public text translation endpoint.
No planner data, account credentials or paid image credits are used.
"""
import time
import ast
import json
from pathlib import Path
import re

import requests

from i18n import LANGUAGES

ROOT = Path(__file__).resolve().parent
CALLS = {'label', 'button', 'QLabel', 'QPushButton', 'QCheckBox', 'setPlaceholderText', 'setToolTip', 'setWindowTitle', 'setText', 'addAction', 'addItems', 'addItem', 'tr'}
EXTRA = {'Today','Week','Focus','Notes','Calendar','Music','Clips','Insights','Hermes','Settings','Inbox','All tasks','Today + overdue','All projects','Countdown','Stopwatch','Silence','Brown noise','Pink noise','Rain','Ocean','TASKS DONE TODAY','FOCUS MINUTES TODAY','DAY COMPLETION STREAK','Priority','High priority','Normal priority','IN THE FLOW','YOUR TIME, PROTECTED','OK','Cancel','Save','Delete','Add','Task','New task','Edit task','Day YYYY-MM-DD; blank for Inbox','Reminder YYYY-MM-DDTHH:MM; blank for none','Deadline YYYY-MM-DD; blank for none','Project','Estimated minutes','Start / pause','Pair','Language','Pair this device','Task created. Original note kept.','No repeat','Daily','Weekdays','Weekly','Monthly','Enable','Deadline','Repeat','Next','Previous','Pause','Play','Open SimpMusic','Speak a task','Sync now'}


def strings():
    values = set(EXTRA)
    for name in ['genta.py','workspace_panels.py','desktop_services.py','music_widget.py','kanban.py']:
        tree = ast.parse((ROOT / name).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id if isinstance(node.func, ast.Name) else ''
            if function not in CALLS or not node.args:
                continue
            first = node.args[0]
            candidates = first.elts if isinstance(first, (ast.List, ast.Tuple)) else [first]
            for value in candidates:
                if isinstance(value, ast.Constant) and isinstance(value.value, str) and value.value.strip():
                    values.add(value.value)
    for filename in ['MainActivity.java','Widget.java']:
        java = (ROOT / 'android/src/ai/genta/app' / filename).read_text()
        for match in re.finditer(r'(?:text|button|input|tr|message)\("((?:[^"\\]|\\.)*)"', java):
            try:
                values.add(json.loads('"' + match.group(1) + '"'))
            except ValueError:
                continue
    return sorted(value for value in values if len(value) <= 1000 and not value.startswith(('http:', 'https:', 'file:', 'genta:', '["')))


def main():
    folder = ROOT / 'locales'
    folder.mkdir(exist_ok=True)
    source = strings()
    english = {value: value for value in source}
    (folder / 'en.json').write_text(json.dumps(english, ensure_ascii=False, indent=2), encoding='utf-8')
    (folder / 'languages.json').write_text(json.dumps(LANGUAGES, ensure_ascii=False, indent=2), encoding='utf-8')
    glossary = re.compile(r'\{[^{}]+\}|Genta|Hermes|SimpMusic|Google|Outlook|Calendly|Cal\.com|Claude|Cursor|Android|Windows|Linux|HTTPS|JSON|MCP|API')
    session = requests.Session()
    failures = []
    for code, language in LANGUAGES.items():
        if code == 'en':
            continue
        target = folder / (code + '.json')
        existing = json.loads(target.read_text()) if target.exists() else {}
        translations = {key: value for key, value in existing.items() if key in english and isinstance(value, str) and value}
        missing = [key for key in source if key not in translations]
        try:
            for offset in range(0, len(missing), 20):
                batch = missing[offset:offset+20]
                lines, replacements = [], {}
                for index, key in enumerate(batch):
                    tokens = {}
                    def protect(match):
                        token = f'ZXQ{len(tokens)}QXZ'
                        tokens[token] = match.group(0)
                        return token
                    text = glossary.sub(protect, key).replace('\n', ' ')
                    lines.append(f'{index:03d}\t{text}')
                    replacements[index] = tokens
                response = session.get('https://translate.googleapis.com/translate_a/single', params={'client': 'gtx', 'sl': 'en', 'tl': 'zh-CN' if code == 'zh' else code, 'dt': 't', 'q': '\n'.join(lines)}, timeout=(5, 30))
                response.raise_for_status()
                result = ''.join(part[0] for part in response.json()[0] if part and part[0])
                parsed, current = {}, None
                for line in result.splitlines():
                    prefix = re.match(r'^\s*(\d{3})', line)
                    markers = [prefix] if prefix and int(prefix.group(1)) < len(batch) else [match for match in re.finditer(r'(?<!\d)(\d{3})(?!\d)', line) if int(match.group(1)) < len(batch)]
                    if len(markers) == 1:
                        match = markers[0]
                        current = int(match.group(1))
                        if current in parsed:
                            raise ValueError('Duplicate translation index')
                        parsed[current] = (line[:match.start()] + ' ' + line[match.end():]).strip()
                    elif current is not None:
                        parsed[current] += ' ' + line.strip()
                if set(parsed) != set(range(len(batch))):
                    raise ValueError('Translation batch lost its numbered boundaries')
                for index, key in enumerate(batch):
                    value = parsed[index]
                    for token, original in replacements[index].items():
                        value = re.sub(re.escape(token), lambda match, text=original: text, value, flags=re.IGNORECASE)
                    if not value or set(re.findall(r'\{[^{}]+\}', key)) != set(re.findall(r'\{[^{}]+\}', value)):
                        raise ValueError('A translation changed a named placeholder')
                    translations[key] = value
                # Checkpoint real translations; a later failed batch does not lose them.
                temporary = target.with_suffix('.json.new')
                temporary.write_text(json.dumps(translations, ensure_ascii=False, indent=2), encoding='utf-8')
                temporary.replace(target)
                time.sleep(.25)
            if set(translations) != set(english):
                raise ValueError('Catalog is incomplete')
            target.write_text(json.dumps(translations, ensure_ascii=False, indent=2), encoding='utf-8')
            print(f'{code}: {len(translations)} validated UI strings', flush=True)
        except Exception as error:
            failures.append(code)
            print(f'{code}: FAILED {type(error).__name__}: {error}', flush=True)
    if failures:
        raise SystemExit('Incomplete catalogs: ' + ', '.join(failures))


if __name__ == '__main__':
    main()
