# app/config.py

import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    ASSEMBLYAI_API_KEY = os.environ.get('ASSEMBLYAI_API_KEY')
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
    DEFAULT_API = os.environ.get('DEFAULT_TRANSCRIBE_API', 'gpt4o')
    DEFAULT_LANGUAGE = os.environ.get('DEFAULT_LANGUAGE', 'auto')
    # Supported language codes (comma‐separated in the env or defaults to en,nl,fr,es)
    SUPPORTED_LANGUAGE_CODES = os.environ.get('SUPPORTED_LANGUAGE_CODES', 'en,nl,fr,es').split(',')
    # Mapping for language names for the UI (the key "auto" is always included)
    SUPPORTED_LANGUAGE_NAMES = {
        'auto': 'Automatic Detection',
        'en': 'English',
        'nl': 'Dutch',
        'fr': 'French',
        'es': 'Spanish'
    }
    # Database file is stored in the database/ folder.
    DATABASE = os.path.join(os.getcwd(), 'database', 'transcriptions.db')
    # Directory for temporary uploads.
    TEMP_UPLOADS_DIR = os.path.join(os.getcwd(), 'uploads')
    # File deletion threshold in seconds (default: 24 hours)
    DELETE_THRESHOLD = 24 * 60 * 60