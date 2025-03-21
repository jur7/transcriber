# app/services/api_clients/openai_gpt4o.py 

import os
import logging
from typing import Tuple, Optional, Callable
from openai import OpenAI
from app.services import file_service

class OpenAIGPT4oTranscriptionAPI:
    """
    Integration with OpenAI GPT 4o Transcribe.
    """
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        logging.info(f"Initialized OpenAIGPT4oTranscriptionAPI with API key: {api_key}")

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        if progress_callback:
            progress_callback(f"Using OpenAI GPT 4o Transcribe for {audio_file_path}")
        else:
            logging.info(f"Using OpenAI GPT 4o Transcribe for {audio_file_path}")
        client = OpenAI(api_key=self.api_key)
        if os.path.getsize(audio_file_path) > 25 * 1024 * 1024:
            if progress_callback:
                progress_callback("File size exceeds OpenAI limit. Splitting audio file for GPT 4o Transcription.")
            else:
                logging.info("File size exceeds OpenAI limit. Splitting audio file for GPT 4o Transcription.")
            return self.split_and_transcribe(audio_file_path, language_code, progress_callback)
        else:
            abs_path = os.path.abspath(audio_file_path)
            if not file_service.validate_file_path(abs_path, os.path.dirname(audio_file_path)):
                message = "Audio file path is not within the allowed directory"
                logging.error(message)
                raise ValueError(message)
            with open(abs_path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(model="gpt-4o-transcribe", file=audio_file, response_format="text")
                transcription_text = transcript if isinstance(transcript, str) else transcript.text
                detected_language = language_code if language_code != 'auto' else 'en'
                if progress_callback:
                    progress_callback(f"GPT 4o Transcription completed. Detected/assumed language: {detected_language}")
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
                transcript = client.audio.transcriptions.create(model="gpt-4o-transcribe", file=audio_file, response_format="text")
                text = transcript if isinstance(transcript, str) else transcript.text
                transcription_texts.append(text)
        file_service.remove_files(chunk_files)
        detected_language = language_code if language_code != 'auto' else 'en'
        return " ".join(transcription_texts), detected_language
