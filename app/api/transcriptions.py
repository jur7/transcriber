# app/api/transcriptions.py

import os
import uuid
import threading
import logging
import json # For parsing progress log from DB
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from app.config import Config
from app.services import transcription_service # Main service for processing
from app.services import file_service
# Import the model directly for DB operations related to job status/retrieval
from app.models import transcription as transcription_model

transcriptions_bp = Blueprint('transcriptions_bp', __name__)

# Logging is configured in __init__.py

@transcriptions_bp.route('/transcribe', methods=['POST'])
def transcribe_audio():
    """API endpoint to upload audio and start transcription job."""
    logging.info("[API] /transcribe endpoint called")
    if 'audio_file' not in request.files:
        logging.error("[API] No audio file provided in /transcribe request")
        return jsonify({'error': 'No audio file provided'}), 400
    file = request.files['audio_file']
    if file.filename == '':
        logging.error("[API] No file selected in /transcribe form")
        return jsonify({'error': 'No selected file'}), 400
    if not file_service.allowed_file(file.filename):
        logging.error(f"[API] File type not allowed: {file.filename}")
        return jsonify({'error': 'File type not allowed'}), 400

    original_filename = secure_filename(file.filename)
    job_id = str(uuid.uuid4()) # Generate unique ID for this job
    short_job_id = job_id[:8] # For logging

    # Save the uploaded file temporarily
    upload_dir = Config.TEMP_UPLOADS_DIR
    os.makedirs(upload_dir, exist_ok=True)
    # Include job_id in temp filename to avoid collisions
    temp_filename = os.path.join(upload_dir, f"{job_id}_{original_filename}")
    try:
        file.save(temp_filename)
        # Log file saving in job context
        logging.info(f"[JOB:{short_job_id}] Saved temp upload: {os.path.basename(temp_filename)}")
    except Exception as e:
        # Log error with job context if possible, otherwise general API error
        logging.exception(f"[API:JOB:{short_job_id}] Failed to save uploaded file {os.path.basename(temp_filename)}: {e}")
        return jsonify({'error': 'Failed to save uploaded file.'}), 500

    # Get parameters from form
    language_code = request.form.get('language_code', Config.DEFAULT_LANGUAGE)
    api_choice = request.form.get('api_choice', Config.DEFAULT_API)
    context_prompt = request.form.get('context_prompt', '')

    try:
        # Create initial job record in the database (model function logs DB action)
        transcription_model.create_transcription_job(
            job_id=job_id,
            filename=original_filename,
            api_used=api_choice
        )
        logging.info(f"[JOB:{short_job_id}] Created job record for '{original_filename}'")

        # Start the background processing thread
        thread = threading.Thread(
            target=transcription_service.process_transcription,
            args=(job_id, temp_filename, language_code, api_choice, original_filename, context_prompt),
            daemon=True # Allows app to exit even if threads are running
        )
        thread.start()
        # Log thread start, service layer will log actual processing start
        logging.info(f"[JOB:{short_job_id}] Background transcription thread started.")

        # Return job ID to the client for polling
        return jsonify({'job_id': job_id, 'message': 'Transcription job started successfully.'}), 202 # Accepted

    except Exception as e:
        # Log error during job initiation phase
        logging.exception(f"[API:JOB:{short_job_id}] Error initiating transcription job: {e}")
        # Attempt to clean up saved file if job creation failed
        if os.path.exists(temp_filename):
            try:
                os.remove(temp_filename)
                logging.info(f"[JOB:{short_job_id}] Cleaned up temp file {os.path.basename(temp_filename)} after initiation error.")
            except OSError:
                logging.error(f"[JOB:{short_job_id}] Failed to cleanup temp file {os.path.basename(temp_filename)} after error.")
        return jsonify({'error': 'Failed to start transcription job.'}), 500


@transcriptions_bp.route('/progress/<job_id>', methods=['GET'])
def get_progress(job_id):
    """API endpoint to poll for job progress and results."""
    short_job_id = job_id[:8] # Use short ID for logging
    logging.debug(f"[API:/progress] Progress check requested for job {short_job_id}") # Use debug for frequent checks
    try:
        job_data = transcription_model.get_transcription_by_id(job_id) # Model logs DB access

        if not job_data:
            logging.warning(f"[API:/progress] Progress check failed: Job ID not found: {short_job_id}")
            return jsonify({'error': 'Job not found'}), 404

        # Determine if finished based on status
        is_finished = job_data['status'] in ('finished', 'error')
        is_error = job_data['status'] == 'error'

        # Parse progress log from JSON string
        progress_log = []
        if job_data['progress_log']:
            try:
                progress_log = json.loads(job_data['progress_log'])
                if not isinstance(progress_log, list):
                    progress_log = [str(progress_log)] # Handle non-list JSON
            except (json.JSONDecodeError, TypeError):
                # Log parsing error with job context
                logging.warning(f"[JOB:{short_job_id}] Could not parse progress log from DB. Content: {job_data['progress_log']}")
                progress_log = ["Error parsing progress log."]

        # Prepare response structure
        response_data = {
            'job_id': job_id,
            'status': job_data['status'],
            'progress': progress_log,
            'finished': is_finished,
            'error_message': job_data['error_message'] if is_error else None,
            'result': None # Populate result only if finished successfully
        }

        if is_finished and not is_error:
            # If finished successfully, populate the 'result' field
            response_data['result'] = {
                'id': job_data['id'],
                'filename': job_data['filename'],
                'detected_language': job_data['detected_language'],
                'transcription_text': job_data['transcription_text'],
                'api_used': job_data['api_used'],
                'created_at': job_data['created_at'],
                'status': job_data['status']
            }
            logging.debug(f"[API:/progress] Job {short_job_id} finished successfully, returning result.")

        elif is_error:
             logging.debug(f"[API:/progress] Job {short_job_id} finished with error.")

        else:
             logging.debug(f"[API:/progress] Job {short_job_id} status: {job_data['status']}")


        return jsonify(response_data)

    except Exception as e:
        # Log error fetching progress with job context
        logging.exception(f"[API:/progress:JOB:{short_job_id}] Error fetching progress: {e}")
        return jsonify({'error': 'Internal server error fetching job progress.'}), 500


@transcriptions_bp.route('/transcriptions', methods=['GET'])
def get_transcriptions():
    """API endpoint to get the list of all transcription records."""
    logging.info("[API] /transcriptions GET endpoint called")
    try:
        # Fetch all records from DB (model function logs DB access)
        transcriptions = transcription_model.get_all_transcriptions()
        logging.info(f"[API] Retrieved {len(transcriptions)} transcription records.")
        return jsonify(transcriptions)
    except Exception as e:
        logging.exception("[API] Error fetching transcription history:")
        return jsonify({'error': 'Failed to retrieve transcription history.'}), 500


@transcriptions_bp.route('/transcriptions/<transcription_id>', methods=['DELETE'])
def delete_transcription(transcription_id):
    """API endpoint to delete a specific transcription record."""
    short_job_id = transcription_id[:8] # Use short ID for logging
    logging.info(f"[API] /transcriptions DELETE endpoint called for ID: {short_job_id}")
    try:
        # Check if exists first? Optional, DELETE is often idempotent.
        job_data = transcription_model.get_transcription_by_id(transcription_id) # Model logs DB access
        if not job_data:
             logging.warning(f"[API:JOB:{short_job_id}] Delete failed: Transcription not found.")
             return jsonify({'error': 'Transcription not found'}), 404

        transcription_model.delete_transcription(transcription_id) # Model logs DB action
        # Note: This doesn't delete the original audio or transcription text files if saved elsewhere.
        logging.info(f"[API:JOB:{short_job_id}] Transcription deleted successfully.")
        return jsonify({'message': 'Transcription deleted successfully'})
    except Exception as e:
        logging.exception(f"[API:JOB:{short_job_id}] Error deleting transcription:")
        return jsonify({'error': 'Failed to delete transcription.'}), 500


@transcriptions_bp.route('/transcriptions/clear', methods=['DELETE'])
def clear_transcriptions():
    """API endpoint to delete all transcription records."""
    logging.info("[API] /transcriptions/clear DELETE endpoint called")
    try:
        transcription_model.clear_transcriptions() # Model logs DB action
        logging.info("[API] All transcriptions cleared successfully.")
        return jsonify({'message': 'All transcriptions cleared'})
    except Exception as e:
        logging.exception("[API] Error clearing all transcriptions:")
        return jsonify({'error': 'Failed to clear all transcriptions.'}), 500

