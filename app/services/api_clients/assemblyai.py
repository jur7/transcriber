# app/services/api_clients/assemblyai.py

import logging
from typing import Tuple, Optional, Callable
import assemblyai as aai
from app.config import Config

class AssemblyAITranscriptionAPI:
    """
    Integration with AssemblyAI.
    """
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        logging.info("Initialized AssemblyAITranscriptionAPI.")

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        if progress_callback:
            progress_callback(f"Transcribing file {audio_file_path} with AssemblyAI, language {language_code}")
        else:
            logging.info(f"Transcribing file {audio_file_path} with AssemblyAI, language {language_code}")
        aai.settings.api_key = self.api_key
        try:
            if language_code == 'auto':
                config_obj = aai.TranscriptionConfig(language_detection=True)
            elif language_code in Config.SUPPORTED_LANGUAGE_CODES:
                config_obj = aai.TranscriptionConfig(language_code=language_code)
            else:
                msg = f"Invalid language code for AssemblyAI: {language_code}"
                if progress_callback:
                    progress_callback(msg)
                logging.error(msg)
                raise ValueError(msg)
            transcriber = aai.Transcriber(config=config_obj)
            transcript = transcriber.transcribe(audio_file_path)
            if transcript.status == aai.TranscriptStatus.error:
                msg = f"AssemblyAI transcription failed: {transcript.error}"
                if progress_callback:
                    progress_callback(msg)
                logging.error(msg)
                raise Exception(msg)
        except Exception as e:
            error_msg = f"Error transcribing file with AssemblyAI: {e}"
            if progress_callback:
                progress_callback(error_msg)
            logging.error(error_msg)
            return "", ""
        detected_language = language_code
        if language_code == 'auto':
            try:
                detected_language = getattr(transcript, 'detected_language_code', None) or getattr(transcript, 'language_code', None)
                if not detected_language:
                    detected_language = 'en'
            except Exception:
                detected_language = 'en'
        if progress_callback:
            progress_callback(f"AssemblyAI transcription completed. Detected language: {detected_language}")
        return transcript.text, detected_language
