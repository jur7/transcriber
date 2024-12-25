import os
import sqlite3
import logging
from datetime import datetime
import uuid
import assemblyai as aai
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.exceptions import NotFound

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Correct the paths for static and template folders
app = Flask(__name__, static_folder='app/static', template_folder='app/templates')
CORS(app)

# AssemblyAI API Key
aai.settings.api_key = "d0969df61d2c41f1b573ad9d7b53145f"  # Replace with your actual API key

# Database setup
DATABASE = 'transcriptions.db'

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
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# Serve Frontend
@app.route('/')
def index():
    # Correct the path to serve index.html
    template_path = app.template_folder
    full_path = os.path.join(template_path, 'index.html')

    print("-" * 20)
    print("Attempting to serve index.html")
    print("Template folder:", template_path)
    print("Full path to index.html:", full_path)
    print("File exists:", os.path.exists(full_path))
    print("-" * 20)

    try:
        return send_from_directory(app.template_folder, 'index.html')
    except NotFound:
        print("Error: index.html not found in the specified directory.")
        return "Error: index.html not found", 404

@app.route('/<path:path>')
def serve_static(path):
    # Serve static files
    return send_from_directory(app.static_folder, path)

# API Endpoints
@app.route('/api/transcribe', methods=['POST'])
def transcribe_audio():
    logging.info("Transcribe audio endpoint called")
    if 'audio_file' not in request.files:
        return jsonify({'error': 'No audio file provided'}), 400

    audio_file = request.files['audio_file']
    language_code = request.form.get('language_code')
    logging.info(f"Received language code: {language_code}")

    transcription_id = str(uuid.uuid4())
    temp_filename = f"{transcription_id}.temp"
    audio_file.save(temp_filename)

    try:
        if language_code == 'auto':
            config = aai.TranscriptionConfig(
                language_detection=True,
                webhook_url=None
            )
        elif language_code in ['en', 'nl', 'fr', 'es']:
            config = aai.TranscriptionConfig(
                language_code=language_code,
                webhook_url=None
            )
        else:
            logging.error("Invalid language code provided")
            return jsonify({'error': 'Invalid language code'}), 400

        transcriber = aai.Transcriber(config=config)
        transcript = transcriber.transcribe(temp_filename)

        if transcript.status == aai.TranscriptStatus.error:
            logging.error(f"Transcription failed: {transcript.error}")
            return jsonify({'error': f"Transcription failed: {transcript.error}"}), 500

        # Get the detected language
        if language_code == 'auto':
            # Try to get the detected language from transcript attributes
            try:
                detected_language = getattr(transcript, 'detected_language_code', None)
                if not detected_language:
                    detected_language = getattr(transcript, 'language_code', 'en')
            except AttributeError:
                detected_language = 'en'
            logging.info(f"Detected language: {detected_language}")
        else:
            detected_language = language_code

        conn = get_db_connection()
        conn.execute('''
            INSERT INTO transcriptions (id, filename, recording_date, detected_language, transcription_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (transcription_id, audio_file.filename, datetime.now().isoformat(), detected_language, transcript.text, datetime.now().isoformat()))
        conn.commit()
        conn.close()

        logging.info(f"Transcription successful: {transcript.text[:100]}...")
        return jsonify({
            'id': transcription_id,
            'filename': audio_file.filename,
            'recording_date': datetime.now().isoformat(),
            'detected_language': detected_language,
            'transcription_text': transcript.text
        })

    except Exception as e:
        logging.exception("An error occurred during transcription")
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
