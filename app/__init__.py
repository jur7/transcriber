# app/__init__.py

import os
import threading
import time
import logging
from flask import Flask, render_template
#from flask_sock import Sock
from werkzeug.middleware.proxy_fix import ProxyFix
from app.config import Config

# Configure root logger - Use a simple format, prefixes will be added in messages
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Reduce Werkzeug logging noise for cleaner output
werkzeug_logger = logging.getLogger('werkzeug')
werkzeug_logger.setLevel(logging.WARNING)

app = Flask(__name__,
            template_folder=os.path.join(os.getcwd(), 'app', 'templates'),
            static_folder=os.path.join(os.getcwd(), 'app', 'static'))
app.config.from_object(Config)

#sock = Sock(app)  # Provide websocket support via Flask-Sock


# Ensure Flask/Gunicorn respect reverse-proxy headers (X-Forwarded-*)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


# Initialize database and register teardown context
from app.models import transcription
# Add logging context for DB initialization
logging.info("[SYSTEM] Initializing database connection handling...")
transcription.init_app(app)
logging.info("[SYSTEM] Database setup complete.")

# Register Blueprints
from app.api.transcriptions import transcriptions_bp
app.register_blueprint(transcriptions_bp, url_prefix='/api')

from app.api.auth import auth_bp
app.register_blueprint(auth_bp, url_prefix='/api')

# Register realtime websocket proxy routes.
#from app.api import realtime_ws
#realtime_ws.init_app(sock)

@app.route('/')
def index():
    """Renders the main index page."""
    # Main upload workflow remains on the existing index page.
    return render_template(
        'index.html',
        default_api=app.config.get('DEFAULT_API'),
        default_language=app.config.get('DEFAULT_LANGUAGE'),
        supported_languages=app.config.get('SUPPORTED_LANGUAGE_NAMES'),
    )

# Version/info endpoints
from app.api.version_info import version_bp
app.register_blueprint(version_bp, url_prefix='/api')


#@app.route('/realtime')
#def realtime_page():
#    """Renders the realtime streaming page."""
#    # Render the mobile-oriented realtime streaming UI.
#    return render_template(
#        'realtime.html',
#        sample_rate=app.config.get('REALTIME_SAMPLE_RATE', 16000),
#        chunk_ms=app.config.get('REALTIME_CHUNK_MILLIS', 250),
#        enable_translation=app.config.get('REALTIME_ENABLE_TRANSLATION', False),
#        enable_tts=app.config.get('REALTIME_ENABLE_TTS', False),
#        default_voice=app.config.get('REALTIME_DEFAULT_VOICE', 'verse'),
#        supported_languages=app.config.get('SUPPORTED_LANGUAGE_NAMES', {}),
#    )

# --- Background task for cleaning up old files ---
from app.services.file_service import cleanup_old_files

def run_cleanup_task():
    """Periodically cleans up old files in the uploads directory."""
    # Give the app a moment to start up before the first run
    time.sleep(15)
    worker_pid = os.getpid() # Get PID once
    logging.info(f"[SYSTEM:{worker_pid}] Cleanup thread started.")

    while True:
        try:
            # Need app context to access config
            with app.app_context():
                upload_dir = app.config['TEMP_UPLOADS_DIR']
                threshold = app.config.get('DELETE_THRESHOLD', 24 * 60 * 60) # Default 24h
                logging.info(f"[SYSTEM:{worker_pid}] Running periodic cleanup in '{upload_dir}' (threshold: {threshold}s)...")
                # The cleanup_old_files function will log specifics about deleted files
                deleted_count = cleanup_old_files(upload_dir, threshold)
                logging.info(f"[SYSTEM:{worker_pid}] Cleanup task finished. Deleted {deleted_count} old file(s).")
        except Exception as e:
            # Log exceptions occurring in the cleanup loop itself
            logging.error(f"[SYSTEM:{worker_pid}] Error during cleanup task loop: {e}", exc_info=True) # Include traceback

        # Sleep for the configured interval (e.g., 6 hours)
        sleep_interval = 21600 # 6 hours in seconds
        logging.debug(f"[SYSTEM:{worker_pid}] Cleanup thread sleeping for {sleep_interval} seconds.")
        time.sleep(sleep_interval)

# Start the cleanup thread only if not already running (e.g., check a flag or use a lock if needed,
# though Gunicorn often handles process management). Assuming one thread per worker process is intended.
# The check `if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'` can prevent
# the thread from starting twice in Flask's debug mode reloader.
# However, for Gunicorn, starting it directly is usually fine.
if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    cleanup_thread = threading.Thread(target=run_cleanup_task, daemon=True)
    cleanup_thread.start()
# else: # Optional: Log why thread isn't starting in debug reloader sub-process
#    logging.info(f"[SYSTEM:{os.getpid()}] Skipping cleanup thread start in Flask debug reloader sub-process.")
