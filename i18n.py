"""Presentation translations. Locale is fixed for a process; changes apply on restart.

Internal role IDs, queue states, filenames and user text are not translation keys.
"""
import app_settings
from translations_en import EN
LANGUAGE=app_settings.load().get('language','zh_CN')

def tr(text):
    return EN.get(text,text) if LANGUAGE=='en' and isinstance(text,str) else text

def labels(values):
    return [tr(value) for value in values]
