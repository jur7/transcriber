# app/services/api_clients/openai_whisper.py

import os
import logging
import time
from typing import Tuple, Optional, Callable
from openai import OpenAI, OpenAIError, APIError, APIConnectionError, RateLimitError
from app.services import file_service
from app.config import Config

# Define a type hint for the progress callback
ProgressCallback = Optional[Callable[[str, bool], None]] # Message, IsError

class OpenAITranscriptionAPI: # Renamed from original
    """
    Integration with OpenAI Whisper (whisper-1 model).
    Handles large file splitting and language detection. Reports progress via callback.
    """
    MODEL_NAME = "whisper-1"
    API_NAME = "OpenAI_Whisper" # For logging

    def __init__(self, api_key: str) -> None:
        """Initializes the OpenAI Whisper API client."""
        if not api_key:
            logging.error(f"[{self.API_NAME}] API key is required but not provided.")
            raise ValueError("OpenAI API key is required.")
        self.api_key = api_key
        try:
            self.client = OpenAI(api_key=self.api_key)
            # Log successful initialization (console only)
            logging.info(f"[{self.API_NAME}] Client initialized successfully for model {self.MODEL_NAME}.")
            # DO NOT send initialization message to UI progress log
        except OpenAIError as e:
            logging.error(f"[{self.API_NAME}] Failed to initialize OpenAI client: {e}")
            raise ValueError(f"OpenAI client initialization failed: {e}") from e

    def lang_to_code(self, lang_name_or_code: str) -> str:
        lang_code = None
        lang_name_or_code = lang_name_or_code.strip().lower()
        # already have a supported language code, return it
        if lang_name_or_code in (code.lower() for code in Config.SUPPORTED_LANGUAGE_CODES):
            lang_code = lang_name_or_code
        # probably the language name
        if not lang_code:
        # Reverse map names -> codes (ignore the 'auto' label if you want)
            name_to_code = {name.lower(): code for code, name in Config.SUPPORTED_LANGUAGE_NAMES.items()}
            lang_code = name_to_code.get(lang_name_or_code)
        # language not detected -> auto
        if not lang_code:
            lang_code = "auto"
        return lang_code
    
    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: ProgressCallback = None,
                   context_prompt: str = "",
                   original_filename: Optional[str] = None
                   ) -> Tuple[Optional[str], Optional[str]]:
        """
        Transcribes the audio file using OpenAI Whisper-1. Handles splitting.

        Returns:
            A tuple containing (transcription_text, detected_language) or (None, None) on failure.
        """
        requested_language = language_code
        display_filename = original_filename or os.path.basename(audio_file_path)
        log_prefix = f"[{self.API_NAME}:{display_filename}]" # Prefix for internal console logs

        # Report start via callback - SIMPLE UI MESSAGE
        # (The service layer already sent "Starting transcription of file: ...")
        # if progress_callback:
        #     progress_callback(f"Starting transcription with {self.API_NAME}...", False)
        # else:
        #     logging.info(f"{log_prefix} Starting transcription (no callback)...")

        transcription_text = None
        final_detected_language = None # Track the actual detected/used language

        try:
            # Check file existence before getting size
            if not os.path.exists(audio_file_path):
                 # SIMPLE UI ERROR MESSAGE
                 msg = f"ERROR: Audio file not found at path: {audio_file_path}"
                 if progress_callback: progress_callback(msg, True)
                 logging.error(f"{log_prefix} {msg}") # Console log
                 return None, None

            file_size = os.path.getsize(audio_file_path)
            # Check if splitting is needed (progress message handled by service layer)
            if file_size > file_service.OPENAI_MAX_FILE_SIZE:
                # Delegate to splitting method - uses callback for progress
                logging.info(f"{log_prefix} File size ({file_size / 1024 / 1024:.2f}MB) exceeds limit. Starting chunked transcription.") # Console log
                # The splitting function will send its own UI messages
                return self._split_and_transcribe(audio_file_path, requested_language, progress_callback, context_prompt, display_filename)
            else:
                # Transcribe single file
                logging.info(f"{log_prefix} File size ({file_size / 1024 / 1024:.2f}MB) within limit. Processing as single file.") # Console log
                abs_path = os.path.abspath(audio_file_path)
                temp_dir = os.path.dirname(abs_path)
                if not file_service.validate_file_path(abs_path, temp_dir):
                     # SIMPLE UI ERROR MESSAGE
                     msg = f"ERROR: Audio file path is not allowed or outside expected directory: {abs_path}"
                     if progress_callback: progress_callback(msg, True)
                     logging.error(f"{log_prefix} {msg}") # Console log
                     raise ValueError(msg)

                with open(abs_path, "rb") as audio_file:
                    api_params = {
                        "model": self.MODEL_NAME,
                        "file": audio_file,
                        "prompt": context_prompt
                    }
                    log_lang_param_desc = ""
                    ui_lang_msg = ""

                    # Determine response format and language parameter
                    if requested_language == 'auto':
                        api_params["response_format"] = "verbose_json" # Need this for detected language
                        log_lang_param_desc = "'auto' (detection requested)"
                        # SIMPLE UI Message
                        ui_lang_msg = "Language detection requested."
                        if progress_callback: progress_callback(ui_lang_msg, False)
                    elif requested_language in Config.SUPPORTED_LANGUAGE_CODES:
                        api_params["language"] = requested_language
                        api_params["response_format"] = "text" # Text is sufficient
                        log_lang_param_desc = f"'{requested_language}'"
                        # SIMPLE UI Message
                        ui_lang_msg = f"Language set to '{requested_language}'."
                        if progress_callback: progress_callback(ui_lang_msg, False)
                    else:
                        # Console log
                        logging.warning(f"{log_prefix} Invalid language code '{requested_language}'. Using auto-detection as fallback.")
                        # SIMPLE UI Message for fallback
                        ui_lang_msg = f"Invalid language code '{requested_language}'. Using auto-detection as fallback."
                        if progress_callback: progress_callback(ui_lang_msg, False) # Report as info/warning
                        # Fallback settings
                        api_params["response_format"] = "verbose_json"
                        log_lang_param_desc = "'auto' (fallback detection)"
                        requested_language = 'auto' # Update effective language

                    # Log the parameters being sent (console only)
                    log_params = {k: v for k, v in api_params.items() if k != 'file'}
                    logging.info(f"{log_prefix} Calling API with parameters: {log_params} (Lang: {log_lang_param_desc})")

                    # Report API call via callback - SIMPLE UI MESSAGE
                    if progress_callback: progress_callback(f"Transcribing with OpenAI {self.MODEL_NAME}...", False)

                    start_time = time.time()
                    # Console log only
                    logging.info(f"{log_prefix} Calling OpenAI API...")
                    transcript_response = self.client.audio.transcriptions.create(**api_params)
                    duration = time.time() - start_time
                     # Console log only
                    logging.info(f"{log_prefix} OpenAI API call successful. Duration: {duration:.2f}s")

                    # Process response based on format
                    if api_params["response_format"] == "verbose_json":
                        transcription_text = transcript_response.text
                        final_detected_language = transcript_response.language
                        # Console log only
                        logging.info(f"{log_prefix} Detected language: {final_detected_language}")
                        # SIMPLE UI Message for detected language
                        if progress_callback: progress_callback(f"Detected language: {final_detected_language}", False)
                    else: # response_format was 'text'
                        transcription_text = transcript_response if isinstance(transcript_response, str) else str(transcript_response)
                        final_detected_language = requested_language # Language was specified

            # Console log message
            log_lang_msg = f"Transcription finished. Final language: {final_detected_language}"
            logging.info(f"{log_prefix} {log_lang_msg}")
            # SIMPLE UI Message for finish
            ui_finish_msg = f"OpenAI {self.MODEL_NAME} transcription finished. Final language: {final_detected_language}"
            if progress_callback: progress_callback(ui_finish_msg, False)
             # Add a final "completed" message for UI consistency
            if progress_callback: progress_callback("Transcription completed.", False)

            return transcription_text, final_detected_language

        # --- Exception Handling (Similar to GPT4o, adapted messages) ---
        except FileNotFoundError as fnf_error:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Audio file disappeared: {fnf_error}"
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except RateLimitError as rle:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI API rate limit exceeded: {rle}. Please try again later."
            if progress_callback: progress_callback(error_msg, True)
            logging.warning(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except APIConnectionError as ace:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI API connection error: {ace}. Check network connectivity."
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except APIError as apie:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI API returned an error: {apie}"
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except OpenAIError as oae:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI SDK Error: {oae}"
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except ValueError as ve:
             # SIMPLE UI ERROR MESSAGE
             error_msg = f"ERROR: Input Error: {ve}"
             if progress_callback: progress_callback(error_msg, True)
             logging.error(f"{log_prefix} {error_msg}") # Console log
             return None, None
        except Exception as e:
             # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Unexpected error during {self.API_NAME} transcription: {e}"
            if progress_callback: progress_callback(error_msg, True)
            logging.exception(f"{log_prefix} Unexpected error detail:") # Console log with traceback
            return None, None
        # --- End of Exception Handling ---


    def _split_and_transcribe(self, audio_file_path: str, language_code: str,
                             progress_callback: ProgressCallback = None,
                             context_prompt: str = "",
                             display_filename: Optional[str] = None
                             ) -> Tuple[Optional[str], Optional[str]]:
        """Handles splitting large files and transcribing chunks for Whisper-1."""
        requested_language = language_code
        log_prefix = f"[{self.API_NAME}:{display_filename or os.path.basename(audio_file_path)}]" # Prefix for internal console logs

        temp_dir = os.path.dirname(audio_file_path)
        chunk_files = []
        first_chunk_language = None # Store language from first chunk if 'auto'
        final_language_used = None # Track final language for return

        try:
            # file_service.split_audio_file uses the progress_callback internally for UI messages
            chunk_files = file_service.split_audio_file(audio_file_path, temp_dir, progress_callback)
            if not chunk_files:
                raise Exception("Audio splitting failed or resulted in no chunks.")

            transcription_texts = []
            total_chunks = len(chunk_files)
            # Console log only
            logging.info(f"{log_prefix} Starting transcription of {total_chunks} chunks...")
            # No separate UI message needed here, chunk processing messages will follow

            for idx, chunk_path in enumerate(chunk_files):
                chunk_num = idx + 1
                # Construct specific log prefix for console logs
                chunk_log_prefix = f"{log_prefix}:Chunk{chunk_num}"

                # Determine language and format for this chunk
                current_chunk_lang_param = requested_language
                response_format = "text" # Default
                if requested_language == 'auto':
                    if first_chunk_language:
                        # Use language from first chunk for consistency
                        current_chunk_lang_param = self.lang_to_code(first_chunk_language)
                        response_format = "text"
                        # Console log only
                        logging.info(f"{chunk_log_prefix} Using detected language '{first_chunk_language}' from first chunk.")
                        # SIMPLE UI Message
                        if progress_callback: progress_callback(f"Using detected language '{first_chunk_language}' for subsequent chunks.", False)
                    else:
                        # First chunk, need to detect language
                        current_chunk_lang_param = 'auto'
                        response_format = "verbose_json"
                         # Console log only
                        logging.info(f"{chunk_log_prefix} First chunk, requesting language detection.")
                        # SIMPLE UI Message
                        if progress_callback: progress_callback("First chunk: Requesting language detection.", False)
                # No need for explicit invalid code check here if transcribe() handles fallback

                # Pass current_chunk_lang_param and response_format
                # The chunk method will send its own UI messages via callback
                chunk_text, chunk_detected_lang = self._transcribe_single_chunk_with_retry(
                    chunk_path, chunk_num, total_chunks, current_chunk_lang_param, response_format,
                    progress_callback, context_prompt, chunk_log_prefix # Pass specific log prefix
                )

                if chunk_text is None:
                    # Error reported by retry function via callback
                    raise Exception(f"Failed to transcribe chunk {chunk_num}. Aborting.")

                transcription_texts.append(chunk_text)
                # Console log only
                logging.info(f"{chunk_log_prefix} Transcription successful.")

                # Store detected language from the first chunk if auto-detecting
                if requested_language == 'auto' and idx == 0 and chunk_detected_lang:
                    first_chunk_language = chunk_detected_lang
                    # Console log only
                    logging.info(f"{log_prefix} Detected language '{first_chunk_language}' from first chunk.")
                    # SIMPLE UI Message for detected language
                    if progress_callback: progress_callback(f"Detected language: {first_chunk_language}", False)

            full_transcription = " ".join(filter(None, transcription_texts))
            # Console log only
            logging.info(f"{log_prefix} Successfully aggregated transcriptions from {total_chunks} chunks.")

            # Determine final detected language for return value
            if requested_language == 'auto':
                final_language_used = first_chunk_language or 'en' # Fallback if detection failed
                # Console log message
                log_lang_msg = f"Chunked transcription aggregated. Final language (detected/fallback): {final_language_used}"
                # SIMPLE UI Message
                ui_lang_msg = f"Aggregated chunk transcriptions. Final language (detected/fallback): {final_language_used}"
            else:
                final_language_used = requested_language
                 # Console log message
                log_lang_msg = f"Chunked transcription aggregated. Used requested language: {final_language_used}"
                # SIMPLE UI Message
                ui_lang_msg = f"Aggregated chunk transcriptions. Used requested language: {final_language_used}"

            logging.info(f"{log_prefix} {log_lang_msg}") # Console log
            # Send aggregation UI message
            if progress_callback: progress_callback(ui_lang_msg, False)
            # Add a final "completed" message for UI consistency
            if progress_callback: progress_callback("Transcription completed.", False)

            return full_transcription, final_language_used

        except Exception as e:
             # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Error during split and transcribe process: {e}"
            if progress_callback: progress_callback(error_msg, True)
            logging.exception(f"{log_prefix} Error detail in _split_and_transcribe:") # Console log with traceback
            return None, None
        finally:
            if chunk_files:
                # Send SIMPLE UI message for cleanup start
                if progress_callback: progress_callback("Cleaning up temporary chunk files...", False)
                removed_count = file_service.remove_files(chunk_files) # remove_files logs specifics to console
                # Console log only
                logging.info(f"{log_prefix} Cleaned up {removed_count} temporary chunk file(s).")
                 # Send SIMPLE UI message for cleanup finish
                if progress_callback: progress_callback(f"Cleaned up {removed_count} temporary chunk file(s).", False)


    def _transcribe_single_chunk_with_retry(self, chunk_path: str, idx: int, total_chunks: int,
                                            language_code: str, response_format: str, # language_code is the param to send
                                            progress_callback: ProgressCallback = None,
                                            context_prompt: str = "", log_prefix: str = "", max_retries: int = 3
                                            ) -> Tuple[Optional[str], Optional[str]]:
        """
        Transcribes a single chunk with retry logic using Whisper-1. Reports progress via callback.

        Returns: Tuple (transcription_text, detected_language) or (None, None). detected_language is None if format='text'.
        """
        last_error = None
        chunk_base_name = os.path.basename(chunk_path)
        # Use provided log_prefix or construct one for console logs
        effective_log_prefix = log_prefix or f"[{self.API_NAME}:Chunk{idx}]"

        for attempt in range(max_retries):
            # Report chunk processing start via callback - SIMPLE UI MESSAGE
            if progress_callback:
                progress_callback(f"Transcribing chunk {idx}/{total_chunks}", False)

            try:
                abs_chunk_path = os.path.abspath(chunk_path)
                temp_dir = os.path.dirname(abs_chunk_path)
                if not file_service.validate_file_path(abs_chunk_path, temp_dir):
                    msg = f"Chunk file path is not allowed: {abs_chunk_path}"
                    logging.error(f"{effective_log_prefix} {msg}") # Console log
                    raise ValueError(msg)

                with open(abs_chunk_path, "rb") as audio_file:
                    api_params = {
                        "model": self.MODEL_NAME,
                        "file": audio_file,
                        "response_format": response_format,
                        "prompt": context_prompt
                    }
                    log_lang_param_desc = ""
                    # Only add language if it's not 'auto'
                    if language_code != 'auto':
                        api_params["language"] = language_code
                        log_lang_param_desc = f"'{language_code}'"
                    else:
                        log_lang_param_desc = "'auto' (detection requested)"


                    # Log API call parameters internally (console only)
                    log_params = {k: v for k, v in api_params.items() if k != 'file'}
                    logging.info(f"{effective_log_prefix} Attempt {attempt+1}: Calling API with parameters: {log_params} (Lang: {log_lang_param_desc})")

                    start_time = time.time()
                    # Console log only
                    logging.info(f"{effective_log_prefix} Attempt {attempt+1}: Calling OpenAI API...")
                    response = self.client.audio.transcriptions.create(**api_params)
                    duration = time.time() - start_time
                    # Console log only
                    logging.info(f"{effective_log_prefix} Attempt {attempt+1}: API call successful. Duration: {duration:.2f}s")

                    # Process response
                    text = None
                    detected_lang = None # Language detected by API in this chunk
                    if response_format == "verbose_json":
                        text = response.text
                        detected_lang = response.language
                        # Console log only
                        logging.info(f"{effective_log_prefix} Detected language in chunk: {detected_lang}")
                        # UI Message for detected language (only if first chunk, handled in _split_and_transcribe)
                    else: # text format
                        text = response if isinstance(response, str) else str(response)
                        # detected_lang remains None

                # Success - return text and detected language (if available)
                # DO NOT send individual chunk success message to UI to reduce noise
                return (text.strip() if text else ""), detected_lang

            # --- Exception Handling for Retries (Similar to GPT4o) ---
            except RateLimitError as rle:
                last_error = rle
                wait_time = 2 ** attempt
                # SIMPLE UI Message for retry
                error_detail = f"Rate limit hit on chunk {idx}, attempt {attempt+1}. Retrying in {wait_time}s..."
                if progress_callback: progress_callback(error_detail, False)
                # Console log
                logging.warning(f"{effective_log_prefix} Rate limit hit, attempt {attempt+1}. Retrying in {wait_time}s... ({rle})")
                time.sleep(wait_time)
            except (APIConnectionError, APIError) as e:
                 last_error = e
                 wait_time = 2 ** attempt
                 # SIMPLE UI Message for retry
                 error_detail = f"API error on chunk {idx} (Attempt {attempt+1}). Retrying in {wait_time}s..."
                 if progress_callback: progress_callback(error_detail, False)
                 # Console log
                 logging.error(f"{effective_log_prefix} API error on chunk {idx}, attempt {attempt+1}: {e}. Retrying in {wait_time}s...")
                 time.sleep(wait_time)
            except OpenAIError as oae:
                last_error = oae
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: OpenAI SDK error on chunk {idx}: {oae}"
                if progress_callback: progress_callback(error_detail, True)
                # Console log
                logging.error(f"{effective_log_prefix} OpenAI SDK error on chunk {idx}, attempt {attempt+1}: {oae}")
                break
            except ValueError as ve:
                last_error = ve
                 # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Input error processing chunk {idx}: {ve}"
                if progress_callback: progress_callback(error_detail, True)
                # Console log
                logging.error(f"{effective_log_prefix} {error_detail}")
                break
            except FileNotFoundError as fnf_error:
                last_error = fnf_error
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Chunk file not found: {chunk_base_name}. Error: {fnf_error}"
                if progress_callback: progress_callback(error_detail, True)
                # Console log
                logging.error(f"{effective_log_prefix} Chunk file not found on attempt {attempt+1}: {chunk_base_name}. Error: {fnf_error}")
                break
            except Exception as e:
                last_error = e
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Unexpected error transcribing chunk {idx}: {e}"
                if progress_callback: progress_callback(error_detail, True)
                # Console log
                logging.exception(f"{effective_log_prefix} Unexpected error detail on attempt {attempt+1}:")
                break
            # --- End of Exception Handling ---

        # If loop finishes without returning text
        # SIMPLE UI Message for final failure
        final_error_msg = f"ERROR: Chunk {idx} ('{chunk_base_name}') failed after {max_retries} attempts. Last error: {last_error}"
        if progress_callback: progress_callback(final_error_msg, True)
        # Console log
        logging.error(f"{effective_log_prefix} Chunk {idx} failed after {max_retries} attempts. Last error: {last_error}")
        return None, None # Indicate failure for this chunk