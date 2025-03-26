# app/models/transcription.py

import sqlite3
import os
import logging
import json # For handling progress log
from flask import current_app, g
from datetime import datetime, timezone
from typing import Optional

# --- Database Connection Handling (using Flask 'g') ---

def get_db():
    """Opens a new database connection if there is none yet for the current application context."""
    if 'db' not in g:
        db_path = current_app.config['DATABASE']
        try:
            os.makedirs(os.path.dirname(db_path), exist_ok=True)
            g.db = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
            g.db.row_factory = sqlite3.Row
            logging.debug("[DB] Database connection opened.")
        except sqlite3.Error as e:
            logging.error(f"[DB] Database connection error: {e}")
            raise
    return g.db

def close_db(e=None):
    """Closes the database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()
        logging.debug("[DB] Database connection closed.")

# --- Database Initialization ---
def init_db_command():
    """
    Initialize the database schema.
    Uses a file lock (db file + ".lock") to ensure that if multiple worker processes
    try to initialize the database concurrently, only the first one proceeds.
    Subsequent processes will see the schema has already been created and skip initialization.
    """
    db_path = current_app.config['DATABASE']
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    lock_path = db_path + ".lock"
    
    # Use a lock file to coordinate between processes.
    with open(lock_path, 'w') as lock_file:
        try:
            import fcntl
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            
            # Check if the 'transcriptions' table already exists.
            init_needed = True
            if os.path.exists(db_path):
                try:
                    conn = sqlite3.connect(db_path)
                    cursor = conn.cursor()
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='transcriptions'")
                    if cursor.fetchone():
                        init_needed = False
                        logging.info("[DB] Database already initialized; skipping schema initialization.")
                    conn.close()
                except Exception as e:
                    logging.error(f"[DB] Error checking existing database schema: {e}")
                    init_needed = True
            
            if not init_needed:
                return
            
            # Proceed with schema creation if needed.
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            logging.info(f"[DB] Verifying/Initializing database schema at {db_path}...")
            conn.execute(
                '''
                CREATE TABLE IF NOT EXISTS transcriptions (
                    id TEXT PRIMARY KEY,
                    filename TEXT,
                    detected_language TEXT,
                    transcription_text TEXT,
                    api_used TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    progress_log TEXT DEFAULT '[]',
                    error_message TEXT
                )
                '''
            )
            logging.info("[DB] 'transcriptions' table verified/created.")
            conn.commit()
            conn.close()
            logging.info("[DB] Database schema verification/initialization complete.")
        except Exception as e:
            logging.error(f"[DB] Database initialization/migration error: {e}")
            raise
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)

# --- CRUD and Job Status Operations ---

def create_transcription_job(job_id: str, filename: str, api_used: str) -> None:
    """Creates an initial record for a transcription job."""
    short_job_id = job_id[:8]
    sql = '''
        INSERT INTO transcriptions (id, filename, api_used, created_at, status, progress_log, error_message)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        '''
    now_utc_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
    initial_log = json.dumps(["Job created."])
    try:
        db = get_db()
        db.execute(sql, (job_id, filename, api_used, now_utc_iso, 'pending', initial_log, None))
        db.commit()
        logging.info(f"[DB:JOB:{short_job_id}] Created initial job record.")
    except sqlite3.Error as e:
        logging.error(f"[DB:JOB:{short_job_id}] Error creating job record: {e}")
        raise

def update_job_progress(job_id: str, message: str) -> None:
    """Appends a message to the job's progress log in the database."""
    short_job_id = job_id[:8]
    try:
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT progress_log FROM transcriptions WHERE id = ?", (job_id,))
        row = cursor.fetchone()
        if row:
            try:
                current_log = json.loads(row['progress_log'])
                if not isinstance(current_log, list):
                    current_log = []
            except (json.JSONDecodeError, TypeError):
                current_log = []
            current_log.append(message)
            new_log_json = json.dumps(current_log)
            cursor.execute("UPDATE transcriptions SET progress_log = ? WHERE id = ?", (new_log_json, job_id))
            db.commit()
        else:
            logging.warning(f"[DB:JOB:{short_job_id}] Attempted to update DB progress for non-existent job.")
    except sqlite3.Error as e:
        logging.error(f"[DB:JOB:{short_job_id}] Error updating DB progress log: {e}")

def update_job_status(job_id: str, status: str) -> None:
    """Updates the status of a job."""
    short_job_id = job_id[:8]
    try:
        db = get_db()
        db.execute("UPDATE transcriptions SET status = ? WHERE id = ?", (status, job_id))
        db.commit()
        logging.info(f"[DB:JOB:{short_job_id}] Updated status to: {status}")
    except sqlite3.Error as e:
        logging.error(f"[DB:JOB:{short_job_id}] Error updating status: {e}")

def set_job_error(job_id: str, error_message: str) -> None:
    """Sets the job status to 'error' and records the error message."""
    short_job_id = job_id[:8]
    try:
        db = get_db()
        update_job_progress(job_id, f"ERROR: {error_message}")
        db.execute("UPDATE transcriptions SET status = 'error', error_message = ? WHERE id = ?", (error_message, job_id))
        db.commit()
        logging.error(f"[DB:JOB:{short_job_id}] Set error status. Message: {error_message}")
    except sqlite3.Error as e:
        logging.error(f"[DB:JOB:{short_job_id}] Error setting error status: {e}")

def finalize_job_success(job_id: str, transcription_text: str, detected_language: str) -> None:
    """Finalizes a job as successful and saves the results."""
    short_job_id = job_id[:8]
    try:
        db = get_db()
        update_job_progress(job_id, "Transcription successful and saved.")
        db.execute(
            """
            UPDATE transcriptions
            SET status = 'finished',
                transcription_text = ?,
                detected_language = ?,
                error_message = NULL
            WHERE id = ?
            """,
            (transcription_text, detected_language, job_id)
        )
        db.commit()
        logging.info(f"[DB:JOB:{short_job_id}] Finalized job successfully.")
    except sqlite3.Error as e:
        logging.error(f"[DB:JOB:{short_job_id}] Error finalizing successful job: {e}")
        set_job_error(job_id, f"Failed to save final results: {e}")

def get_transcription_by_id(transcription_id: str) -> Optional[dict]:
    """Retrieves a specific transcription/job record by ID."""
    short_job_id = transcription_id[:8]
    try:
        db = get_db()
        transcription = db.execute('SELECT * FROM transcriptions WHERE id = ?', (transcription_id,)).fetchone()
        logging.debug(f"[DB:JOB:{short_job_id}] Retrieved job record by ID.")
        return dict(transcription) if transcription else None
    except sqlite3.Error as e:
        logging.error(f"[DB:JOB:{short_job_id}] Error retrieving transcription by ID: {e}")
        return None

def get_all_transcriptions() -> list[dict]:
    """Retrieves all completed transcriptions ordered by creation date."""
    try:
        db = get_db()
        transcriptions = db.execute('SELECT * FROM transcriptions ORDER BY created_at DESC').fetchall()
        logging.debug(f"[DB] Retrieved {len(transcriptions)} total transcription records.")
        return [dict(row) for row in transcriptions]
    except sqlite3.Error as e:
        logging.error(f"[DB] Error retrieving all transcriptions: {e}")
        return []

def delete_transcription(transcription_id: str) -> None:
    """Deletes a specific transcription record by ID."""
    short_job_id = transcription_id[:8]
    try:
        db = get_db()
        db.execute('DELETE FROM transcriptions WHERE id = ?', (transcription_id,))
        db.commit()
        logging.info(f"[DB:JOB:{short_job_id}] Deleted transcription record.")
    except sqlite3.Error as e:
        logging.error(f"[DB:JOB:{short_job_id}] Error deleting transcription record: {e}")
        raise

def clear_transcriptions() -> None:
    """Deletes all transcription records from the database."""
    try:
        db = get_db()
        db.execute('DELETE FROM transcriptions')
        db.commit()
        logging.info("[DB] Cleared all transcription records.")
    except sqlite3.Error as e:
        logging.error(f"[DB] Error clearing all transcriptions: {e}")
        raise

# --- Flask App Integration ---
def init_app(app):
    """Register database functions with the Flask app."""
    app.teardown_appcontext(close_db)
    # Initialize schema on startup within app context.
    with app.app_context():
        init_db_command()
