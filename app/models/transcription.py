# app/models/transcription.py

import sqlite3
import os
import logging
from flask import current_app

def get_db_connection():
    db_path = current_app.config['DATABASE']
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db_path = current_app.config['DATABASE']
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
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
    logging.info("Database initialized.")

def insert_transcription(transcription_data):
    conn = get_db_connection()
    conn.execute(
        '''
        INSERT INTO transcriptions (id, filename, recording_date, detected_language, transcription_text, api_used, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ''',
        (
            transcription_data['id'],
            transcription_data['filename'],
            transcription_data['recording_date'],
            transcription_data['detected_language'],
            transcription_data['transcription_text'],
            transcription_data['api_used'],
            transcription_data['created_at']
        )
    )
    conn.commit()
    conn.close()
    logging.info(f"Inserted transcription {transcription_data['id']} into database.")

def get_all_transcriptions():
    conn = get_db_connection()
    transcriptions = conn.execute('SELECT * FROM transcriptions ORDER BY created_at DESC').fetchall()
    conn.close()
    return [dict(row) for row in transcriptions]

def delete_transcription(transcription_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM transcriptions WHERE id = ?', (transcription_id,))
    conn.commit()
    conn.close()
    logging.info(f"Deleted transcription {transcription_id} from database.")

def clear_transcriptions():
    conn = get_db_connection()
    conn.execute('DELETE FROM transcriptions')
    conn.commit()
    conn.close()
    logging.info("Cleared all transcriptions from database.")