# app/__init__.py

import os
import threading
import time
import logging
from flask import Flask, render_template
from app.config import Config

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Create the Flask app.
app = Flask(__name__,
            template_folder=os.path.join(os.getcwd(), 'app', 'templates'),
            static_folder=os.path.join(os.getcwd(), 'app', 'static'))
app.config.from_object(Config)

# Set Werkzeug (the Flask development server logger) to WARNING to suppress routine access logs.
from logging import getLogger
getLogger('werkzeug').setLevel(logging.WARNING)

# Initialize the database inside an application context.
with app.app_context():
    from app.models import transcription
    transcription.init_db()

# Register API blueprints.
from app.api.transcriptions import transcriptions_bp
app.register_blueprint(transcriptions_bp, url_prefix='/api')

from app.api.auth import auth_bp
app.register_blueprint(auth_bp, url_prefix='/api')

# Serve the main UI.
@app.route('/')
def index():
    return render_template('index.html',
                           default_api=app.config.get('DEFAULT_API'),
                           default_language=app.config.get('DEFAULT_LANGUAGE'))

# Start a background cleanup thread (to delete old temporary files)
from app.services.file_service import cleanup_old_files

def run_cleanup_task():
    while True:
        logging.info("Running cleanup task...")
        cleanup_old_files(app.config['TEMP_UPLOADS_DIR'], app.config.get('DELETE_THRESHOLD', 24*60*60))
        time.sleep(21600)  # Run every 6 hours

cleanup_thread = threading.Thread(target=run_cleanup_task)
cleanup_thread.daemon = True
cleanup_thread.start()
