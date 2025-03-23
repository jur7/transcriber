# app/services/api_clients/openai_whisper.py

import os
import logging
import time
from typing import Tuple, Optional, Callable
from openai import OpenAI
from app.services import file_service
from app.config import Config

class OpenAITranscriptionAPI:
    """
    Integration with OpenAI Whisper.
    """
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        logging.info("Initialized OpenAITranscriptionAPI (Whisper).")

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None,
                   context_prompt: str = "") -> Tuple[str, str]:
        if progress_callback:
            progress_callback(f"Transcribing file {audio_file_path} with OpenAI Whisper, language {language_code}")
        else:
            logging.info(f"Transcribing file {audio_file_path} with OpenAI Whisper, language {language_code}")
        client = OpenAI(api_key=self.api_key)
        try:
            if os.path.getsize(audio_file_path) > 25 * 1024 * 1024:
                if progress_callback:
                    progress_callback("File too large – splitting into chunks.")
                else:
                    logging.info("File too large – splitting into chunks.")
                return self.split_and_transcribe(audio_file_path, language_code, progress_callback, context_prompt=context_prompt)
            else:
                abs_path = os.path.abspath(audio_file_path)
                temp_dir = os.path.dirname(audio_file_path)
                if not file_service.validate_file_path(abs_path, temp_dir):
                    raise ValueError("Audio file path is not allowed.")
                with open(abs_path, "rb") as audio_file:
                    if language_code == 'auto':
                        logging.info(f"API Call Parameters: model=whisper-1, language=auto, prompt={context_prompt}")
                        transcript = client.audio.transcriptions.create(
                            model="whisper-1",
                            file=audio_file,
                            prompt=context_prompt
                        )
                        detected_language = 'en'
                    elif language_code in Config.SUPPORTED_LANGUAGE_CODES:
                        logging.info(f"API Call Parameters: model=whisper-1, language={language_code}, prompt={context_prompt}")
                        transcript = client.audio.transcriptions.create(
                            model="whisper-1",
                            file=audio_file,
                            language=language_code,
                            prompt=context_prompt
                        )
                        detected_language = language_code
                    else:
                        msg = f"Invalid language code for OpenAI Whisper: {language_code}"
                        if progress_callback:
                            progress_callback(msg)
                        logging.error(msg)
                        raise ValueError(msg)
                transcription_text = transcript.text
        except Exception as e:
            error_msg = f"Error transcribing file with OpenAI Whisper: {e}"
            if progress_callback:
                progress_callback(error_msg)
            logging.error(error_msg)
            return "", ""
        if progress_callback:
            progress_callback("Transcription completed.")
        return transcription_text, detected_language

    def split_and_transcribe(self, audio_file_path: str, language_code: str,
                             progress_callback: Optional[Callable[[str], None]] = None,
                             context_prompt: str = "") -> Tuple[str, str]:
        if progress_callback:
            progress_callback(f"Splitting file {audio_file_path}")
        else:
            logging.info(f"Splitting file {audio_file_path}")
        temp_dir = os.path.dirname(audio_file_path)
        chunk_files = file_service.split_audio_file(audio_file_path, temp_dir, progress_callback)
        client = OpenAI(api_key=self.api_key)
        transcription_texts = []
        total_chunks = len(chunk_files)
        detected_language = None
        for idx, chunk_path in enumerate(chunk_files):
            if progress_callback:
                progress_callback(f"Transcribing chunk {idx+1}/{total_chunks}")
            else:
                logging.debug(f"Transcribing chunk {idx+1}/{total_chunks}")
            try:
                with open(chunk_path, "rb") as audio_file:
                    if language_code == 'auto':
                        logging.info(f"API Call Parameters (chunk): model=whisper-1, language=auto, prompt={context_prompt}")
                        transcript = client.audio.transcriptions.create(
                            model="whisper-1",
                            file=audio_file,
                            prompt=context_prompt
                        )
                        if not detected_language:
                            detected_language = 'en'
                    elif language_code in Config.SUPPORTED_LANGUAGE_CODES:
                        logging.info(f"API Call Parameters (chunk): model=whisper-1, language={language_code}, prompt={context_prompt}")
                        transcript = client.audio.transcriptions.create(
                            model="whisper-1",
                            file=audio_file,
                            language=language_code,
                            prompt=context_prompt
                        )
                        detected_language = language_code
                    else:
                        msg = f"Invalid language code for OpenAI Whisper: {language_code}"
                        if progress_callback:
                            progress_callback(msg)
                        logging.error(msg)
                        raise ValueError(msg)
                transcription_text = transcript.text
            except Exception as e:
                error_msg = f"Error transcribing chunk {idx+1} with OpenAI Whisper: {e}"
                if progress_callback:
                    progress_callback(error_msg)
                logging.error(error_msg)
                transcription_text = ""
            transcription_texts.append(transcription_text)
        file_service.remove_files(chunk_files)
        full_transcription = " ".join(transcription_texts)
        if language_code == 'auto' and not detected_language:
            detected_language = 'en'
        logging.info("Transcription aggregated successfully.")
        return full_transcription, detected_language
