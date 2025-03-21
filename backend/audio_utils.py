#!/usr/bin/env python3
"""
Utility functions for audio file manipulation.
"""

import os
from typing import Callable, List, Optional

from pydub import AudioSegment

# Constant for chunk length (10 minutes in milliseconds)
CHUNK_LENGTH_MS: int = 10 * 60 * 1000  # 10 minutes


def split_audio_file(
    file_path: str,
    temp_dir: str,
    progress_callback: Optional[Callable[[str], None]] = None,
    chunk_length_ms: int = CHUNK_LENGTH_MS
) -> List[str]:
    """
    Splits an audio file into chunks of fixed duration.

    Args:
        file_path: The path to the input audio file.
        temp_dir: The directory where chunk files are to be stored.
        progress_callback: Optional callback for progress messages.
        chunk_length_ms: Duration for each chunk in milliseconds (default 10 min).

    Returns:
        A list of file paths to the generated audio chunks.
    """
    audio: AudioSegment = AudioSegment.from_file(file_path)
    total_length: int = len(audio)
    chunk_files: List[str] = []
    for i in range(0, total_length, chunk_length_ms):
        chunk: AudioSegment = audio[i:i + chunk_length_ms]
        chunk_filename: str = os.path.join(
            temp_dir,
            f"{os.path.splitext(os.path.basename(file_path))[0]}_chunk_{i // chunk_length_ms}.mp3"
        )
        chunk.export(chunk_filename, format="mp3")
        chunk_files.append(chunk_filename)
        if progress_callback:
            progress_callback(f"Created chunk: {chunk_filename}")
    return chunk_files


def remove_files(file_paths: List[str]) -> None:
    """
    Removes files from disk if they exist.

    Args:
        file_paths: A list of file paths to remove.
    """
    for path in file_paths:
        if os.path.exists(path):
            os.remove(path)


def validate_file_path(file_path: str, temp_dir: str) -> bool:
    """
    Validates that a file path is located within the given temporary directory.

    Args:
        file_path: The file path to check.
        temp_dir: The temporary uploads directory.

    Returns:
        True if the absolute file_path starts with the absolute temp_dir, otherwise False.
    """
    abs_temp_dir: str = os.path.abspath(temp_dir)
    abs_file_path: str = os.path.abspath(file_path)
    return abs_file_path.startswith(abs_temp_dir)
