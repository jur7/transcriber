# app/services/file_service.py

import os
import time
import logging
from typing import List, Callable, Optional
from pydub import AudioSegment

ALLOWED_EXTENSIONS = {'mp3', 'm4a', 'wav', 'ogg', 'webm'}

def allowed_file(filename: str) -> bool:
    """
    Checks if a file has an allowed extension.
    """
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Constant for chunk length: 10 minutes in milliseconds
CHUNK_LENGTH_MS = 10 * 60 * 1000

def split_audio_file(file_path: str, temp_dir: str,
                     progress_callback: Optional[Callable[[str], None]] = None,
                     chunk_length_ms: int = CHUNK_LENGTH_MS) -> List[str]:
    """
    Splits an audio file into chunks.
    """
    audio = AudioSegment.from_file(file_path)
    total_length = len(audio)
    chunk_files = []
    for i in range(0, total_length, chunk_length_ms):
        chunk = audio[i:i + chunk_length_ms]
        base_name = os.path.splitext(os.path.basename(file_path))[0]
        chunk_filename = os.path.join(temp_dir, f"{base_name}_chunk_{i // chunk_length_ms}.mp3")
        chunk.export(chunk_filename, format="mp3")
        chunk_files.append(chunk_filename)
        if progress_callback:
            progress_callback(f"Created chunk: {chunk_filename}")
        logging.info(f"Created chunk file: {chunk_filename}")
    return chunk_files

def remove_files(file_paths: List[str]) -> None:
    """
    Removes each file in file_paths if it exists.
    """
    for path in file_paths:
        if os.path.exists(path):
            os.remove(path)
            logging.info(f"Removed file: {path}")

def validate_file_path(file_path: str, temp_dir: str) -> bool:
    """
    Validates that the file path is within the designated temporary directory.
    """
    abs_temp_dir = os.path.abspath(temp_dir)
    abs_file_path = os.path.abspath(file_path)
    return abs_file_path.startswith(abs_temp_dir)

def cleanup_old_files(directory: str, threshold: int) -> None:
    """
    Deletes files in the given directory older than the specified threshold (seconds).
    """
    current_time = time.time()
    if not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if os.path.isfile(file_path):
            file_age = current_time - os.path.getctime(file_path)
            if file_age > threshold:
                try:
                    os.remove(file_path)
                    logging.info(f"Deleted old file: {file_path}")
                except Exception as e:
                    logging.exception(f"Error deleting file {file_path}: {e}")
