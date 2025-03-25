# app/services/api_clients/assemblyai.py

import logging
import os
from typing import Tuple, Optional, Callable
import assemblyai as aai
from app.config import Config

# Define a type hint for the progress callback
ProgressCallback = Optional[Callable[[str, bool], None]] # Message, IsError

class AssemblyAITranscriptionAPI:
    """
    Integration with AssemblyAI.
    """
    API_NAME = "AssemblyAI" # For logging

    def __init__(self, api_key: str) -> None:
        """Initializes the AssemblyAI API client."""
        if not api_key:
            # Log configuration error
            logging.error(f"[{self.API_NAME}] API key is required but not provided.")
            raise ValueError(f"{self.API_NAME} API key is required.")
        self.api_key = api_key
        try:
            aai.settings.api_key = self.api_key # Set globally for the library
            # Log successful initialization (console only)
            logging.info(f"[{self.API_NAME}] Client initialized successfully.")
            # DO NOT send initialization message to UI progress log
        except Exception as e:
            logging.error(f"[{self.API_NAME}] Failed to configure AssemblyAI SDK: {e}")
            raise ValueError(f"AssemblyAI SDK configuration failed: {e}") from e

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: ProgressCallback = None,
                   original_filename: Optional[str] = None
                   ) -> Tuple[Optional[str], Optional[str]]:
        """
        Transcribes the audio file using AssemblyAI. Reports progress via callback.

        Returns:
            A tuple containing (transcription_text, detected_language) or (None, None) on failure.
        """
        display_filename = original_filename or os.path.basename(audio_file_path)
        log_prefix = f"[{self.API_NAME}:{display_filename}]" # Prefix for internal console logs

        # Report start via callback - SIMPLE UI MESSAGE
        # (The service layer already sent "Starting transcription of file: ...")
        # if progress_callback:
        #     progress_callback(f"Starting transcription with {self.API_NAME}...", False)
        # else:
        #     logging.info(f"{log_prefix} Starting transcription (no callback)...")

        detected_language = None
        transcription_text = None

        try:
            # Check file existence before proceeding
            if not os.path.exists(audio_file_path):
                 # SIMPLE UI ERROR MESSAGE
                 msg = f"ERROR: Audio file not found at path: {audio_file_path}"
                 if progress_callback: progress_callback(msg, True)
                 logging.error(f"{log_prefix} {msg}") # Console log
                 return None, None # Return failure explicitly

            # Configure transcription based on language code
            config_params = {}
            if language_code == 'auto':
                config_params['language_detection'] = True
                # SIMPLE UI Message for language setting
                if progress_callback: progress_callback("Language detection enabled.", False)
                logging.info(f"{log_prefix} Language detection enabled.") # Console log
            elif language_code in Config.SUPPORTED_LANGUAGE_CODES:
                config_params['language_code'] = language_code
                # SIMPLE UI Message for language setting
                if progress_callback: progress_callback(f"Language set to '{language_code}'.", False)
                logging.info(f"{log_prefix} Language set to '{language_code}'.") # Console log
            else:
                # Console log
                logging.warning(f"{log_prefix} Invalid language code '{language_code}'. Using auto-detection as fallback.")
                # SIMPLE UI Message for fallback
                if progress_callback: progress_callback(f"Invalid language code '{language_code}'. Using auto-detection as fallback.", False) # Report as info/warning
                config_params['language_detection'] = True
                language_code = 'auto' # Update effective language code

            config_obj = aai.TranscriptionConfig(**config_params)
            transcriber = aai.Transcriber(config=config_obj)

            # SIMPLE UI Message for upload/processing start
            if progress_callback: progress_callback(f"Uploading and processing audio with {self.API_NAME}...", False)
            logging.info(f"{log_prefix} Submitting transcription request...") # Console log
            transcript = transcriber.transcribe(audio_file_path)
            logging.info(f"{log_prefix} Received response. Status: {transcript.status}") # Console log

            if transcript.status == aai.TranscriptStatus.error:
                error_detail = transcript.error or "Unknown AssemblyAI error"
                # SIMPLE UI Message for error
                msg = f"ERROR: {self.API_NAME} transcription failed: {error_detail}"
                if progress_callback: progress_callback(msg, True)
                logging.error(f"{log_prefix} {msg}") # Console log
                return None, None # Indicate failure

            # Success case
            transcription_text = transcript.text
            detected_language = language_code # Default assumption

            if language_code == 'auto':
                detected_lang_val = getattr(transcript, 'language_code', None)
                if detected_lang_val:
                    detected_language = str(detected_lang_val)
                    logging.info(f"{log_prefix} Detected language: {detected_language}") # Console log
                    # SIMPLE UI Message for detected language
                    if progress_callback: progress_callback(f"Detected language: {detected_language}", False)
                else:
                    detected_language = 'en' # Fallback
                    logging.warning(f"{log_prefix} Auto-detection did not return language code, defaulting to 'en'.") # Console log
                     # SIMPLE UI Message for inconclusive detection
                    if progress_callback: progress_callback("Language detection inconclusive, assuming 'en'.", False)

            # Report final success via callback - SIMPLE UI MESSAGE
            if progress_callback: progress_callback(f"{self.API_NAME} transcription completed.", False)
            logging.info(f"{log_prefix} Transcription completed successfully.") # Console log

            return transcription_text, detected_language

        except FileNotFoundError: # Should be caught earlier
             # SIMPLE UI ERROR MESSAGE
             error_msg = f"ERROR: Audio file disappeared before processing: {audio_file_path}"
             if progress_callback: progress_callback(error_msg, True)
             logging.error(f"{log_prefix} {error_msg}") # Console log
             return None, None
        except aai.Error as aai_error: # Catch specific AssemblyAI SDK errors
             # SIMPLE UI ERROR MESSAGE
             error_msg = f"ERROR: {self.API_NAME} API Error: {aai_error}"
             if progress_callback: progress_callback(error_msg, True)
             logging.error(f"{log_prefix} {error_msg}") # Console log
             return None, None
        except ValueError as ve: # Catch config errors from this method
             # SIMPLE UI ERROR MESSAGE
             error_msg = f"ERROR: Input Error: {ve}"
             if progress_callback: progress_callback(error_msg, True)
             logging.error(f"{log_prefix} {error_msg}") # Console log
             return None, None
        except Exception as e: # Catch unexpected errors
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Unexpected error during {self.API_NAME} transcription: {e}"
            if progress_callback: progress_callback(error_msg, True)
            logging.exception(f"{log_prefix} Unexpected error detail:") # Console log with traceback
            return None, None