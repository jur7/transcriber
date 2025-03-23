# app/services/transcription_service.py

import os
import uuid
import threading
import logging
from datetime import datetime
from typing import Callable, Optional, Any
from flask import current_app
from app import app

from app.models import transcription
from app.services import file_service

from app.services.api_clients.assemblyai import AssemblyAITranscriptionAPI
from app.services.api_clients.openai_whisper import OpenAITranscriptionAPI
from app.services.api_clients.openai_gpt4o import OpenAIGPT4oTranscriptionAPI

# Maximum file size for OpenAI APIs (25MB)
OPENAI_MAX_FILE_SIZE = 25 * 1024 * 1024

jobs = {}
jobs_lock = threading.Lock()

def append_progress(job_id: str, message: str) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id]['progress'].append(message)
    logging.info(f"Job {job_id}: {message}")

def get_transcription_api(api_choice: str) -> Any:
    if api_choice == 'assemblyai':
        return AssemblyAITranscriptionAPI(current_app.config['ASSEMBLYAI_API_KEY'])
    elif api_choice == 'whisper':
        return OpenAITranscriptionAPI(current_app.config['OPENAI_API_KEY'])
    elif api_choice == 'gpt4o':
        return OpenAIGPT4oTranscriptionAPI(current_app.config['OPENAI_API_KEY'])
    else:
        message = f"Invalid API choice: {api_choice}"
        logging.error(message)
        raise ValueError(message)

def process_transcription(job_id: str, temp_filename: str, language_code: str,
                          api_choice: str, original_filename: str, context_prompt: str = "") -> None:
    """
    Main entry point for handling audio transcription in the background.
    Splits large files if necessary, calls the appropriate transcription API,
    and stores the result in the database.
    """
    with app.app_context():
        try:
            append_progress(job_id, "Transcription started.")
            append_progress(job_id, f"Received language code: {language_code}, API choice: {api_choice}")
            append_progress(job_id, f"API call parameters: File: {temp_filename}, Language: {language_code}, Context Prompt: {context_prompt}")

            api = get_transcription_api(api_choice)
            progress_callback = lambda msg: append_progress(job_id, msg)

            # Pass context_prompt to whisper or gpt4o, skip for assemblyAI
            if api_choice in ('gpt4o', 'whisper'):
                transcription_text, detected_language = api.transcribe(
                    temp_filename,
                    language_code,
                    progress_callback=progress_callback,
                    context_prompt=context_prompt
                )
            else:
                transcription_text, detected_language = api.transcribe(
                    temp_filename,
                    language_code,
                    progress_callback=progress_callback
                )

            transcription_data = {
                'id': job_id,
                'filename': original_filename,
                'detected_language': detected_language,
                'transcription_text': transcription_text,
                'api_used': api_choice,
                # Store a valid ISO format date string (without microseconds) so JavaScript can parse it.
                'created_at': datetime.now().replace(microsecond=0).isoformat()
            }
            transcription.insert_transcription(transcription_data)
            append_progress(job_id, "Transcription successful.")
            with jobs_lock:
                jobs[job_id]['result'] = transcription_data
        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            append_progress(job_id, error_message)
            with jobs_lock:
                jobs[job_id]['result'] = {'error': str(e)}
        finally:
            with jobs_lock:
                jobs[job_id]['finished'] = True
            if os.path.exists(temp_filename):
                os.remove(temp_filename)
                logging.info(f"Deleted temporary file: {temp_filename}")