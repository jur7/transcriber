# app/services/file_service.py

import os
import time
import logging
import json, subprocess, shlex, re
from pathlib import Path
from typing import List, Callable, Optional
from pydub import AudioSegment, exceptions as pydub_exceptions

# Allowed audio extensions
ALLOWED_AUDIO_EXTENSIONS = {'mp3', 'm4a', 'wav', 'ogg', 'webm'}
# Allowed video extensions (audio will be extracted via ffmpeg)
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'mov', 'avi', 'mkv', 'flv', 'wmv', 'webm'}
# Extensions that can be directly copied without re-encoding
#  (handled by ffmpeg segment muxer)
DIRECT_COPY_EXTENSIONS = "mp3, m4a, wav"
# Constant for chunk length: 500 seconds in milliseconds
CHUNK_LENGTH_MS = 500 * 1000
# Minimum chunk length: 20 seconds in milliseconds
CHUNK_MIN_LENGTH_MS = 20 * 1000
# Parameters for smart chunk splitting
CHUNK_SPLIT_BACK_WINDOW_SEC = 45 # seconds
CHUNK_SPLIT_FORWARD_WINDOW_SEC = 15 # seconds
CHUNK_SPLIT_MIN_SILENCE_DUR = 0.65  # seconds
CHUNK_SPLIT_NOISE_DB = -30.0  # dB
#CHUNK_SPLIT_MIN_DB = -35.0  # dB
CHUNK_SPLIT_USE_DEEP_SEARCH = True
CHUNK_SPLIT_STEPS_DB = [-20.0, -25.0, -30.0, -35.0, -40.0, -45.0]
CHUNK_SPLIT_STEP_MAX_DB = -20 #CHUNK_SPLIT_STEPS_DB[0]
CHUNK_SPLIT_STEP_MIN_DB = -45 #CHUNK_SPLIT_STEPS_DB[-1]

CHUNK_SPLIT_SILENCE_PERCENT_MIN = 3.0  # min % of silence in window to consider
CHUNK_SPLIT_SILENCE_PERCENT_MAX = 25.0  # max % of silence in window to consider

# Maximum file size for OpenAI APIs (25MB) - Moved here
OPENAI_MAX_FILE_SIZE = 25 * 1024 * 1024
# Maximum input length = 1500 sec for o4-transcribe AND 2048 output tokens
#OPENAI_MAX_LENGTH_MS_O4 = 1400 * 1000
OPENAI_MAX_LENGTH_MS_4O = CHUNK_LENGTH_MS


# Files to ignore during cleanup
IGNORE_FILES = {'.DS_Store', '.gitkeep'}

def is_audio_file(filename: str) -> bool:
    """Returns True if the file looks like supported audio."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_AUDIO_EXTENSIONS


def is_video_file(filename: str) -> bool:
    """Returns True if the file looks like supported video."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_VIDEO_EXTENSIONS


def allowed_file(filename: str) -> bool:
    """Checks if the file extension is allowed (audio or video)."""
    if '.' not in filename:
        return False
    ext = filename.rsplit('.', 1)[1].lower()
    return (ext in ALLOWED_AUDIO_EXTENSIONS) or (ext in ALLOWED_VIDEO_EXTENSIONS)

def file_extension(filename: str) -> str:
    """Returns the file extension of a filename."""
    if "."  in filename:
      return filename.rsplit('.', 1)[1]
    return ""


def extract_audio_from_video(input_path: str,
                             output_dir: str,
                             progress_callback: Optional[Callable[[str, bool], None]] = None,
                             audio_ext: str = "mp3") -> Optional[str]:
    """
    Extracts the audio track from a video file using ffmpeg and saves it
    as an audio file (default: mp3). Returns the output path on success or
    None on failure.

    The output filename reuses the input base name with the new extension,
    written to `output_dir`.
    """
    try:
        base = os.path.splitext(os.path.basename(input_path))[0]
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{base}.{audio_ext}")

        # If an old file exists with the same name, remove it to avoid mixups
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception:
                pass

        # Common, broadly compatible audio extraction settings
        # -vn drop video; set stereo 2ch, 44.1kHz for good compatibility
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-y",
            "-i", input_path,
            "-vn",
            "-ac", "2",
            "-ar", "44100",
        ]
        # Choose codec/bitrate by target extension
        if audio_ext.lower() == "mp3":
            cmd += ["-c:a", "libmp3lame", "-b:a", "192k"]
        elif audio_ext.lower() in ("m4a", "aac"):
            cmd += ["-c:a", "aac", "-b:a", "192k"]
        elif audio_ext.lower() == "wav":
            cmd += ["-c:a", "pcm_s16le"]
        else:
            # Default to mp3 if unknown target
            cmd += ["-c:a", "libmp3lame", "-b:a", "192k"]
            output_path = os.path.join(output_dir, f"{base}.mp3")

        cmd += [output_path]

        if progress_callback:
            progress_callback("Extracting audio from video...", False)
        logging.info(f"[SYSTEM] Extracting audio via ffmpeg: '{os.path.basename(input_path)}' -> '{os.path.basename(output_path)}'")

        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            err = (result.stderr or "ffmpeg failed").strip()
            msg = f"ERROR: Audio extraction failed: {err}"
            if progress_callback:
                progress_callback(msg, True)
            logging.error(f"[SYSTEM] {msg}")
            return None

        if not os.path.exists(output_path):
            msg = "ERROR: Audio extraction did not produce an output file."
            if progress_callback:
                progress_callback(msg, True)
            logging.error(f"[SYSTEM] {msg}")
            return None

        if progress_callback:
            progress_callback(f"Audio extracted: {os.path.basename(output_path)}", False)
        logging.info(f"[SYSTEM] Audio extracted successfully: '{os.path.basename(output_path)}'")
        return output_path

    except FileNotFoundError:
        msg = "ERROR: ffmpeg is not installed or not found in PATH."
        if progress_callback:
            progress_callback(msg, True)
        logging.error(f"[SYSTEM] {msg}")
        return None
    except Exception as e:
        msg = f"ERROR: Unexpected error extracting audio: {e}"
        if progress_callback:
            progress_callback(msg, True)
        logging.exception(f"[SYSTEM] {msg}")
        return None


def ordinal(n: int) -> str:
    """Returns the ordinal string for a number (e.g., 1st, 2nd, 3rd)."""
    if 10 <= n % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f"{n}{suffix}"


def get_audio_file_length(file_path: str) -> int:
    """Returns the length of the audio file in milliseconds."""
    audio_len = get_audio_file_length_fast(file_path)
    if audio_len == 0:
        audio_len = get_audio_file_length_slow(file_path)
    return audio_len


def get_audio_file_length_fast(audio_file_path: str) -> int:
    audio_len = 0
    if Path(audio_file_path).is_file():
        # Use ffprobe to get duration in seconds, convert to milliseconds
        cmd = f'ffprobe -v error -print_format json -show_format "{audio_file_path}"'
        try:
            data = subprocess.check_output(shlex.split(cmd))
            info = json.loads(data)
            audio_len = round(float(info["format"]["duration"]) * 1000)
        except subprocess.CalledProcessError as e:
            logging.info(f"[SYSTEM] Error executing ffprobe: {e}")
        except json.JSONDecodeError as e:
            logging.info(f"[SYSTEM] Error parsing ffprobe output: {e}")
        except KeyError as e:       
            logging.info(f"[SYSTEM] Error retrieving duration from ffprobe output: {e}")
    
    return audio_len# miliseconds


def get_audio_file_length_slow(file_path: str) -> int:
    base_name_orig = os.path.basename(file_path)

    try:
        # Add explicit logging for pydub loading attempt (console only)
        logging.info(f"[SYSTEM] Loading audio file '{base_name_orig}' for checking...")
        audio = AudioSegment.from_file(file_path)
        logging.info(f"[SYSTEM] Successfully loaded '{base_name_orig}'. Duration: {len(audio) / 1000:.2f}s")
    except pydub_exceptions.CouldntDecodeError as cde:
        # SIMPLE UI ERROR MESSAGE
        msg = f"ERROR: Could not decode audio file '{base_name_orig}'. Ensure ffmpeg is installed and file is valid."
        return 0
    except FileNotFoundError:
        # SIMPLE UI ERROR MESSAGE
        msg = f"ERROR: Audio file not found at '{file_path}'"
        return 0
    except Exception as e: # Catch other potential pydub errors
        # SIMPLE UI ERROR MESSAGE
        msg = f"ERROR: Failed loading audio file '{base_name_orig}': {e}"
        return 0

    total_length = len(audio)
    return total_length



def split_audio_file(file_path: str, temp_dir: str,
                     progress_callback: Optional[Callable[[str, bool], None]] = None,
                     chunk_length_ms: int = CHUNK_LENGTH_MS,
                     chunk_direct_format: str = "mp3,m4a,webm") -> List[str]:
    """Splits an audio file into chunks, reporting progress via callback."""
    # Extract file extension from file_path
    chunks = []
    ext = file_extension(file_path).lower()
    # Direct conversion using ffmpeg segment muxer if format matches
    if ext in ALLOWED_AUDIO_EXTENSIONS and ext in chunk_direct_format:
        # s
        total_len_ms = get_audio_file_length(file_path)
        cut_points = []

        # Compute cut points (seconds)
        if progress_callback:
            # SIMPLE UI MESSAGE
            progress_callback(f"Preparing splitting file. Silence detection...", False)

#        if total_len_ms > chunk_length_ms + CHUNK_MIN_LENGTH_MS:
        if CHUNK_SPLIT_USE_DEEP_SEARCH:
            cut_points = compute_smart_segment_times_deep(file_path, chunk_length_ms=CHUNK_LENGTH_MS,
                back_window_sec=CHUNK_SPLIT_BACK_WINDOW_SEC,
                forward_window_sec=CHUNK_SPLIT_FORWARD_WINDOW_SEC,
                noise_db=CHUNK_SPLIT_NOISE_DB,
                min_silence_dur=CHUNK_SPLIT_MIN_SILENCE_DUR
            )
        else:    
            cut_points = compute_smart_segment_times(file_path, chunk_length_ms=CHUNK_LENGTH_MS,
                back_window_sec=CHUNK_SPLIT_BACK_WINDOW_SEC,
                forward_window_sec=CHUNK_SPLIT_FORWARD_WINDOW_SEC,
                noise_db=CHUNK_SPLIT_NOISE_DB,
                min_silence_dur=CHUNK_SPLIT_MIN_SILENCE_DUR
            )       
        if cut_points:
            # Log system message (console only)
            msg = f"Computed {len(cut_points)} cut points for split"
            msg += "(sec): " + ", ".join(f"{cp/1000:.2f}" for cp in cut_points)
            logging.info(f"[SYSTEM] {msg}")
            # Prepare output pattern

            base_name_orig = os.path.basename(file_path)
            base_name_no_ext = os.path.splitext(base_name_orig)[0]
            chunk_filename_pattern = base_name_no_ext + "_chunk_%02d" + "." + ext
    
            if progress_callback:
                # SIMPLE UI MESSAGE
                progress_callback(f"Splitting into {len(cut_points)+1} chunks...", False)
            # Use absolute path for output directory        

            out_dir = os.path.abspath(temp_dir)
            parts = split_audio_file_fast_ffmpeg(file_path, out_dir, cut_points, progress_callback, chunk_filename_pattern)        
            if parts:
                chunks.extend(parts)
                return chunks
            else:
                msg = "Fast ffmpeg split failed; falling back to pydub method."
                logging.warning(f"[SYSTEM] {msg}")

        else :
            msg = "No cut points found; file may be shorter than chunk length."
            logging.info(f"[SYSTEM] {msg}")
#            return [file_path] # Return original file as single chunk


    # Fallback to pydub and slow conversion method to mp3
    if not chunks:
        chunks = split_audio_file_pydup(file_path, temp_dir, progress_callback, chunk_length_ms, "mp3")

    return chunks



def split_audio_file_pydup(file_path: str, temp_dir: str,
                     progress_callback: Optional[Callable[[str, bool], None]] = None,
                     chunk_length_ms: int = CHUNK_LENGTH_MS,
                     chunk_format: str = "mp3") -> List[str]:
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

        chunk_filename_base = f"{base_name_no_ext}_chunk_{chunk_index}." + chunk_format
        chunk_filename_full = os.path.join(temp_dir, chunk_filename_base)

        try:
            # Log export attempt (console only)
            logging.info(f"[SYSTEM] Exporting chunk {chunk_index}/{num_chunks} to '{chunk_filename_base}'...")
            chunk.export(chunk_filename_full, format=chunk_format)
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


def split_audio_file_fast_ffmpeg(
    input_file: str,
    output_dir: str,
    segment_times_ms: List[int],
    progress_callback: Optional[Callable[[str, bool], None]] = None,
    output_pattern: Optional[str] = None,
) -> List[str]:
    """
    Quickly splits an audio file into chunks using ffmpeg's segment muxer without re-encoding.

    Calls ffmpeg with flags equivalent to:
      ffmpeg -i Audio_20250724.m4a -f segment -segment_times 600000,1200000,... \
             -c copy -reset_timestamps 1 -fflags +bitexact -flags:v +bitexact -flags:a +bitexact "part_%02d.m4a"

    Args:
        input_file: Path to the source audio file.
        output_dir: Directory where chunk files will be written.
        segment_times_ms: List of split times in milliseconds (e.g., [600000, 1200000, ...]).
        progress_callback: Optional progress reporter (message, is_error).
        output_pattern: Optional basename pattern like "part_%02d.ext". Defaults to source extension.

    Returns:
        List of created chunk file paths in index order. Empty list on failure.
    """
    base_name = os.path.basename(input_file)
    abs_input = os.path.abspath(input_file)
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(abs_input):
        msg = f"ERROR: Audio file not found at path: {abs_input}"
        if progress_callback: progress_callback(msg, True)
        logging.error(f"[SYSTEM] {msg}")
        return []

    if not validate_file_path(abs_input, os.path.dirname(abs_input)):
        msg = f"ERROR: Audio file path is not allowed: {abs_input}"
        if progress_callback: progress_callback(msg, True)
        logging.error(f"[SYSTEM] {msg}")
        return []

    if not segment_times_ms or any(t <= 0 for t in segment_times_ms):
        msg = "ERROR: segment_times_ms must be a non-empty list of positive milliseconds."
        if progress_callback: progress_callback(msg, True)
        logging.error(f"[SYSTEM] {msg}")
        return []

    seg_str = ",".join(str(float(t/1000)) for t in segment_times_ms)
    src_ext = os.path.splitext(base_name)[1].lower() or ".m4a"
    pattern = output_pattern or f"part_%02d{src_ext}"
    out_pattern_path = os.path.join(output_dir, pattern)

    # Build ffmpeg command
    cmd = [
        "ffmpeg",
        "-hide_banner", "-loglevel", "error",
        "-y",
        "-i", abs_input,
        "-f", "segment",
        "-segment_times", seg_str,
        "-c", "copy",
        "-reset_timestamps", "1",
        "-fflags", "+bitexact",
        "-flags:v", "+bitexact",
        "-flags:a", "+bitexact",
        out_pattern_path,
    ]

    # Inform UI
    ui_msg = f"Fast splitting '{base_name}' via ffmpeg at {len(segment_times_ms)} cut points..."
 #   if progress_callback: progress_callback(ui_msg, False)
    logging.info(f"[SYSTEM] {ui_msg}")

    try:
        start = time.time()
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        duration = time.time() - start
        if result.returncode != 0:
            err = result.stderr.strip() or "ffmpeg returned non-zero exit code."
            msg = f"ERROR: ffmpeg split failed for '{base_name}': {err}"
            if progress_callback: progress_callback(msg, True)
            logging.error(f"[SYSTEM] {msg}")
            return []
        logging.info(f"[SYSTEM] ffmpeg split completed in {duration:.2f}s")
    except FileNotFoundError:
        msg = "ERROR: ffmpeg is not installed or not found in PATH."
        if progress_callback: progress_callback(msg, True)
        logging.error(f"[SYSTEM] {msg}")
        return []
    except Exception as e:
        msg = f"ERROR: Unexpected error running ffmpeg: {e}"
        if progress_callback: progress_callback(msg, True)
        logging.exception(f"[SYSTEM] {msg}")
        return []

    # Collect expected output files in order
    expected_count = len(segment_times_ms) + 1
    created_files: List[str] = []
    for i in range(expected_count):
        try:
            # Use printf-style pattern expansion
            rel_name = (pattern % i) if "%" in pattern else f"part_{i:02d}{src_ext}"
        except TypeError:
            rel_name = f"part_{i:02d}{src_ext}"
        full_path = os.path.join(output_dir, rel_name)
        if os.path.exists(full_path):
            created_files.append(full_path)
        else:
            logging.warning(f"[SYSTEM] Expected chunk missing: {rel_name}")

    if progress_callback:
        progress_callback(f"Created {len(created_files)} chunk file(s).", False)

    return created_files


def detect_silences_ffmpeg(
    input_file: str,
    noise_db: float = CHUNK_SPLIT_NOISE_DB,
    min_silence_dur: float = CHUNK_SPLIT_MIN_SILENCE_DUR,
    start_time = 0.0,
    finish_time = 0.0,  
) -> List[dict]:
    """
    Detects silence intervals using ffmpeg silencedetect filter.

    Equivalent shell example:
      ffmpeg -i "input.m4a" -af silencedetect=n=-20dB:d=0.65 -f null - 2>&1 | grep 'silence_end'

    Returns a list of dictionaries with keys: start, end, duration (all seconds as float).
    """
    abs_input = os.path.abspath(input_file)
    if not os.path.exists(abs_input):
        logging.error(f"[SYSTEM] ERROR: Audio file not found at path: {abs_input}")
        return []

    # Build ffmpeg command
    filter_arg = f"silencedetect=n={noise_db}dB:d={min_silence_dur}"

    cmd = [
        "ffmpeg",
        "-hide_banner", "-nostats", "-loglevel", "info",
        *(['-ss', str(start_time)] if start_time and start_time >= 0.001 else []),
        *(['-to', str(finish_time)] if finish_time and finish_time > 0.001 and abs(finish_time - start_time) > min_silence_dur else []),
        "-i", abs_input,
        "-af", filter_arg,
        "-f", "null", "-",
    ]
    # jur - debug
    # print(cmd )

    try:
        # Capture stderr where silencedetect writes its logs
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        logging.error("[SYSTEM] ERROR: ffmpeg is not installed or not found in PATH.")
        return []
    except Exception as e:
        logging.exception(f"[SYSTEM] ERROR: Unexpected error running ffmpeg silencedetect: {e}")
        return []

    stderr = proc.stderr or ""
    # Regex to capture silence_end and duration
    end_re = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)\s*\|\s*silence_duration:\s*([0-9]+(?:\.[0-9]+)?)")

    silences: List[dict] = []
    for line in stderr.splitlines():
        m = end_re.search(line)
        if not m:
            continue
        end_s = start_time + float(m.group(1))
        dur_s = float(m.group(2))
        start_s = max(0.0, end_s - dur_s)
        silences.append({
            "start": start_s,
            "end": end_s,
            "duration": dur_s,
        })

    logging.info(f"[SYSTEM] Silence detection: found {len(silences)} intervals (n={noise_db}dB, d={min_silence_dur}s)")
    return silences


def compute_smart_segment_times(
    input_file: str,
    chunk_length_ms: int = CHUNK_LENGTH_MS,
    back_window_sec: int = CHUNK_SPLIT_BACK_WINDOW_SEC,
    forward_window_sec: int = CHUNK_SPLIT_FORWARD_WINDOW_SEC,
    noise_db: float = CHUNK_SPLIT_NOISE_DB,
    min_silence_dur: float = CHUNK_SPLIT_MIN_SILENCE_DUR,
) -> List[int]:
    """
    Computes smart segment cut points (in seconds) near detected silences.

    Steps:
      a) Run silence detection via ffmpeg.
      b) Plan nominal cut points at CHUNK_LENGTH_MS increments.
      c) For each nominal point P, search for the longest silence whose end timestamp
         lies within [P-100s, P+15s]. Adjust P to that silence end.
      d) If none found, keep nominal P.

    Returns list of increasing cut points in seconds suitable for ffmpeg -segment_times.
    """
    abs_input = os.path.abspath(input_file)
    if not os.path.exists(abs_input):
        logging.error(f"[SYSTEM] ERROR: Audio file not found at path: {abs_input}")
        return []

    total_len_ms = get_audio_file_length(abs_input)
    if total_len_ms <= 0:
        logging.error(f"[SYSTEM] ERROR: Could not determine audio length for: {abs_input}")
        return []


    # Prepare nominal cut points in ms
    nominal_points_ms: List[int] = []
    p = chunk_length_ms
    while p < total_len_ms:
        nominal_points_ms.append(p)
        p += chunk_length_ms

    # If no planned points, nothing to compute
    if not nominal_points_ms:
        return []


    # Silence intervals (seconds)
    silences = detect_silences_ffmpeg(abs_input, noise_db=noise_db, min_silence_dur=min_silence_dur)

    # Build adjusted points
    adjusted_points_ms: List[int] = []
    last_cut_ms = 0
    back_ms = back_window_sec * 1000
    fwd_ms = forward_window_sec * 1000

    # Pre-compute silence end times in ms and duration
    silence_candidates = [
        {
            "end_ms": int(s["end"] * 1000),
            "duration_ms": int(s["duration"] * 1000)
        } for s in silences
    ]

    for nominal in nominal_points_ms:
        win_start = max(0, nominal - back_ms)
        win_end = nominal + fwd_ms
        # Find silence ends within window
        best = None
        best_dur = -1
        for s in silence_candidates:
            end_ms = s["end_ms"]
            dur_ms = s["duration_ms"]
            if end_ms < last_cut_ms:
                continue
            if win_start <= end_ms <= win_end:
                if dur_ms > best_dur:
                    best = end_ms
                    best_dur = dur_ms
        cut_ms = nominal if best is None else best - best_dur / 2
        # Ensure strictly increasing and not beyond total length
        cut_ms = max(last_cut_ms + 1, min(cut_ms, total_len_ms - 1))
        adjusted_points_ms.append(cut_ms)
        last_cut_ms = cut_ms

    # Convert to seconds for ffmpeg -segment_times
#    adjusted_points_sec = [int(ms / 1000) for ms in adjusted_points_ms]
    # Ensure strictly increasing seconds (remove duplicates due to int division)
    final_points_ms: List[int] = []
    prev = -1
    for t in adjusted_points_ms:
        if (t > prev) and (t < total_len_ms - CHUNK_MIN_LENGTH_MS):
            final_points_ms.append(t)
            prev = t

    logging.info(f"[SYSTEM] Smart segmentation produced {len(final_points_ms)} cut points (nominal {len(nominal_points_ms)}).")
    return final_points_ms


def compute_smart_segment_times_deep(
    input_file: str,
    chunk_length_ms: int = CHUNK_LENGTH_MS,
    back_window_sec: int = CHUNK_SPLIT_BACK_WINDOW_SEC,
    forward_window_sec: int = CHUNK_SPLIT_FORWARD_WINDOW_SEC,
    noise_db: float = CHUNK_SPLIT_NOISE_DB,
    min_silence_dur: float = CHUNK_SPLIT_MIN_SILENCE_DUR,
) -> List[int]:
    """
    Computes optimal segment cut points (in milliseconds) near detected silences for audio splitting.
    This function aims to find the best cut points for segmenting an audio file by:
        1. Running silence detection (via ffmpeg) in windows around each nominal cut point.
        2. Planning nominal cut points at regular intervals (chunk_length_ms).
        3. For each nominal point, searching for the longest silence whose end timestamp falls within a configurable window around the nominal point ([P-back_window_sec, P+forward_window_sec]).
        4. Adjusting the nominal cut point to the end of the best-matching silence, if found; otherwise, keeping the nominal point.
        5. Iteratively adjusting silence detection parameters (noise_db, min_silence_dur) to improve silence detection if needed.
    Parameters:
        input_file (str): Path to the audio file to segment.
        chunk_length_ms (int): Desired segment length in milliseconds (default: CHUNK_LENGTH_MS).
        back_window_sec (int): Seconds before each nominal cut point to search for silences (default: CHUNK_SPLIT_BACK_WINDOW_SEC).
        forward_window_sec (int): Seconds after each nominal cut point to search for silences (default: 15).
        noise_db (float): Silence threshold in decibels for silence detection (default: -20.0).
        min_silence_dur (float): Minimum duration (in seconds) for a silence to be considered (default: 0.65).
    Returns:
        List[int]: List of strictly increasing cut points (in milliseconds) suitable for use with ffmpeg's -segment_times option.

    """
    abs_input = os.path.abspath(input_file)
    if not os.path.exists(abs_input):
        logging.error(f"[SYSTEM] ERROR: Audio file not found at path: {abs_input}")
        return []

    total_len_ms = get_audio_file_length(abs_input)
    if total_len_ms <= 0:
        logging.error(f"[SYSTEM] ERROR: Could not determine audio length for: {abs_input}")
        return []

    # Prepare nominal cut points in ms
    nominal_points_ms: List[int] = []
    p = chunk_length_ms
    while p < total_len_ms:
        nominal_points_ms.append(p)
        p += chunk_length_ms

    # If no planned points, nothing to compute
    if not nominal_points_ms:
        return []
    
    # Build adjusted points by iterating over nominal points
    adjusted_points_ms: List[int] = []
    for nominal in nominal_points_ms:
        # Default to nominal if no better found
        adjusted_points_ms.append(nominal)
        cut_point_found = False
        prev_db = noise_db
        # Silence intervals (seconds)
        point_start = max(0, nominal/1000 - back_window_sec)
        point_end = nominal/1000 + forward_window_sec
        point_len_ms = int(point_end - point_start) * 1000 
        # start with initial silence detection
        silences = detect_silences_ffmpeg(abs_input, noise_db=noise_db, min_silence_dur=min_silence_dur, start_time=point_start, finish_time=point_end)

        if silences:
            prev_silences = silences
            noise_floor_reached = False
            # 1. Compute percentage of silence in the window   
            silence_percent = compute_silence_percentage_from_intervals(silences, point_len_ms)
            # 1if within target range, just accept the results
            if CHUNK_SPLIT_SILENCE_PERCENT_MIN <= silence_percent <= CHUNK_SPLIT_SILENCE_PERCENT_MAX  or (noise_db - CHUNK_SPLIT_STEP_MIN_DB) < 0.1:
                cut_ms = get_best_silence_candidate(silences, nominal, back_window_sec, forward_window_sec, finish_time=total_len_ms/1000)
                adjusted_points_ms[-1] = cut_ms
                cut_point_found = True
                logging.info(f"[SYSTEM] Adjusted nominal {nominal/1000:.2f}s to {cut_ms/1000:.2f}s (silence {silence_percent:.1f}%)")
            elif silence_percent >= CHUNK_SPLIT_SILENCE_PERCENT_MAX:

                # 2. Too long silences => quiet speech & record => try to search at LOWER decibel levels
                prev_silences = silences
                noise_floor_reached = False

                db = noise_db
                min_db = CHUNK_SPLIT_STEP_MIN_DB # -50.0
                step_db = -5.0
                db += step_db
                while db >= min_db:
                    # Redo silence detection with new db level
                    silences = detect_silences_ffmpeg(abs_input, noise_db=db, min_silence_dur=min_silence_dur, start_time=point_start, finish_time=point_end)
                    if silences or prev_silences:
                        # Use current silences if found, else previous
                        if not silences:
                            silences = prev_silences
                            noise_floor_reached = True
                            # jur - debug
                            #print(f"Noise floor reached at {db}dB")
                        else: prev_silences = silences
                        # Compute percentage of silence in the window       
                        silence_percent = compute_silence_percentage_from_intervals(silences, point_len_ms)
                        # jur - debug
                        #print(f"db={db}, silence_percent={silence_percent}")
                        # check if within target range
                        # if we reached noise floor or @min_db, accept best we can get from previous silences
                        if (CHUNK_SPLIT_SILENCE_PERCENT_MIN <= silence_percent <= CHUNK_SPLIT_SILENCE_PERCENT_MAX) or noise_floor_reached or (db - min_db) < 0.1:
                            cut_ms = get_best_silence_candidate(silences, nominal, back_window_sec, forward_window_sec, finish_time=total_len_ms/1000)
                            adjusted_points_ms[-1] = cut_ms
                            cut_point_found = True
                            if noise_floor_reached: logging.info(f"[SYSTEM] Adjusted nominal {nominal/1000:.2f}s to {cut_ms/1000:.2f}s (silence {silence_percent:.1f}%, {prev_db}dB)")
                            else: logging.info(f"[SYSTEM] Adjusted nominal {nominal/1000:.2f}s to {cut_ms/1000:.2f}s (silence {silence_percent:.1f}%, {db}dB)")

                            break

                    prev_db = db
                    db += step_db
    
        # 3. No silences found or too short => loud speech & record => try to search at HIGHER decibel levels
        if (not silences or silence_percent < CHUNK_SPLIT_SILENCE_PERCENT_MIN) and not cut_point_found:
    
            db = noise_db
            # If initial decibel level < -20, iterate up with the same decibel steps as below
            if noise_db < CHUNK_SPLIT_STEP_MAX_DB + 0.1: #-20.0+0.1
            # If still no silences found or too few, try increasing decibel levels
                db = noise_db
                max_db = CHUNK_SPLIT_STEP_MAX_DB # -20.0
                step_db = 5.0
                db += step_db
                while db <= max_db:
                    # jur - debug
                    #print(f"Trying increasing db levels from {prev_db} to {db}") 
                    silences3 = detect_silences_ffmpeg(abs_input, noise_db=db, min_silence_dur=min_silence_dur, start_time=point_start, finish_time=point_end)
                    if silences3:
                        silence_percent3 = compute_silence_percentage_from_intervals(silences3, point_len_ms)
                        # check if within target range
                        if CHUNK_SPLIT_SILENCE_PERCENT_MIN <= silence_percent3 <= CHUNK_SPLIT_SILENCE_PERCENT_MAX:
                            cut_ms = get_best_silence_candidate(silences3, nominal, back_window_sec, forward_window_sec, finish_time=total_len_ms/1000)
                            adjusted_points_ms[-1] = cut_ms
                            cut_point_found = True
                            logging.info(f"[SYSTEM] Adjusted nominal {nominal/1000:.2f}s to {cut_ms/1000:.2f}s (silence {silence_percent3:.1f}%, {db}dB)")
                            break
                        else:
                            logging.info(f"[SYSTEM] Keeping nominal {nominal/1000:.2f}s (silence {silence_percent3:.1f}%, {db}dB)")

                    prev_db = db
                    db += step_db
#                logging.info(f"[SYSTEM] Keeping nominal {nominal/1000:.2f}s (silence {silence_percent:.1f}%)")

            # 4. We are at max db level of -20dB and still no silences found or too few => probably fast speech => try decreasing min_silence_dur
            # This is only done once, at the initial decibel level of -20dB to limit processing time
            # as decreasing min_silence_dur increases the number of detected silences significantly
            # and thus the processing time for each nominal point
            if (noise_db == CHUNK_SPLIT_STEP_MAX_DB or prev_db == CHUNK_SPLIT_STEP_MAX_DB) and not cut_point_found:
                # fixed db level at -20dB
                db = CHUNK_SPLIT_STEP_MAX_DB # -20.0

                # jur - debug
                #print(f"Trying lower min_silence_dur levels from {min_silence_dur}")
                # Try with min_silence_dur = 0.5s   
                silences2 = detect_silences_ffmpeg(
                    abs_input, noise_db=db, min_silence_dur=0.5, start_time=point_start, finish_time=point_end
                )
                if silences2:
                    silence_percent2 = compute_silence_percentage_from_intervals(silences2, point_len_ms)
                    # jur - debug
                    # print(f"min_silence_dur=0.5, silence_percent2={silence_percent2}")
                    if silence_percent2 < CHUNK_SPLIT_SILENCE_PERCENT_MAX:
                        cut_ms = get_best_silence_candidate(silences2, nominal, back_window_sec, forward_window_sec, finish_time=total_len_ms/1000)
                        adjusted_points_ms[-1] = cut_ms
                        logging.info(f"[SYSTEM] Adjusted nominal {nominal/1000:.2f}s to {cut_ms/1000:.2f}s (silence {silence_percent2:.1f}%, min_silence_dur=0.5)")
                    else: logging.info(f"[SYSTEM] Keeping nominal {nominal/1000:.2f}s (silence {silence_percent2:.1f}%, min_silence_dur=0.5)")

    # Ensure strictly increasing and not beyond total length
    # Remove cuts that are too close to each other
    # or too close to start or end
    last_cut_ms = 0
    final_points_ms: List[int] = []
    for i in range(len(adjusted_points_ms)):
        cut_ms = adjusted_points_ms[i]
        cut_ms = max(last_cut_ms + 1, min(cut_ms, total_len_ms - 1))
        # Only keep cuts that are at least CHUNK_MIN_LENGTH_MS apart
        # and not too close to start or end
        if (cut_ms > last_cut_ms + CHUNK_MIN_LENGTH_MS) and (cut_ms < total_len_ms - CHUNK_MIN_LENGTH_MS):
            final_points_ms.append(cut_ms)
        last_cut_ms = cut_ms
    logging.info(f"[SYSTEM] Smart segmentation produced {len(final_points_ms)} cut points (nominal {len(nominal_points_ms)}).")

    return final_points_ms



def get_best_silence_candidate(
    silences: List[dict],
    nominal_point_ms: int,
    back_window_sec: int = CHUNK_SPLIT_BACK_WINDOW_SEC,
    forward_window_sec: int = CHUNK_SPLIT_FORWARD_WINDOW_SEC,
    finish_time: float = 0.0,
) -> int:
    
    # Silence intervals (seconds)
    if not silences:
        return nominal_point_ms
 
    # Build adjusted points
    last_cut_ms = 0
    back_ms = back_window_sec * 1000
    fwd_ms = forward_window_sec * 1000

    # Pre-compute silence end times in ms and duration
    silence_candidates = [
        {
            "end_ms": int(s["end"] * 1000),
            "duration_ms": int(s["duration"] * 1000)
        } for s in silences
    ]

    win_start = max(0, nominal_point_ms - back_ms)
    win_end = nominal_point_ms + fwd_ms
    # Find silence ends within window
    best = None
    best_dur = -1
    for s in silence_candidates:
        end_ms = s["end_ms"]
        dur_ms = s["duration_ms"]
        if end_ms < last_cut_ms:
            continue
        if win_start <= end_ms <= win_end:
            if dur_ms > best_dur:
                best = end_ms
                best_dur = dur_ms
    cut_ms = nominal_point_ms if best is None else int(best - best_dur / 2)
    # Ensure strictly increasing and not beyond total length
    cut_ms = max(last_cut_ms + 1, min(cut_ms, int(finish_time * 1000) - 1))

    return cut_ms



def get_audio_sample_rate_ffprobe(audio_file_path: str) -> int:
    """
    Returns the sample rate (Hz) of the first audio stream using ffprobe.

    ffprobe command:
      ffprobe -v error -select_streams a:0 -show_entries stream=sample_rate \
              -of default=noprint_wrappers=1:nokey=1 "file"

    Returns 0 on error.
    """
    abs_input = os.path.abspath(audio_file_path)
    if not os.path.exists(abs_input):
        logging.error(f"[SYSTEM] ERROR: Audio file not found at path: {abs_input}")
        return 0

    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        abs_input,
    ]

    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            logging.error(f"[SYSTEM] ffprobe failed to read sample_rate: {result.stderr.strip()}")
            return 0
        out = (result.stdout or "").strip()
        # Handle values like "48000" or "48000/1"
        if "/" in out:
            num, _, den = out.partition("/")
            try:
                num_i = int(num)
                den_i = int(den) if den else 1
                if den_i == 0:
                    return 0
                return int(round(num_i / den_i))
            except ValueError:
                return 0
        # Plain integer string
        return int(out)
    except FileNotFoundError:
        logging.error("[SYSTEM] ERROR: ffprobe is not installed or not found in PATH.")
        return 0
    except Exception as e:
        logging.exception(f"[SYSTEM] ERROR: Unexpected error running ffprobe for sample_rate: {e}")
        return 0


def compute_silence_percentage_from_intervals(
    silences: List[dict],
    total_length_ms: int,
) -> float:
    """
    Computes the percentage of silence given a list of silence intervals
    from detect_silences_ffmpeg and the total audio length in milliseconds.

    The input `silences` is expected to be a list of dicts with keys
    "start", "end", and "duration", where values are in seconds.

    Returns a float in 0..100. Clamps to 0 if total_length_ms <= 0.
    """
    if total_length_ms <= 0:
        logging.warning("[SYSTEM] Total length is non-positive; returning 0% silence.")
        return 0.0

    # Sum durations (seconds -> ms)
    total_silence_ms = 0
    for s in silences or []:
        try:
            dur_ms = max(0.0, float(s.get("duration", 0.0)) * 1000.0)
        except (TypeError, ValueError):
            dur_ms = 0.0
        total_silence_ms += dur_ms

    # Avoid exceeding the full length due to any anomalies
    total_silence_ms = min(total_silence_ms, float(total_length_ms))
    percent = 100.0 * total_silence_ms / float(total_length_ms)
    logging.info(
        f"[SYSTEM] Silence total: {total_silence_ms:.0f} ms of {total_length_ms} ms -> {percent:.2f}%"
    )
    return percent


def compute_silence_percentage_via_ffmpeg(
    input_file: str,
    noise_db: float = CHUNK_SPLIT_NOISE_DB,
    min_silence_dur: float = CHUNK_SPLIT_MIN_SILENCE_DUR,
    start_time: float = 0.0,
    finish_time: float = 0.0,
) -> float:
    """
    Runs detect_silences_ffmpeg on the file and returns total percentage of time
    that is silence according to the given parameters.

    Returns a float in 0..100. Returns 0.0 on error.
    """
    abs_input = os.path.abspath(input_file)
    if not os.path.exists(abs_input):
        logging.error(f"[SYSTEM] ERROR: Audio file not found at path: {abs_input}")
        return 0.0

    total_len_ms = get_audio_file_length(abs_input)
    if total_len_ms <= 0:
        logging.error(f"[SYSTEM] ERROR: Could not determine audio length for: {abs_input}")
        return 0.0

    if finish_time and finish_time > 0.001 and finish_time <= start_time:
        logging.error("[SYSTEM] ERROR: finish_time must be greater than start_time if both are set.")
        return 0.0
    
    if finish_time and finish_time > 0.001 and (finish_time * 1000) > total_len_ms:
        finish_time = total_len_ms / 1000.0
        logging.info(f"[SYSTEM] Adjusted finish_time to audio length: {finish_time:.3f}s")
    # Detect silences

    silences = detect_silences_ffmpeg(
        abs_input, noise_db=noise_db, min_silence_dur=min_silence_dur, start_time=start_time, finish_time=finish_time
    )

    analysis_len_ms = total_len_ms
    # If start/finish provided, adjust analysis length accordingly 
    if start_time and finish_time and finish_time > start_time:
        analysis_len_ms = int((finish_time - start_time) * 1000)
    percent = compute_silence_percentage_from_intervals(silences, analysis_len_ms)
    logging.info(
        f"[SYSTEM] Silence percentage (n={noise_db}dB, d={min_silence_dur}s): {percent:.2f}%"
    )
    return percent


def compute_low_volume_percentage_ffmpeg(
    input_file: str,
    rms_threshold_db: float = -30.0,
    resample_hz: Optional[int] = None,
) -> float:
    """
    Computes percentage of time where Overall RMS level is below a given threshold.

    Uses ffmpeg with astats to emit per-window Overall.RMS_level values and counts
    how many windows fall below `rms_threshold_db`.

    Default behavior avoids resampling and uses 1-second windows by setting
    asetnsamples to the file's native sample rate (queried via ffprobe).

    If `resample_hz` is provided, the audio is resampled to that rate and the
    window size is set to 1 second at that rate.

    Args:
        input_file: Path to audio file.
        rms_threshold_db: Threshold in dBFS (e.g., -30.0). Windows below this are treated as low-volume.
        resample_hz: Optional. If set, resamples audio before analysis; otherwise the file's native rate is used.

    Returns:
        Percentage of time (0..100) considered "low volume" by the threshold. Returns 0.0 on error.
    """
    abs_input = os.path.abspath(input_file)
    if not os.path.exists(abs_input):
        logging.error(f"[SYSTEM] ERROR: Audio file not found at path: {abs_input}")
        return 0.0

    # Determine analysis rate and build the ffmpeg filter chain
    filters = []
    if resample_hz is not None and resample_hz > 0:
        analysis_rate = int(resample_hz)
        filters.append(f"aresample={analysis_rate}")
    else:
        analysis_rate = get_audio_sample_rate_ffprobe(abs_input)
        if analysis_rate <= 0:
            logging.error("[SYSTEM] ERROR: Could not determine sample rate for analysis.")
            return 0.0
    # Use 1-second analysis windows
    filters.append(f"asetnsamples={analysis_rate}")
    filters.append("astats=metadata=1:reset=1")
    filters.append("ametadata=print:key=lavfi.astats.Overall.RMS_level")
    filter_arg = ",".join(filters)

    cmd = [
        "ffmpeg",
        "-hide_banner", "-nostats",
        "-i", abs_input,
        "-af", filter_arg,
        "-f", "null", "-",
    ]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError:
        logging.error("[SYSTEM] ERROR: ffmpeg is not installed or not found in PATH.")
        return 0.0
    except Exception as e:
        logging.exception(f"[SYSTEM] ERROR: Unexpected error running ffmpeg astats: {e}")
        return 0.0

    # astats metadata values printed via ametadata=print go to stdout. Keep stderr for context if needed.
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    pattern = re.compile(r"lavfi\.astats\.Overall\.RMS_level=([-]?[0-9]+(?:\.[0-9]+)?)")

    total = 0
    low = 0
    for line in combined.splitlines():
        m = pattern.search(line)
        if not m:
            continue
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        total += 1
        if v < rms_threshold_db:
            low += 1

    if total == 0:
        logging.warning("[SYSTEM] No RMS_level samples parsed from ffmpeg output; returning 0%.")
        return 0.0

    percent = 100.0 * low / total
    logging.info(
        f"[SYSTEM] Low-volume windows: {low}/{total} below {rms_threshold_db} dBFS -> {percent:.2f}%"
    )
    return percent

