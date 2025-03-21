# app/services/api_clients/openai_whisper.py

import os
import logging
from typing import Tuple, Optional, Callable
from openai import OpenAI
from app.services import file_service

class OpenAITranscriptionAPI:
    """
    Integration with OpenAI Whisper.
    """
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        logging.info(f"Initialized OpenAITranscriptionAPI with API key: {api_key}")

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        if progress_callback:
            progress_callback(f"Using OpenAI Whisper for transcription of {audio_file_path} with language code {language_code}")
        else:
            logging.info(f"Using OpenAI Whisper for transcription of {audio_file_path} with language code {language_code}")
        client = OpenAI(api_key=self.api_key)
        if os.path.getsize(audio_file_path) > 25 * 1024 * 1024:
            if progress_callback:
                progress_callback("File size exceeds OpenAI limit. Splitting audio file.")
            else:
                logging.info("File size exceeds OpenAI limit. Splitting audio file.")
            return self.split_and_transcribe(audio_file_path, language_code, progress_callback)
        else:
            abs_path = os.path.abspath(audio_file_path)
            temp_dir = os.path.dirname(audio_file_path)
            if not file_service.validate_file_path(abs_path, temp_dir):
                message = "Audio file path is not within the allowed directory"
                logging.error(message)
                raise ValueError(message)
            with open(abs_path, "rb") as audio_file:
                if language_code == 'auto':
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
                    detected_language = 'en'
                elif language_code in ['en', 'nl', 'fr', 'es']:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file, language=language_code)
                    detected_language = language_code
                else:
                    message = f"Invalid language code for OpenAI Whisper: {language_code}"
                    if progress_callback:
                        progress_callback(message)
                    logging.error(message)
                    raise ValueError(message)
                transcription_text = transcript.text
                if progress_callback:
                    progress_callback(f"OpenAI detected language: {detected_language}")
                return transcription_text, detected_language

    def split_and_transcribe(self, audio_file_path: str, language_code: str,
                             progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        if progress_callback:
            progress_callback(f"Splitting audio file: {audio_file_path}")
        else:
            logging.info(f"Splitting audio file: {audio_file_path}")
        temp_dir = os.path.dirname(audio_file_path)
        chunk_files = file_service.split_audio_file(audio_file_path, temp_dir, progress_callback)
        client = OpenAI(api_key=self.api_key)
        transcription_texts = []
        total_chunks = len(chunk_files)
        for idx, chunk_path in enumerate(chunk_files):
            if progress_callback:
                progress_callback(f"Transcribing chunk {idx+1} of {total_chunks}: {chunk_path}")
            else:
                logging.info(f"Transcribing chunk: {chunk_path}")
            with open(chunk_path, "rb") as audio_file:
                if language_code == 'auto':
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file)
                    detected_language = 'en'
                elif language_code in ['en', 'nl', 'fr', 'es']:
                    transcript = client.audio.transcriptions.create(model="whisper-1", file=audio_file, language=language_code)
                    detected_language = language_code
                else:
                    message = f"Invalid language code for OpenAI Whisper: {language_code}"
                    if progress_callback:
                        progress_callback(message)
                    logging.error(message)
                    raise ValueError(message)
                transcription_texts.append(transcript.text)
        file_service.remove_files(chunk_files)
        return " ".join(transcription_texts), detected_language
