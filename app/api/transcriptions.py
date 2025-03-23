# app/api/transcriptions.py

import os
import uuid
import threading
import logging
from flask import Blueprint, request, jsonify
from werkzeug.utils import secure_filename
from app.config import Config
from app.services import transcription_service
from app.services import file_service
from app.models import transcription as transcription_model

transcriptions_bp = Blueprint('transcriptions_bp', __name__)

@transcriptions_bp.route('/transcribe', methods=['POST'])
def transcribe_audio():
    logging.info("Transcribe audio endpoint called")
    if 'audio_file' not in request.files:
        logging.error("No audio file provided")
        return jsonify({'error': 'No audio file provided'}), 400
    file = request.files['audio_file']
    if file.filename == '':
        logging.error("No selected file")
        return jsonify({'error': 'No selected file'}), 400
    if not file_service.allowed_file(file.filename):
        logging.error("File type not allowed")
        return jsonify({'error': 'File type not allowed'}), 400

    # Limit active concurrent jobs.
    with transcription_service.jobs_lock:
        active_jobs = sum(1 for job in transcription_service.jobs.values() if not job.get('finished', False))
        if active_jobs >= 10:
            return jsonify({'error': 'Too many concurrent transcription jobs. Please try again later.'}), 429

    original_filename = secure_filename(file.filename)
    job_id = str(uuid.uuid4())
    upload_dir = Config.TEMP_UPLOADS_DIR
    os.makedirs(upload_dir, exist_ok=True)
    temp_filename = os.path.join(upload_dir, f"{job_id}_{original_filename}")
    file.save(temp_filename)

    with transcription_service.jobs_lock:
        transcription_service.jobs[job_id] = {'progress': [], 'finished': False, 'result': None}

    language_code = request.form.get('language_code', Config.DEFAULT_LANGUAGE)
    api_choice = request.form.get('api_choice', Config.DEFAULT_API)
    context_prompt = request.form.get('context_prompt', '')  # New field for OpenAI context prompt

    # Start a background thread to process this transcription.
    thread = threading.Thread(
        target=transcription_service.process_transcription,
        args=(job_id, temp_filename, language_code, api_choice, original_filename, context_prompt)
    )
    thread.start()
    return jsonify({'job_id': job_id, 'message': 'Transcription started'})

@transcriptions_bp.route('/progress/<job_id>', methods=['GET'])
def get_progress(job_id):
    with transcription_service.jobs_lock:
        if job_id not in transcription_service.jobs:
            return jsonify({'error': 'Job not found'}), 404
        job_info = transcription_service.jobs[job_id].copy()
    return jsonify(job_info)

@transcriptions_bp.route('/transcriptions', methods=['GET'])
def get_transcriptions():
    logging.info("Get transcriptions endpoint called")
    transcriptions = transcription_model.get_all_transcriptions()
    return jsonify(transcriptions)

@transcriptions_bp.route('/transcriptions/<transcription_id>', methods=['DELETE'])
def delete_transcription(transcription_id):
    logging.info(f"Delete transcription endpoint called for ID: {transcription_id}")
    transcription_model.delete_transcription(transcription_id)
    return jsonify({'message': 'Transcription deleted successfully'})

@transcriptions_bp.route('/transcriptions/clear', methods=['DELETE'])
def clear_transcriptions():
    logging.info("Clear transcriptions endpoint called")
    transcription_model.clear_transcriptions()
    return jsonify({'message': 'All transcriptions cleared'})