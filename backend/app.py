#!/usr/bin/env python3
import os
import sqlite3
import logging
from datetime import datetime
import uuid
import time
import threading

import assemblyai as aai
from openai import OpenAI

from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from werkzeug.exceptions import NotFound
from werkzeug.utils import secure_filename
from config import Config
from dotenv import load_dotenv
from pydub import AudioSegment

from delete_old_files import delete_old_files, DELETE_THRESHOLD

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Allowed file extensions for audio uploads
ALLOWED_EXTENSIONS = {'mp3', 'm4a', 'wav', 'ogg', 'webm'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Initialize Flask with CORS
app = Flask(__name__, static_folder='app/static', template_folder='app/templates')
CORS(app)

# Load configuration
config = Config()
app.config.from_object(config)

# Database path and temporary upload directory
DATABASE = app.config['DATABASE']
TEMP_UPLOADS_DIR = 'temp_uploads'
OPENAI_MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

# Get default API and language from environment variables
DEFAULT_API = os.environ.get('DEFAULT_TRANSCRIBE_API', 'assemblyai')
DEFAULT_LANGUAGE = os.environ.get('DEFAULT_LANGUAGE', 'auto')

# Limit the number of concurrent jobs (for security and performance)
MAX_ACTIVE_JOBS = 10

###########################################################################
# Global “jobs” dictionary for asynchronous transcription progress tracking
# Each job holds a progress list, a finish flag, and a result.
###########################################################################
jobs = {}
jobs_lock = threading.Lock()

def append_progress(job_id, message):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['progress'].append(message)
    logging.info(message)

###########################################################################
# Background transcription worker function. It processes a transcription,
# updates job progress, and writes the final data to the database.
###########################################################################
def process_transcription(job_id, temp_filename, language_code, api_choice, original_filename):
    try:
        append_progress(job_id, "Transcription started.")
        append_progress(job_id, f"Received language code: {language_code}, API choice: {api_choice}")
        
        # Get the desired transcription API and set up a progress callback.
        api = get_transcription_api(api_choice)
        progress_callback = lambda msg: append_progress(job_id, msg)
        
        # Perform the transcription (this may run for several minutes)
        transcription_text, detected_language = api.transcribe(temp_filename, language_code, progress_callback=progress_callback)
        
        # Save result in the database
        conn = get_db_connection()
        recording_date = datetime.now().isoformat()
        conn.execute('''
            INSERT INTO transcriptions (id, filename, recording_date, detected_language, transcription_text, api_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (job_id, original_filename, recording_date, detected_language, transcription_text, api_choice, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        append_progress(job_id, "Transcription successful.")
        result = {
            'id': job_id,
            'filename': original_filename,
            'recording_date': recording_date,
            'detected_language': detected_language,
            'transcription_text': transcription_text,
            'api_used': api_choice
        }
        with jobs_lock:
            jobs[job_id]['result'] = result
    except Exception as e:
        append_progress(job_id, f"An error occurred: {str(e)}")
        with jobs_lock:
            jobs[job_id]['result'] = {'error': str(e)}
    finally:
        with jobs_lock:
            jobs[job_id]['finished'] = True
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

###########################################################################
# Database Helper Functions
###########################################################################
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

###########################################################################
# Transcription API Classes with Progress Reporting
###########################################################################
class BaseTranscriptionAPI:
    def __init__(self, api_key):
        self.api_key = api_key
        logging.info(f"Initialized {self.__class__.__name__} with API key: {api_key}")

    def transcribe(self, audio_file_path, language_code, progress_callback=None):
        logging.info(f"Starting transcription with {self.__class__.__name__} for file: {audio_file_path} and language code: {language_code}")
        raise NotImplementedError("Subclasses must implement the transcribe method")

class AssemblyAITranscriptionAPI(BaseTranscriptionAPI):
    def transcribe(self, audio_file_path, language_code, progress_callback=None):
        if progress_callback:
            progress_callback(f"Using AssemblyAI for transcription of {audio_file_path} with language code {language_code}")
        else:
            logging.info(f"Using AssemblyAI for transcription of {audio_file_path} with language code {language_code}")
        aai.settings.api_key = self.api_key
        if language_code == 'auto':
            config_obj = aai.TranscriptionConfig(language_detection=True)
        elif language_code in ['en', 'nl', 'fr', 'es']:
            config_obj = aai.TranscriptionConfig(language_code=language_code)
        else:
            if progress_callback:
                progress_callback(f"Invalid language code for AssemblyAI: {language_code}")
            logging.error(f"Invalid language code for AssemblyAI: {language_code}")
            raise ValueError("Invalid language code for AssemblyAI")
        transcriber = aai.Transcriber(config=config_obj)
        transcript = transcriber.transcribe(audio_file_path)
        if transcript.status == aai.TranscriptStatus.error:
            if progress_callback:
                progress_callback(f"AssemblyAI transcription failed: {transcript.error}")
            logging.error(f"AssemblyAI transcription failed: {transcript.error}")
            raise Exception(f"AssemblyAI transcription failed: {transcript.error}")
        detected_language = language_code
        if language_code == 'auto':
            try:
                detected_language = getattr(transcript, 'detected_language_code', None) or getattr(transcript, 'language_code', 'en')
            except AttributeError:
                detected_language = 'en'
        if progress_callback:
            progress_callback(f"AssemblyAI detected language: {detected_language}")
        return transcript.text, detected_language

class OpenAITranscriptionAPI(BaseTranscriptionAPI):
    def transcribe(self, audio_file_path, language_code, progress_callback=None):
        if progress_callback:
            progress_callback(f"Using OpenAI Whisper for transcription of {audio_file_path} with language code {language_code}")
        else:
            logging.info(f"Using OpenAI Whisper for transcription of {audio_file_path} with language code {language_code}")
        client = OpenAI(api_key=self.api_key)
        if os.path.getsize(audio_file_path) > OPENAI_MAX_FILE_SIZE:
            if progress_callback:
                progress_callback("File size exceeds OpenAI limit. Splitting audio file.")
            else:
                logging.info("File size exceeds OpenAI limit. Splitting audio file.")
            return self.split_and_transcribe(audio_file_path, language_code, progress_callback)
        else:
            audio_file_path = os.path.abspath(audio_file_path)
            # Ensure file is within TEMP_UPLOADS_DIR; adjust check per your deployment
            if not audio_file_path.startswith(os.path.join(os.getcwd(), TEMP_UPLOADS_DIR)):
                logging.error("Audio file path is not within the mounted volume")
                raise ValueError("Audio file path is not within the mounted volume")
            with open(audio_file_path, "rb") as audio_file:
                if language_code == 'auto':
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
                    detected_language = 'en'
                elif language_code in ['en', 'nl', 'fr', 'es']:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file, language=language_code)
                    detected_language = language_code
                else:
                    if progress_callback:
                        progress_callback(f"Invalid language code for OpenAI Whisper: {language_code}")
                    logging.error(f"Invalid language code for OpenAI Whisper: {language_code}")
                    raise ValueError("Invalid language code for OpenAI Whisper")
                transcription_text = transcript.text
                if progress_callback:
                    progress_callback(f"OpenAI detected language: {detected_language}")
                return transcription_text, detected_language

    def split_and_transcribe(self, audio_file_path, language_code, progress_callback=None):
        if progress_callback:
            progress_callback(f"Splitting audio file: {audio_file_path}")
        else:
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
            if progress_callback:
                progress_callback(f"Created chunk: {chunk_filename}")
            else:
                logging.info(f"Created chunk: {chunk_filename}")
        client = OpenAI(api_key=self.api_key)
        total_chunks = len(chunks)
        for idx, chunk_path in enumerate(chunks):
            if progress_callback:
                progress_callback(f"Transcribing chunk {idx+1} of {total_chunks}: {chunk_path}")
            else:
                logging.info(f"Transcribing chunk: {chunk_path}")
            with open(chunk_path, "rb") as audio_file:
                if language_code == 'auto':
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
                    detected_language = 'en'
                elif language_code in ['en', 'nl', 'fr', 'es']:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file, language=language_code)
                    detected_language = language_code
                else:
                    if progress_callback:
                        progress_callback(f"Invalid language code for OpenAI Whisper: {language_code}")
                    logging.error(f"Invalid language code for OpenAI Whisper: {language_code}")
                    raise ValueError("Invalid language code for OpenAI Whisper")
                transcription_texts.append(transcript.text)
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
        logging.error(f"Invalid API choice: {api_choice}")
        raise ValueError("Invalid API choice")

###########################################################################
# Serve the Frontend
###########################################################################
@app.route('/')
def index():
    template_path = app.template_folder
    full_path = os.path.join(template_path, 'index.html')
    logging.info("-" * 20)
    logging.info("Attempting to serve index.html")
    logging.info(f"Template folder: {template_path}")
    logging.info(f"Full path to index.html: {full_path}")
    logging.info(f"File exists: {os.path.exists(full_path)}")
    logging.info("-" * 20)
    try:
        return render_template('index.html', default_api=DEFAULT_API, default_language=DEFAULT_LANGUAGE)
    except NotFound:
        logging.error("Error: index.html not found in the specified directory.")
        return "Error: index.html not found", 404

@app.route('/path/<path:path>')
def serve_static(path):
    return send_from_directory(app.static_folder, path)

###########################################################################
# API Endpoints
###########################################################################
@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    logging.info("Transcribe audio endpoint called")
    if 'audio_file' not in request.files:
        logging.error("No audio file provided in the request")
        return jsonify({'error': 'No audio file provided'}), 400

    file = request.files['audio_file']
    if file.filename == '':
        logging.error("No selected file")
        return jsonify({'error': 'No selected file'}), 400

    if not allowed_file(file.filename):
        logging.error("File extension not allowed")
        return jsonify({'error': 'File type not allowed'}), 400

    # Enforce maximum number of active jobs to protect resources
    with jobs_lock:
        active_jobs = sum(1 for job in jobs.values() if not job.get('finished', False))
        if active_jobs >= MAX_ACTIVE_JOBS:
            return jsonify({'error': 'Too many concurrent transcription jobs. Please try again later.'}), 429

    original_filename = secure_filename(file.filename)
    job_id = str(uuid.uuid4())
    temp_filename = os.path.join(TEMP_UPLOADS_DIR, f"{job_id}_{original_filename}")
    file.save(temp_filename)

    with jobs_lock:
        jobs[job_id] = {'progress': [], 'finished': False, 'result': None}

    # Run transcription in a background thread.
    thread = threading.Thread(target=process_transcription, args=(job_id, temp_filename, request.form.get('language_code', DEFAULT_LANGUAGE), request.form.get('api_choice', DEFAULT_API), original_filename))
    thread.start()

    return jsonify({'job_id': job_id, 'message': 'Transcription started'})

@app.route('/api/progress/<job_id>', methods=['GET'])
def get_progress(job_id):
    with jobs_lock:
        if job_id not in jobs:
            return jsonify({'error': 'Job not found'}), 404
        job_info = jobs[job_id].copy()
    return jsonify(job_info)

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

###########################################################################
# Background Cleanup Task to Delete Old Files
###########################################################################
def cleanup_task():
    while True:
        logging.info("Running cleanup task...")
        delete_old_files(TEMP_UPLOADS_DIR, DELETE_THRESHOLD)
        time.sleep(21600)  # Every 6 hours

cleanup_thread = threading.Thread(target=cleanup_task)
cleanup_thread.daemon = True
cleanup_thread.start()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)