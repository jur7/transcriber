# app/services/file_service.py

import os
import time
import logging
from typing import List, Callable, Optional
from pydub import AudioSegment, exceptions as pydub_exceptions

ALLOWED_EXTENSIONS = {'mp3', 'm4a', 'wav', 'ogg', 'webm'}
# Constant for chunk length: 10 minutes in milliseconds
CHUNK_LENGTH_MS = 10 * 60 * 1000
# Maximum file size for OpenAI APIs (25MB) - Moved here
OPENAI_MAX_FILE_SIZE = 25 * 1024 * 1024
# Files to ignore during cleanup
IGNORE_FILES = {'.DS_Store', '.gitkeep'}

def allowed_file(filename: str) -> bool:
    """Checks if the file extension is allowed."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def ordinal(n: int) -> str:
    """Returns the ordinal string for a number (e.g., 1st, 2nd, 3rd)."""
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"

def split_audio_file(file_path: str, temp_dir: str,
                     progress_callback: Optional[Callable[[str, bool], None]] = None,
                     chunk_length_ms: int = CHUNK_LENGTH_MS) -> List[str]:
    """Splits an audio file into chunks, reporting progress via callback."""
    base_name_orig = os.path.basename(file_path)

    try:
        # Add explicit logging for pydub loading attempt (console only)
        logging.info(f"[SYSTEM] Loading audio file '{base_name_orig}' for splitting...")
        audio = AudioSegment.from_file(file_path)
        logging.info(f"[SYSTEM] Successfully loaded '{base_name_orig}'. Duration: {len(audio) / 1000:.2f}s")
    except pydub_exceptions.CouldntDecodeError as cde:
        # SIMPLE UI ERROR MESSAGE
        msg = f"ERROR: Could not decode audio file '{base_name_orig}'. Ensure ffmpeg is installed and file is valid."
        if progress_callback: progress_callback(msg, True)
        logging.error(f"[SYSTEM] {msg} Details: {cde}") # Console log with details
        return []
    except FileNotFoundError:
        # SIMPLE UI ERROR MESSAGE
        msg = f"ERROR: Audio file not found at '{file_path}'"
        if progress_callback: progress_callback(msg, True)
        logging.error(f"[SYSTEM] {msg}") # Console log
        return []
    except Exception as e: # Catch other potential pydub errors
        # SIMPLE UI ERROR MESSAGE
        msg = f"ERROR: Failed loading audio file '{base_name_orig}': {e}"
        if progress_callback: progress_callback(msg, True)
        logging.error(f"[SYSTEM] {msg}", exc_info=True) # Console log with traceback
        return []

    total_length = len(audio)
    chunk_files = []
    chunk_index = 1
    num_chunks = (total_length + chunk_length_ms - 1) // chunk_length_ms # Calculate total chunks

    base_name_no_ext = os.path.splitext(base_name_orig)[0]

    if progress_callback:
        # SIMPLE UI MESSAGE
        progress_callback(f"Splitting into {num_chunks} chunks...", False)

    for i in range(0, total_length, chunk_length_ms):
        start_ms = i
        end_ms = min(i + chunk_length_ms, total_length)
        chunk = audio[start_ms:end_ms]

        chunk_filename_base = f"{base_name_no_ext}_chunk_{chunk_index}.mp3"
        chunk_filename_full = os.path.join(temp_dir, chunk_filename_base)

        try:
            # Log export attempt (console only)
            logging.info(f"[SYSTEM] Exporting chunk {chunk_index}/{num_chunks} to '{chunk_filename_base}'...")
            chunk.export(chunk_filename_full, format="mp3")
            chunk_files.append(chunk_filename_full)

            # Report progress via callback - SIMPLE UI MESSAGE
            message = f"Created {ordinal(chunk_index)} audio chunk of {num_chunks}"
            if progress_callback:
                progress_callback(message, False)
            # Also log system-level success for this chunk (console only)
            logging.info(f"[SYSTEM] Successfully exported chunk {chunk_index}/{num_chunks}: '{chunk_filename_base}'")
            chunk_index += 1

        except Exception as e:
             # Report error via callback - SIMPLE UI ERROR MESSAGE
             msg = f"ERROR: Failed exporting audio chunk {chunk_index}: {e}"
             if progress_callback: progress_callback(msg, True)
             # Also log system-level error (console only)
             logging.error(f"[SYSTEM] Error exporting audio chunk {chunk_index} ('{chunk_filename_base}'): {e}", exc_info=True)
             # Stop splitting and cleanup already created chunks for this job
             logging.warning(f"[SYSTEM] Aborting split process for '{base_name_orig}' due to export error.")
             remove_files(chunk_files) # Clean up chunks created so far
             return [] # Return empty list to indicate failure

    logging.info(f"[SYSTEM] Finished splitting '{base_name_orig}' into {len(chunk_files)} chunks.")
    return chunk_files

def remove_files(file_paths: List[str]) -> int:
    """Removes a list of files, logging actions and errors. Returns count of successfully removed files."""
    removed_count = 0
    # No UI messages sent from here

    for path in file_paths:
        file_basename = os.path.basename(path)
        try:
            if os.path.exists(path):
                os.remove(path)
                # Use INFO level for successful removal (console only)
                logging.info(f"[SYSTEM] Removed temp file: {file_basename}")
                removed_count += 1
            else:
                # Use DEBUG level if file was already gone (console only)
                logging.debug(f"[SYSTEM] Temp file already removed: {file_basename}")
        except OSError as e:
            # Log error during removal (console only)
            logging.error(f"[SYSTEM] Error removing file '{file_basename}': {e}")
        except Exception as e:
             logging.exception(f"[SYSTEM] Unexpected error removing file '{file_basename}': {e}")
    return removed_count


def validate_file_path(file_path: str, allowed_dir: str) -> bool:
    """Validates that a file path is within an allowed directory."""
    try:
        abs_allowed_dir = os.path.abspath(allowed_dir)
        abs_file_path = os.path.abspath(file_path)
        # Ensure commonpath returns the allowed directory itself, preventing traversal
        is_valid = os.path.commonpath([abs_allowed_dir, abs_file_path]) == abs_allowed_dir
        if not is_valid:
             logging.warning(f"[SYSTEM] Path validation failed: '{file_path}' is outside allowed directory '{allowed_dir}'.")
        return is_valid
    except ValueError:
        # Handle cases where paths might be on different drives on Windows, etc.
        logging.warning(f"[SYSTEM] Path validation error for '{file_path}' against '{allowed_dir}'.")
        return False


def cleanup_old_files(directory: str, threshold_seconds: int) -> int:
    """
    Cleans up files older than threshold_seconds in the specified directory.
    Logs actions and returns the count of deleted files.
    """
    deleted_count = 0
    if not os.path.exists(directory):
        logging.warning(f"[SYSTEM] Cleanup directory not found: {directory}")
        return 0 # Nothing to delete

    current_time = time.time()
    logging.info(f"[SYSTEM] Starting cleanup scan in directory: {directory}")

    try:
        for filename in os.listdir(directory):
            # Skip ignored files
            if filename in IGNORE_FILES:
                logging.debug(f"[SYSTEM] Skipping ignored file: {filename}")
                continue

            file_path = os.path.join(directory, filename)

            try:
                # Check if it's a file (and not a directory or symlink) before stating
                if os.path.isfile(file_path):
                    file_stat = os.stat(file_path)
                    file_age = current_time - file_stat.st_mtime

                    if file_age > threshold_seconds:
                        # Console log
                        logging.info(f"[SYSTEM] Deleting old file: {filename} (Age: {file_age:.0f}s)")
                        # Check existence again right before removing (mitigate race condition)
                        if os.path.exists(file_path):
                            os.remove(file_path)
                            logging.info(f"[SYSTEM] Successfully deleted old file: {filename}")
                            deleted_count += 1
                        else:
                            logging.warning(f"[SYSTEM] Old file '{filename}' already removed (likely concurrent process).")
                    # else: # Optional: Debug log for files checked but not old enough
                    #    logging.debug(f"[SYSTEM] Keeping file: {filename} (Age: {file_age:.0f}s)")

            except FileNotFoundError:
                # Catch error if file is removed between listdir and stat/isfile/remove
                logging.warning(f"[SYSTEM] File not found during cleanup scan (likely removed concurrently): {filename}")
            except OSError as e:
                # Catch other potential errors like permission issues during stat/remove
                logging.error(f"[SYSTEM] OS error processing file '{filename}' during cleanup: {e}")
            except Exception as e:
                # Catch any other unexpected errors during file processing
                logging.exception(f"[SYSTEM] Unexpected error processing file '{filename}' during cleanup: {e}")

    except Exception as e:
        # Catch errors during listdir itself
        logging.exception(f"[SYSTEM] Error listing directory '{directory}' during cleanup: {e}")

    logging.info(f"[SYSTEM] Cleanup scan finished for directory: {directory}. Deleted {deleted_count} file(s).")
    return deleted_count