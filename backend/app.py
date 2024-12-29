import os
import sqlite3
import logging
from datetime import datetime
import uuid
import assemblyai as aai
from openai import OpenAI
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from werkzeug.exceptions import NotFound
from config import Config
from dotenv import load_dotenv
from pydub import AudioSegment
import time
import threading
from delete_old_files import delete_old_files, DELETE_THRESHOLD

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__, static_folder='app/static', template_folder='app/templates')
CORS(app)

# Load configuration
config = Config()
app.config.from_object(config)

# Database setup
DATABASE = app.config['DATABASE']

# Temporary uploads directory
TEMP_UPLOADS_DIR = 'temp_uploads'

# Maximum file size for OpenAI Whisper (25MB in bytes)
OPENAI_MAX_FILE_SIZE = 25 * 1024 * 1024

# Get default API and language from environment variables
DEFAULT_API = os.environ.get('DEFAULT_TRANSCRIBE_API', 'assemblyai')
DEFAULT_LANGUAGE = os.environ.get('DEFAULT_LANGUAGE', 'auto')

# Function to run the file cleanup task
def cleanup_task():
    while True:
        logging.info("Running cleanup task...")
        delete_old_files(TEMP_UPLOADS_DIR, DELETE_THRESHOLD)
        time.sleep(3600)  # Wait for 1 hour

# Start the cleanup task in a background thread
cleanup_thread = threading.Thread(target=cleanup_task)
cleanup_thread.daemon = True  # Allow the app to exit when this thread is running
cleanup_thread.start()

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS transcriptions (
            id TEXT PRIMARY KEY,
            filename TEXT,
            recording_date TEXT,
            detected_language TEXT,
            transcription_text TEXT,
            api_used TEXT,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Transcription API Handling
class BaseTranscriptionAPI:
    def __init__(self, api_key):
        self.api_key = api_key

    def transcribe(self, audio_file_path, language_code):
        raise NotImplementedError("Subclasses must implement the transcribe method")

class AssemblyAITranscriptionAPI(BaseTranscriptionAPI):
    def transcribe(self, audio_file_path, language_code):
        logging.info("Using AssemblyAI for transcription")
        aai.settings.api_key = self.api_key
        if language_code == 'auto':
            config = aai.TranscriptionConfig(
                language_detection=True
            )
        elif language_code in ['en', 'nl', 'fr', 'es']:
            config = aai.TranscriptionConfig(
                language_code=language_code
            )
        else:
            raise ValueError("Invalid language code for AssemblyAI")

        transcriber = aai.Transcriber(config=config)
        transcript = transcriber.transcribe(audio_file_path)

        if transcript.status == aai.TranscriptStatus.error:
            raise Exception(f"AssemblyAI transcription failed: {transcript.error}")

        detected_language = language_code
        if language_code == 'auto':
            try:
                detected_language = getattr(transcript, 'detected_language_code', None)
                if not detected_language:
                    detected_language = getattr(transcript, 'language_code', 'en')
            except AttributeError:
                detected_language = 'en'

        return transcript.text, detected_language

class OpenAITranscriptionAPI(BaseTranscriptionAPI):
    def transcribe(self, audio_file_path, language_code):
        logging.info("Using OpenAI Whisper for transcription")
        client = OpenAI(api_key=self.api_key)

        # Check if the file needs to be split
        if os.path.getsize(audio_file_path) > OPENAI_MAX_FILE_SIZE:
            logging.info("File size exceeds OpenAI limit. Splitting audio file.")
            transcription_text, detected_language = self.split_and_transcribe(audio_file_path, language_code)
        else:
            # Ensure the file path is absolute and within the mounted volume
            audio_file_path = os.path.abspath(audio_file_path)

            if not audio_file_path.startswith('/app/temp_uploads/'):
                raise ValueError("Audio file path is not within the mounted volume")

            audio_file = open(audio_file_path, "rb")

            if language_code == 'auto':
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                )
                detected_language = 'en'  # Default to English as Whisper doesn't return detected language
            elif language_code in ['en', 'nl', 'fr', 'es']:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language=language_code
                )
                detected_language = language_code
            else:
                raise ValueError("Invalid language code for OpenAI Whisper")
            transcription_text = transcript.text

        return transcription_text, detected_language

    def split_and_transcribe(self, audio_file_path, language_code):
        logging.info(f"Splitting audio file: {audio_file_path}")
        audio = AudioSegment.from_file(audio_file_path)
        total_length = len(audio)
        chunk_length = 10 * 60 * 1000  # 10 minutes in milliseconds
        chunks = []
        transcription_texts = []

        for i in range(0, total_length, chunk_length):
            chunk = audio[i:i + chunk_length]
            chunk_filename = os.path.join(TEMP_UPLOADS_DIR, f"{os.path.splitext(os.path.basename(audio_file_path))[0]}_chunk_{i // chunk_length}.mp3")
            chunk.export(chunk_filename, format="mp3")
            chunks.append(chunk_filename)
            logging.info(f"Created chunk: {chunk_filename}")

        client = OpenAI(api_key=self.api_key)
        for chunk_path in chunks:
            logging.info(f"Transcribing chunk: {chunk_path}")
            with open(chunk_path, "rb") as audio_file:
                if language_code == 'auto':
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                    )
                    detected_language = 'en'
                elif language_code in ['en', 'nl', 'fr', 'es']:
                    transcript = client.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language=language_code
                    )
                    detected_language = language_code
                else:
                    raise ValueError("Invalid language code for OpenAI Whisper")
                transcription_texts.append(transcript.text)

        # Clean up temporary chunk files
        for chunk_path in chunks:
            if os.path.exists(chunk_path):
                os.remove(chunk_path)

        return " ".join(transcription_texts), detected_language

def get_transcription_api(api_choice):
    if api_choice == 'assemblyai':
        return AssemblyAITranscriptionAPI(app.config['ASSEMBLYAI_API_KEY'])
    elif api_choice == 'openai':
        return OpenAITranscriptionAPI(app.config['OPENAI_API_KEY'])
    else:
        raise ValueError("Invalid API choice")

# Serve Frontend
@app.route('/')
def index():
    template_path = app.template_folder
    full_path = os.path.join(template_path, 'index.html')

    print("-" * 20)
    print("Attempting to serve index.html")
    print("Template folder:", template_path)
    print("Full path to index.html:", full_path)
    print("File exists:", os.path.exists(full_path))
    print("-" * 20)

    try:
        return render_template('index.html', default_api=DEFAULT_API, default_language=DEFAULT_LANGUAGE)
    except NotFound:
        print("Error: index.html not found in the specified directory.")
        return "Error: index.html not found", 404

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

# API Endpoints
@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    logging.info("Transcribe audio endpoint called")
    if 'audio_file' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    audio_file = request.files['audio_file']
    language_code = request.form.get('language_code', DEFAULT_LANGUAGE)
    api_choice = request.form.get('api_choice', DEFAULT_API)
    logging.info(f"Received language code: {language_code}, API choice: {api_choice}")

    transcription_id = str(uuid.uuid4())
    
    # Save the file to the temp_uploads directory
    temp_filename = os.path.join(TEMP_UPLOADS_DIR, f"{transcription_id}_{audio_file.filename}")
    audio_file.save(temp_filename)

    try:
        api = get_transcription_api(api_choice)
        transcription_text, detected_language = api.transcribe(temp_filename, language_code)

        conn = get_db_connection()
        conn.execute('''
            INSERT INTO transcriptions (id, filename, recording_date, detected_language, transcription_text, api_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (transcription_id, audio_file.filename, datetime.now().isoformat(), detected_language, transcription_text, api_choice, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        logging.info(f"Transcription successful using {api_choice}: {transcription_text[:100]}...")
        return jsonify({
            'id': transcription_id,
            'filename': audio_file.filename,
            'recording_date': datetime.now().isoformat(),
            'detected_language': detected_language,
            'transcription_text': transcription_text,
            'api_used': api_choice  # Include API used in the response
        })

    except Exception as e:
        logging.exception(f"An error occurred during transcription using {api_choice}")
        return jsonify({'error': str(e)}), 500
    finally:
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

@app.route('/api/transcriptions', methods=['GET'])
def get_transcriptions():
    logging.info("Get transcriptions endpoint called")
    conn = get_db_connection()
    transcriptions = conn.execute('SELECT * FROM transcriptions ORDER BY created_at DESC').fetchall()
    conn.close()
    return jsonify([dict(row) for row in transcriptions])

@app.route('/api/transcriptions/<transcription_id>', methods=['DELETE'])
def delete_transcription(transcription_id):
    logging.info(f"Delete transcription endpoint called for ID: {transcription_id}")
    conn = get_db_connection()
    conn.execute('DELETE FROM transcriptions WHERE id = ?', (transcription_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Transcription deleted successfully'})

@app.route('/api/transcriptions/clear', methods=['DELETE'])
def clear_transcriptions():
    logging.info("Clear transcriptions endpoint called")
    conn = get_db_connection()
    conn.execute('DELETE FROM transcriptions')
    conn.commit()
    conn.close()
    return jsonify({'message': 'All transcriptions cleared'})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)