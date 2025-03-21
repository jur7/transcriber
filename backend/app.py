#!/usr/bin/env python3
"""
Main transcription application.
Implements file uploads, background processing, and API endpoints.
"""

import os
import sqlite3
import logging
from datetime import datetime
import uuid
import time
import threading
from functools import wraps
from typing import Any, Callable, Optional, Tuple

import assemblyai as aai
from openai import OpenAI

from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
from werkzeug.exceptions import NotFound
from werkzeug.utils import secure_filename
from config import Config
from dotenv import load_dotenv

from pydub import AudioSegment
from audio_utils import split_audio_file, remove_files, validate_file_path, CHUNK_LENGTH_MS
from delete_old_files import delete_old_files, DELETE_THRESHOLD

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Allowed file extensions for audio uploads (defined as a constant)
ALLOWED_EXTENSIONS = {'mp3', 'm4a', 'wav', 'ogg', 'webm'}


def allowed_file(filename: str) -> bool:
    """
    Checks if a file has an allowed extension.

    Args:
        filename: Name of the file

    Returns:
        True if allowed, False otherwise.
    """
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# Initialize Flask with CORS
app = Flask(__name__, static_folder='app/static', template_folder='app/templates')
CORS(app)

# Load configuration
config = Config()
app.config.from_object(config)

# Database path and temporary upload directory
DATABASE: str = app.config['DATABASE']
TEMP_UPLOADS_DIR: str = 'temp_uploads'
OPENAI_MAX_FILE_SIZE: int = 25 * 1024 * 1024  # 25MB

# Get default API and language from environment variables.
# Updated default to use GPT 4o ("gpt4o") by default.
DEFAULT_API: str = os.environ.get('DEFAULT_TRANSCRIBE_API', 'gpt4o')
DEFAULT_LANGUAGE: str = os.environ.get('DEFAULT_LANGUAGE', 'auto')

# Limit the number of concurrent jobs (for security and performance)
MAX_ACTIVE_JOBS: int = 10

###########################################################################
# Global jobs dictionary for asynchronous transcription progress tracking.
# Each job holds a progress list, a finished flag, and a result.
###########################################################################
jobs: dict[str, Any] = {}
jobs_lock = threading.Lock()


def append_progress(job_id: str, message: str) -> None:
    """
    Appends a progress message for a given job and logs the message.

    Args:
        job_id: The unique job identifier.
        message: The progress message.
    """
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['progress'].append(message)
    logging.info(message)


def catch_exceptions(func: Callable) -> Callable:
    """
    Decorator to catch and log exceptions in background tasks.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.exception("Exception in %s: %s", func.__name__, e)
            raise
    return wrapper


@catch_exceptions
def process_transcription(job_id: str, temp_filename: str, language_code: str, api_choice: str, original_filename: str) -> None:
    """
    Background worker that processes a transcription job.

    Args:
        job_id: Unique job identifier.
        temp_filename: Path to the temporary audio file.
        language_code: Language code requested.
        api_choice: Chosen transcription API.
        original_filename: Original filename uploaded by the user.
    """
    try:
        append_progress(job_id, "Transcription started.")
        append_progress(job_id, f"Received language code: {language_code}, API choice: {api_choice}")

        # Get the desired transcription API and define the progress callback.
        api = get_transcription_api(api_choice)
        progress_callback = lambda msg: append_progress(job_id, msg)

        # Perform transcription (this may run for several minutes).
        transcription_text, detected_language = api.transcribe(temp_filename, language_code, progress_callback=progress_callback)

        # Save result in the database.
        conn = get_db_connection()
        recording_date: str = datetime.now().isoformat()
        conn.execute(
            '''
            INSERT INTO transcriptions (id, filename, recording_date, detected_language, transcription_text, api_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (job_id, original_filename, recording_date, detected_language, transcription_text, api_choice, datetime.now().isoformat())
        )
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
# Database helper functions
###########################################################################
def get_db_connection() -> sqlite3.Connection:
    """
    Obtains a connection to the SQLite database.

    Returns:
        SQLite connection with row_factory set to sqlite3.Row.
    """
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    Initializes the database by creating the necessary tables if they don't exist.
    """
    conn = get_db_connection()
    conn.execute(
        '''
        CREATE TABLE IF NOT EXISTS transcriptions (
            id TEXT PRIMARY KEY,
            filename TEXT,
            recording_date TEXT,
            detected_language TEXT,
            transcription_text TEXT,
            api_used TEXT,
            created_at TEXT
        )
        '''
    )
    conn.commit()
    conn.close()


init_db()

###########################################################################
# Transcription API Classes with Progress Reporting
###########################################################################
class BaseTranscriptionAPI:
    """
    Abstract base class for transcription APIs.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key: str = api_key
        logging.info(f"Initialized {self.__class__.__name__} with API key: {api_key}")

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        """
        Perform a transcription. Must be overridden in subclasses.

        Args:
            audio_file_path: Path to the audio file.
            language_code: Language code to use.
            progress_callback: Optional callback for progress updates.

        Returns:
            A tuple (transcription_text, detected_language).
        """
        logging.info(f"Starting transcription with {self.__class__.__name__} for file: {audio_file_path} and language code: {language_code}")
        raise NotImplementedError("Subclasses must implement the transcribe method")


class AssemblyAITranscriptionAPI(BaseTranscriptionAPI):
    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        """
        Transcribes an audio file using AssemblyAI.
        """
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
            message = f"Invalid language code for AssemblyAI: {language_code}"
            if progress_callback:
                progress_callback(message)
            logging.error(message)
            raise ValueError(message)
        transcriber = aai.Transcriber(config=config_obj)
        transcript = transcriber.transcribe(audio_file_path)
        if transcript.status == aai.TranscriptStatus.error:
            message = f"AssemblyAI transcription failed: {transcript.error}"
            if progress_callback:
                progress_callback(message)
            logging.error(message)
            raise Exception(message)
        detected_language: str = language_code
        if language_code == 'auto':
            try:
                detected_language = getattr(transcript, 'detected_language_code', None) or getattr(transcript, 'language_code', 'en')
            except AttributeError:
                detected_language = 'en'
        if progress_callback:
            progress_callback(f"AssemblyAI detected language: {detected_language}")
        return transcript.text, detected_language


class OpenAITranscriptionAPI(BaseTranscriptionAPI):
    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        """
        Transcribes an audio file using OpenAI Whisper.
        """
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
            abs_path: str = os.path.abspath(audio_file_path)
            if not validate_file_path(abs_path, os.path.join(os.getcwd(), TEMP_UPLOADS_DIR)):
                message = "Audio file path is not within the mounted volume"
                logging.error(message)
                raise ValueError(message)
            with open(abs_path, "rb") as audio_file:
                if language_code == 'auto':
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
                    detected_language: str = 'en'
                elif language_code in ['en', 'nl', 'fr', 'es']:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file, language=language_code)
                    detected_language = language_code
                else:
                    message = f"Invalid language code for OpenAI Whisper: {language_code}"
                    if progress_callback:
                        progress_callback(message)
                    logging.error(message)
                    raise ValueError(message)
                transcription_text: str = transcript.text
                if progress_callback:
                    progress_callback(f"OpenAI detected language: {detected_language}")
                return transcription_text, detected_language

    def split_and_transcribe(self, audio_file_path: str, language_code: str,
                             progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        """
        Splits and transcribes an oversized audio file using OpenAI Whisper.
        """
        if progress_callback:
            progress_callback(f"Splitting audio file: {audio_file_path}")
        else:
            logging.info(f"Splitting audio file: {audio_file_path}")
        # Use the shared helper to split file.
        chunk_files = split_audio_file(audio_file_path, TEMP_UPLOADS_DIR, progress_callback, CHUNK_LENGTH_MS)
        client = OpenAI(api_key=self.api_key)
        transcription_texts: list[str] = []
        detected_language: str = 'en'
        total_chunks: int = len(chunk_files)
        for idx, chunk_path in enumerate(chunk_files):
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
                    message = f"Invalid language code for OpenAI Whisper: {language_code}"
                    if progress_callback:
                        progress_callback(message)
                    logging.error(message)
                    raise ValueError(message)
                transcription_texts.append(transcript.text)
        remove_files(chunk_files)
        return " ".join(transcription_texts), detected_language


class OpenAIGPT4oTranscriptionAPI(BaseTranscriptionAPI):
    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        """
        Transcribes an audio file using OpenAI GPT 4o Transcribe API.
        This endpoint returns plain text.
        """
        if progress_callback:
            progress_callback(f"Using OpenAI GPT 4o Transcribe for {audio_file_path}")
        else:
            logging.info(f"Using OpenAI GPT 4o Transcribe for {audio_file_path}")
        client = OpenAI(api_key=self.api_key)
        if os.path.getsize(audio_file_path) > OPENAI_MAX_FILE_SIZE:
            if progress_callback:
                progress_callback("File size exceeds OpenAI limit. Splitting audio file for GPT 4o Transcription.")
            else:
                logging.info("File size exceeds OpenAI limit. Splitting audio file for GPT 4o Transcription.")
            return self.split_and_transcribe(audio_file_path, language_code, progress_callback)
        else:
            abs_path: str = os.path.abspath(audio_file_path)
            if not validate_file_path(abs_path, os.path.join(os.getcwd(), TEMP_UPLOADS_DIR)):
                message = "Audio file path is not within the mounted volume"
                logging.error(message)
                raise ValueError(message)
            with open(abs_path, "rb") as audio_file:
                # Call the GPT 4o Transcribe endpoint with response_format="text"
                transcript = client.audio.transcriptions.create(model="gpt-4o-transcribe", file=audio_file, response_format="text")
                # Use transcript directly because it is expected to be a plain text string.
                transcription_text: str = transcript if isinstance(transcript, str) else transcript.text
                detected_language: str = language_code if language_code != 'auto' else 'en'
                if progress_callback:
                    progress_callback(f"GPT 4o Transcription completed. Detected/assumed language: {detected_language}")
                return transcription_text, detected_language

    def split_and_transcribe(self, audio_file_path: str, language_code: str,
                             progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        """
        Splits and transcribes an oversized audio file using GPT 4o.
        """
        if progress_callback:
            progress_callback(f"Splitting audio file: {audio_file_path}")
        else:
            logging.info(f"Splitting audio file: {audio_file_path}")
        # Use helper function to split the audio
        chunk_files = split_audio_file(audio_file_path, TEMP_UPLOADS_DIR, progress_callback, CHUNK_LENGTH_MS)
        client = OpenAI(api_key=self.api_key)
        transcription_texts: list[str] = []
        total_chunks: int = len(chunk_files)
        for idx, chunk_path in enumerate(chunk_files):
            if progress_callback:
                progress_callback(f"Transcribing chunk {idx+1} of {total_chunks}: {chunk_path}")
            else:
                logging.info(f"Transcribing chunk: {chunk_path}")
            with open(chunk_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(model="gpt-4o-transcribe", file=audio_file, response_format="text")
                # Use transcript directly since it is a plain text string.
                transcription_texts.append(transcript if isinstance(transcript, str) else transcript.text)
        remove_files(chunk_files)
        detected_language: str = language_code if language_code != 'auto' else 'en'
        return " ".join(transcription_texts), detected_language


def get_transcription_api(api_choice: str) -> BaseTranscriptionAPI:
    """
    Factory function to return the appropriate transcription API instance.

    Args:
        api_choice: The API choice string ('assemblyai', 'openai', or 'gpt4o').

    Returns:
        An instance of a subclass of BaseTranscriptionAPI.
    """
    if api_choice == 'assemblyai':
        return AssemblyAITranscriptionAPI(app.config['ASSEMBLYAI_API_KEY'])
    elif api_choice == 'openai':
        return OpenAITranscriptionAPI(app.config['OPENAI_API_KEY'])
    elif api_choice == 'gpt4o':
        return OpenAIGPT4oTranscriptionAPI(app.config['OPENAI_API_KEY'])
    else:
        message = f"Invalid API choice: {api_choice}"
        logging.error(message)
        raise ValueError(message)


###########################################################################
# API Endpoints and Frontend Serving
###########################################################################
@app.route('/')
def index() -> Any:
    """
    Serves the main frontend page.
    """
    template_path: str = app.template_folder
    full_path: str = os.path.join(template_path, 'index.html')
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
def serve_static(path: str) -> Any:
    """
    Serves static files.
    """
    return send_from_directory(app.static_folder, path)


@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio() -> Any:
    """
    API endpoint to start an audio transcription.
    """
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

    # Enforce maximum number of active jobs to protect resources.
    with jobs_lock:
        active_jobs: int = sum(1 for job in jobs.values() if not job.get('finished', False))
        if active_jobs >= MAX_ACTIVE_JOBS:
            return jsonify({'error': 'Too many concurrent transcription jobs. Please try again later.'}), 429

    original_filename: str = secure_filename(file.filename)
    job_id: str = str(uuid.uuid4())
    temp_filename: str = os.path.join(TEMP_UPLOADS_DIR, f"{job_id}_{original_filename}")
    file.save(temp_filename)

    with jobs_lock:
        jobs[job_id] = {'progress': [], 'finished': False, 'result': None}

    # Run transcription in a background thread.
    thread = threading.Thread(
        target=process_transcription,
        args=(job_id, temp_filename, request.form.get('language_code', DEFAULT_LANGUAGE),
              request.form.get('api_choice', DEFAULT_API), original_filename)
    )
    thread.start()

    return jsonify({'job_id': job_id, 'message': 'Transcription started'})


@app.route('/api/progress/<job_id>', methods=['GET'])
def get_progress(job_id: str) -> Any:
    """
    API endpoint to fetch the progress of a transcription job.
    """
    with jobs_lock:
        if job_id not in jobs:
            return jsonify({'error': 'Job not found'}), 404
        job_info = jobs[job_id].copy()
    return jsonify(job_info)


@app.route('/api/transcriptions', methods=['GET'])
def get_transcriptions() -> Any:
    """
    API endpoint to fetch all transcriptions from the database.
    """
    logging.info("Get transcriptions endpoint called")
    conn = get_db_connection()
    transcriptions = conn.execute('SELECT * FROM transcriptions ORDER BY created_at DESC').fetchall()
    conn.close()
    return jsonify([dict(row) for row in transcriptions])


@app.route('/api/transcriptions/<transcription_id>', methods=['DELETE'])
def delete_transcription(transcription_id: str) -> Any:
    """
    API endpoint to delete a specific transcription.
    """
    logging.info(f"Delete transcription endpoint called for ID: {transcription_id}")
    conn = get_db_connection()
    conn.execute('DELETE FROM transcriptions WHERE id = ?', (transcription_id,))
    conn.commit()
    conn.close()
    return jsonify({'message': 'Transcription deleted successfully'})


@app.route('/api/transcriptions/clear', methods=['DELETE'])
def clear_transcriptions() -> Any:
    """
    API endpoint to clear all transcriptions from the database.
    """
    logging.info("Clear transcriptions endpoint called")
    conn = get_db_connection()
    conn.execute('DELETE FROM transcriptions')
    conn.commit()
    conn.close()
    return jsonify({'message': 'All transcriptions cleared'})


###########################################################################
# Background cleanup task to delete old files.
###########################################################################
def cleanup_task() -> None:
    while True:
        logging.info("Running cleanup task...")
        delete_old_files(TEMP_UPLOADS_DIR, DELETE_THRESHOLD)
        time.sleep(21600)  # Every 6 hours


cleanup_thread = threading.Thread(target=cleanup_task)
cleanup_thread.daemon = True
cleanup_thread.start()

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5001)