# app/config.py

import os
from dotenv import load_dotenv
load_dotenv()

class Config:
    ASSEMBLYAI_API_KEY = os.environ.get('ASSEMBLYAI_API_KEY')
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
    DEFAULT_API = os.environ.get('DEFAULT_TRANSCRIBE_API', 'gpt4o')
    # Gemini / Vertex settings
    GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')
    # Provider can be 'google' (aka Gemini API) or 'vertex'
    GEMINI_PROVIDER = os.environ.get('GEMINI_PROVIDER', 'google')
    VERTEXAI_PROJECT_ID = os.environ.get('VERTEXAI_PROJECT_ID')
    VERTEXAI_LOCATION = os.environ.get('VERTEXAI_LOCATION')
    GEMINI_MODEL_NAME = os.environ.get('GEMINI_MODEL_NAME', 'gemini-2.5-pro')
    # Default language for transcription (e.g. 'en' or 'auto' for auto-detect)  
    DEFAULT_LANGUAGE = os.environ.get('DEFAULT_LANGUAGE', 'auto')
    # Supported language codes (comma‐separated in the env or defaults to en,nl,fr,es)
    SUPPORTED_LANGUAGE_CODES = os.environ.get('SUPPORTED_LANGUAGE_CODES', 'en,nl,fr,es,ru').split(',')
    # Mapping for language names for the UI (the key "auto" is always included)
    SUPPORTED_LANGUAGE_NAMES = {
        'auto': 'Automatic Detection',
        'en': 'English',
        'nl': 'Dutch',
        'fr': 'French',
        'es': 'Spanish',
        'ru': 'Russian'
    }
    # Database file is stored in the database/ folder.
    DATABASE = os.path.join(os.getcwd(), 'database', 'transcriptions.db')
    # Directory for temporary uploads.
    TEMP_UPLOADS_DIR = os.path.join(os.getcwd(), 'uploads')
    # File deletion threshold in seconds (default: 24 hours)
    DELETE_THRESHOLD = 24 * 60 * 60
    # Max concurrent chunk transcriptions for OpenAI calls
    OPENAI_MAX_CONCURRENCY = int(os.environ.get('OPENAI_MAX_CONCURRENCY', '4'))
    # Max concurrency for Gemini (defaults to same as OpenAI if not set)
    GEMINI_MAX_CONCURRENCY = int(os.environ.get('GEMINI_MAX_CONCURRENCY', OPENAI_MAX_CONCURRENCY if 'OPENAI_MAX_CONCURRENCY' in os.environ else '3'))



    # --- Realtime configuration ---
    # Model IDs used when the websocket proxy opens upstream realtime sessions.
    REALTIME_MODEL_TRANSCRIBE = os.environ.get('REALTIME_MODEL_TRANSCRIBE', 'gpt-4o-transcribe')
    REALTIME_MODEL_TRANSLATE = os.environ.get('REALTIME_MODEL_TRANSLATE', REALTIME_MODEL_TRANSCRIBE)
    REALTIME_MODEL_TTS = os.environ.get('REALTIME_MODEL_TTS', 'gpt-4o-mini-tts')
    REALTIME_DEFAULT_VOICE = os.environ.get('REALTIME_DEFAULT_VOICE', 'verse')
    REALTIME_ALLOWED_ORIGINS = [origin.strip() for origin in os.environ.get('REALTIME_ALLOWED_ORIGINS', '*').split(',') if origin.strip()]  # Origin allowlist for websocket clients
    REALTIME_ENABLE_TRANSLATION = os.environ.get('REALTIME_ENABLE_TRANSLATION', '1')  # Toggle UI/server translation support
    REALTIME_ENABLE_TTS = os.environ.get('REALTIME_ENABLE_TTS', '0')  # Toggle synthesized playback support
    REALTIME_SAMPLE_RATE = int(os.environ.get('REALTIME_SAMPLE_RATE', '16000'))  # Expected PCM sample rate from clients
    REALTIME_CHUNK_MILLIS = int(os.environ.get('REALTIME_CHUNK_MILLIS', '250'))  # How often clients flush audio buffers
    REALTIME_SESSION_TTL_SECONDS = int(os.environ.get('REALTIME_SESSION_TTL_SECONDS', '900'))  # Hard cap on session lifetime
    REALTIME_SESSION_MAX_IDLE_SECONDS = int(os.environ.get('REALTIME_SESSION_MAX_IDLE_SECONDS', '300'))  # Idle timeout before cleanup
    REALTIME_MAX_SERVER_LATENCY_MS = int(os.environ.get('REALTIME_MAX_SERVER_LATENCY_MS', '1500'))  # Client hint for acceptable roundtrip

