# app/services/api_clients/assemblyai.py

import logging
from typing import Tuple, Optional, Callable
import assemblyai as aai

class AssemblyAITranscriptionAPI:
    """
    Integration with AssemblyAI.
    """
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        logging.info(f"Initialized AssemblyAITranscriptionAPI with API key: {api_key}")

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[str, str]:
        if progress_callback:
            progress_callback(f"Using AssemblyAI for transcription of {audio_file_path} with language code {language_code}")
        else:
            logging.info(f"Using AssemblyAI for transcription of {audio_file_path} with language code {language_code}")

        aai.settings.api_key = self.api_key
        if language_code == 'auto':
            config_obj = aai.TranscriptionConfig(language_detection=True)
        elif language_code in ['en', 'nl', 'fr', 'es']:
            config_obj = aai.TranscriptionConfig(language_code=language_code)
        else:
            message = f"Invalid language code for AssemblyAI: {language_code}"
            if progress_callback:
                progress_callback(message)
            logging.error(message)
            raise ValueError(message)
        transcriber = aai.Transcriber(config=config_obj)
        transcript = transcriber.transcribe(audio_file_path)
        if transcript.status == aai.TranscriptStatus.error:
            message = f"AssemblyAI transcription failed: {transcript.error}"
            if progress_callback:
                progress_callback(message)
            logging.error(message)
            raise Exception(message)
        detected_language = language_code
        if language_code == 'auto':
            try:
                detected_language = getattr(transcript, 'detected_language_code', None) or getattr(transcript, 'language_code', 'en')
            except AttributeError:
                detected_language = 'en'
        if progress_callback:
            progress_callback(f"AssemblyAI detected language: {detected_language}")
        return transcript.text, detected_language
