# app/services/transcription_service.py

import os
import uuid
import threading
import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Any
from flask import current_app
from app import app # Import the app instance

# Use the new DB functions for status/progress
from app.models import transcription as transcription_model
from app.services import file_service

from app.services.api_clients.assemblyai import AssemblyAITranscriptionAPI
from app.services.api_clients.openai_whisper import OpenAITranscriptionAPI
from app.services.api_clients.openai_gpt4o import OpenAIGPT4oTranscriptionAPI
from app.services.api_clients.gemini import GeminiTranscriptionAPI

# Import specific API errors if available (example for OpenAI)
from openai import OpenAIError

# --- Helper Function for Progress Update ---

def _update_progress(job_id: str, message: str, is_error: bool = False) -> None:
    """Formats, logs (console), and saves (DB) a progress message for a job."""
    short_job_id = job_id[:8]
    log_level = logging.ERROR if is_error else logging.INFO
    # Format message with prefix for CONSOLE logging
    log_message_console = f"[JOB:{short_job_id}] {message}"

    # Log to console/file (using the structured message)
    logging.log(log_level, log_message_console)

    try:
        # Update database log (needs app context)
        with app.app_context():
             # Pass the original, unmodified message string intended for the UI to the DB log
             transcription_model.update_job_progress(job_id, message)
    except Exception as e:
        # Log error updating DB progress, but don't stop the main process
        logging.error(f"[JOB:{short_job_id}] Failed to update DB progress log: {e}")

# --- API Client Factory ---

def get_transcription_api(api_choice: str) -> Any:
    """Factory function to get an instance of the chosen transcription API client."""
    # This function now runs within the app context provided by process_transcription
    # API client __init__ methods will log their own initialization.
    try:
        if api_choice == 'assemblyai':
            api_key = current_app.config.get('ASSEMBLYAI_API_KEY')
            if not api_key:
                raise ValueError("AssemblyAI API key is not configured.")
            return AssemblyAITranscriptionAPI(api_key)
        elif api_choice == 'whisper':
            api_key = current_app.config.get('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OpenAI API key is not configured.")
            return OpenAITranscriptionAPI(api_key)
        elif api_choice == 'gpt4o':
            api_key = current_app.config.get('OPENAI_API_KEY')
            if not api_key:
                raise ValueError("OpenAI API key is not configured.")
            return OpenAIGPT4oTranscriptionAPI(api_key)
        elif api_choice == 'gemini':
            # For Google AI provider, GEMINI_API_KEY is required; for Vertex, project/location are required.
            # The client reads the rest of config internally.
            api_key = current_app.config.get('GEMINI_API_KEY')
            # api_key may be None for Vertex provider
            return GeminiTranscriptionAPI(api_key)
        else:
            message = f"Invalid API choice specified: {api_choice}"
            logging.error(f"[SYSTEM] {message}") # Log as system error if choice is invalid
            raise ValueError(message)
    except ValueError as ve:
         # Log config errors during factory creation
         logging.error(f"[SYSTEM] Configuration error getting API client for '{api_choice}': {ve}")
         raise # Re-raise to be caught by process_transcription

# --- Main Background Transcription Process ---

def process_transcription(job_id: str, temp_filename: str, language_code: str,
                          api_choice: str, original_filename: str, context_prompt: str = "") -> None:
    """
    Handles audio/video transcription in the background, updating status in the database.
    For video files, extracts audio first, then processes through transcription pipeline.
    Runs within a Flask application context.
    """
    short_job_id = job_id[:8] # For console logging
    extracted_audio_path = None  # Track extracted audio for cleanup

    with app.app_context(): # Ensure access to current_app.config and models
        try:
            # Update status: Processing (model function logs the DB update)
            transcription_model.update_job_status(job_id, 'processing')
            # Log start using the helper - SIMPLE UI MESSAGE
            _update_progress(job_id, "Transcription process started.")
            # SIMPLE UI MESSAGE
            _update_progress(job_id, f"Using API: {api_choice}, Language: {language_code}")

            # Check if this is a video file - if so, extract audio first
            audio_file_path = temp_filename
            if file_service.is_video_file(original_filename):
                _update_progress(job_id, f"Video file detected: {original_filename}")
                progress_callback = lambda msg, is_err=False: _update_progress(job_id, msg, is_error=is_err)

                # Extract audio from video
                upload_dir = os.path.dirname(temp_filename)
                extracted_audio_path = file_service.extract_audio_from_video(
                    temp_filename,
                    upload_dir,
                    progress_callback=progress_callback
                )

                if not extracted_audio_path:
                    raise Exception("Failed to extract audio from video file.")

                # Use the extracted audio file for transcription
                audio_file_path = extracted_audio_path
                _update_progress(job_id, "Audio extraction completed. Starting transcription...")

            # Check for potential splitting (before calling API client)
            try:
                file_size = os.path.getsize(audio_file_path)
                limit = file_service.OPENAI_MAX_FILE_SIZE # Default to OpenAI limit
                # Add specific limits if AssemblyAI differs significantly
                # if api_choice == 'assemblyai': limit = ASSEMBLYAI_LIMIT

                # Only log splitting if size exceeds limit AND API requires splitting
                if file_size > limit and api_choice in ('whisper', 'gpt4o', 'gemini'):
                     # SIMPLE UI MESSAGE
                     _update_progress(job_id, f"Splitting large file: {original_filename}...")
            except OSError as e:
                 # Use warning level for non-fatal issue during check - SIMPLE UI MESSAGE
                 _update_progress(job_id, f"Warning: Could not get size of temp file '{os.path.basename(audio_file_path)}'.", is_error=False) # Log as warning


            # Get API client instance
            api = get_transcription_api(api_choice) # Logs initialization internally to console

            # Define the progress callback to use our helper
            # This lambda ensures all messages from the API client are logged via _update_progress
            # The messages passed *to* this lambda from the clients will be the simple UI versions.
            progress_callback = lambda msg, is_err=False: _update_progress(job_id, msg, is_error=is_err)

            # Execute transcription via the chosen API client
            # Pass original_filename TO API CLIENTS for their internal logging/progress
            # SIMPLE UI MESSAGE (added before calling transcribe)
            _update_progress(job_id, f"Starting transcription of file: {original_filename}")
            if api_choice in ('gpt4o', 'whisper', 'gemini'):
                transcription_text, detected_language = api.transcribe(
                    audio_file_path=audio_file_path,
                    language_code=language_code,
                    progress_callback=progress_callback,
                    context_prompt=context_prompt,
                    original_filename=original_filename # Pass original filename
                )
            else: # AssemblyAI
                transcription_text, detected_language = api.transcribe(
                    audio_file_path=audio_file_path,
                    language_code=language_code,
                    progress_callback=progress_callback,
                    original_filename=original_filename # Pass original filename
                )

            # Check if transcription failed within the API client (indicated by None return)
            if transcription_text is None:
                 # Specific error should have been logged via callback by the client
                 # Raise a generic exception here to be caught below, ensuring error status is set
                raise Exception("Transcription failed via API client. See previous logs for details.")

            # Ensure detected_language has a sensible default if API returns None/empty
            detected_language = detected_language or language_code or 'unknown'

            # Finalize success in DB (model function logs the DB update)
            # The message "Transcription successful and saved." is added inside finalize_job_success
            transcription_model.finalize_job_success(
                job_id,
                transcription_text,
                detected_language
            )
            # Add a final verbose message for UI after DB save - SIMPLE UI MESSAGE
            _update_progress(job_id, f"Finalized job {short_job_id} successfully.")


        except ValueError as ve: # Configuration or validation errors before/during API init
            error_message = f"Configuration or Input Error: {str(ve)}"
            # Log error using helper - SIMPLE UI ERROR MESSAGE
            _update_progress(job_id, f"ERROR: {error_message}", is_error=True)
            # Set final error status in DB (model function logs DB action)
            transcription_model.set_job_error(job_id, error_message)
        except OpenAIError as oae: # Specific OpenAI errors (if not caught by client)
            error_message = f"OpenAI API Error: {str(oae)}"
            # SIMPLE UI ERROR MESSAGE
            _update_progress(job_id, f"ERROR: {error_message}", is_error=True)
            # User-friendly message for DB status
            transcription_model.set_job_error(job_id, "An error occurred with the OpenAI API.")
        # Add specific AssemblyAI error catch if library provides one, e.g., except aai.Error as aae:
        except Exception as e: # Catch-all for unexpected errors in this service layer or raised from clients
            error_message = f"An unexpected error occurred: {str(e)}"
             # SIMPLE UI ERROR MESSAGE
            _update_progress(job_id, f"ERROR: {error_message}", is_error=True)
            # Log the full traceback for debugging (console only)
            logging.exception(f"[JOB:{short_job_id}] Unexpected error during transcription process")
            # User-friendly message for DB status
            transcription_model.set_job_error(job_id, "An unexpected internal error occurred.")
        finally:
            # Cleanup temporary file (original upload - could be video or audio)
            if os.path.exists(temp_filename):
                try:
                    os.remove(temp_filename)
                    # Log cleanup success with job context (console only)
                    logging.info(f"[JOB:{short_job_id}] Cleaned up temp upload: {os.path.basename(temp_filename)}")
                    # Add verbose UI message for cleanup - USE FULL PATH AS REQUESTED
                    _update_progress(job_id, f"Deleted temporary upload file: {temp_filename}")
                except OSError as ose:
                    # Log cleanup failure as an error with job context (console only)
                    logging.error(f"[JOB:{short_job_id}] Error deleting temp upload file '{os.path.basename(temp_filename)}': {ose}")
                    # Add verbose UI warning message - USE BASENAME HERE FOR BREVITY
                    _update_progress(job_id, f"Warning: Failed to delete temporary upload file {os.path.basename(temp_filename)}.", is_error=False) # Log as warning

            # Cleanup extracted audio file (if video was processed)
            if extracted_audio_path and os.path.exists(extracted_audio_path):
                try:
                    os.remove(extracted_audio_path)
                    # Log cleanup success with job context (console only)
                    logging.info(f"[JOB:{short_job_id}] Cleaned up extracted audio: {os.path.basename(extracted_audio_path)}")
                    # Add verbose UI message for cleanup
                    _update_progress(job_id, f"Deleted extracted audio file: {extracted_audio_path}")
                except OSError as ose:
                    # Log cleanup failure as an error with job context (console only)
                    logging.error(f"[JOB:{short_job_id}] Error deleting extracted audio file '{os.path.basename(extracted_audio_path)}': {ose}")
                    # Add verbose UI warning message
                    _update_progress(job_id, f"Warning: Failed to delete extracted audio file {os.path.basename(extracted_audio_path)}.", is_error=False)
            # Note: Chunk files are cleaned up within the API client's _split_and_transcribe method's finally block.