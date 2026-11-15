"""Shared UTF-8 catalogs for desktop and Android; English is the source language."""
import json
from pathlib import Path
from PySide6.QtCore import QLocale

LANGUAGES = {
 'en':'English','ms':'Bahasa Melayu','id':'Bahasa Indonesia','de':'Deutsch','fr':'Français',
 'es':'Español','pt':'Português','it':'Italiano','nl':'Nederlands','sv':'Svenska',
 'da':'Dansk','no':'Norsk','fi':'Suomi','pl':'Polski','cs':'Čeština',
 'tr':'Türkçe','ru':'Русский','uk':'Українська','ar':'العربية','hi':'हिन्दी',
 'th':'ไทย','vi':'Tiếng Việt','ja':'日本語','ko':'한국어','zh':'简体中文',
}
_catalog = {}
_language = 'en'


def set_language(code):
    global _catalog, _language
    if code not in LANGUAGES:
        raise ValueError('Unsupported interface language')
    path = Path(__file__).resolve().parent / 'locales' / (code + '.json')
    if code != 'en' and not path.is_file():
        raise ValueError('The selected language catalog is not installed')
    _catalog = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    _language = code
    QLocale.setDefault(QLocale(code))
    from PySide6.QtWidgets import QApplication
    if QApplication.instance():
        QApplication.instance().setLayoutDirection(QLocale(code).textDirection())


def tr(source):
    return _catalog.get(source, source)
