# app/services/api_clients/openai_whisper.py

import os
import logging
import time
from typing import Tuple, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI, OpenAIError, APIError, APIConnectionError, RateLimitError
from app.services import file_service
from app.config import Config

# Define a type hint for the progress callback
ProgressCallback = Optional[Callable[[str, bool], None]] # Message, IsError

class OpenAITranscriptionAPI:
    """
    Integration with OpenAI Whisper (whisper-1) using synchronous requests.
    Handles large file splitting and parallel chunk transcription with bounded concurrency.
    Reports progress via callback.
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
        Transcribes the audio file using OpenAI Whisper. Handles splitting if needed.

        Returns (transcription_text, detected_language) or (None, None) on failure.
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

        transcription_text: Optional[str] = None
        final_language_used: Optional[str] = None

        try:
            # Check file existence before getting size
            if not os.path.exists(audio_file_path):
                 # SIMPLE UI ERROR MESSAGE
                 msg = f"ERROR: Audio file not found at path: {audio_file_path}"
                 if progress_callback:
                    progress_callback(msg, True)
                 logging.error(f"{log_prefix} {msg}")
                 return None, None

            file_size = os.path.getsize(audio_file_path)
            file_length = file_service.get_audio_file_length(audio_file_path)

            # Decide whether to split; length threshold reused from file_service
            if file_size > file_service.OPENAI_MAX_FILE_SIZE or file_length > file_service.OPENAI_MAX_LENGTH_MS_4O:
                if file_size > file_service.OPENAI_MAX_FILE_SIZE:
                    logging.info(f"{log_prefix} File size ({file_size / 1024 / 1024:.2f}MB) exceeds limit. Starting chunked transcription.")                
                else:
                    logging.info(f"{log_prefix} File length ({file_length / 1000:.2f}sec) exceeds limit. Starting chunked transcription.")
                return self._split_and_transcribe(
                    audio_file_path, requested_language, progress_callback, context_prompt, display_filename
                )
            else:
                # Transcribe single file
                logging.info(f"{log_prefix} File size ({file_size / 1024 / 1024:.2f}MB) within limi. Processing as single file.")
                abs_path = os.path.abspath(audio_file_path)
                temp_dir = os.path.dirname(abs_path)
                if not file_service.validate_file_path(abs_path, temp_dir):
                     msg = f"ERROR: Audio file path is not allowed or outside expected directory: {abs_path}"
                     if progress_callback:
                        progress_callback(msg, True)
                     logging.error(f"{log_prefix} {msg}")
                     raise ValueError(msg)

                with open(abs_path, "rb") as audio_file:
                    api_params = {
                        "model": self.MODEL_NAME,
                        "file": audio_file,
                        "response_format": "text",
                        "prompt": context_prompt,
                    }

                # Encourage stability when language is specified
                    if requested_language != "auto":
                        api_params["temperature"] = 0

                    lang_note = ""
                    if requested_language == "auto":
                        lang_note = " (Language: 'auto' requested - implicit detection by model)"
                        if progress_callback:
                            progress_callback("Language: 'auto' requested - implicit detection by model.", False)
                    elif requested_language in Config.SUPPORTED_LANGUAGE_CODES:
                        api_params["language"] = requested_language
                    else:
                        logging.warning(f"{log_prefix} Invalid language code '{requested_language}'. Using auto-detection as fallback.")

                    # Log parameters (excluding the file object)
                    log_params = {k: v for k, v in api_params.items() if k != "file"}
                    logging.info(f"{log_prefix} Calling API with parameters: {log_params}{lang_note}")

                    if progress_callback:
                        progress_callback(f"Transcribing with OpenAI {self.MODEL_NAME}...", False)

                    start_time = time.time()
                    # Console log only
                    logging.info(f"{log_prefix} Calling OpenAI API...")
                    transcript_response = self.client.audio.transcriptions.create(**api_params)
                    duration = time.time() - start_time
                    # Console log only
                    logging.info(f"{log_prefix} OpenAI API call successful. Duration: {duration:.2f}s")

                    if api_params["response_format"] == "verbose_json":
                        transcription_text = transcript_response.text
                        final_detected_language = transcript_response.language
                        # Console log only
                        logging.info(f"{log_prefix} Detected language: {final_detected_language}")
                    else:
                        transcription_text = (
                            transcript_response if isinstance(transcript_response, str) else str(transcript_response)
                        )

            # Language note and final logging
            if requested_language == "auto":
                final_language_used = "en"
                log_lang_msg = "Transcription finished. Language detected implicitly (logged as 'en' default for 'auto' request)."
                ui_lang_msg = f"OpenAI {self.MODEL_NAME} transcription finished. Language detected implicitly by model."
            else:
                final_language_used = requested_language
                log_lang_msg = f"Transcription finished. Used requested language: {final_language_used}"
                ui_lang_msg = (
                    f"OpenAI {self.MODEL_NAME} transcription finished. Used requested language: {final_language_used}"
                )

            logging.info(f"{log_prefix} {log_lang_msg}")
            if progress_callback: progress_callback(ui_lang_msg, False)
            if progress_callback: progress_callback("Transcription completed.", False)

            return transcription_text, final_language_used

        # --- Exception Handling (Similar to GPT4o, adapted messages) ---
        except FileNotFoundError as fnf_error:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Audio file disappeared: {fnf_error}"
            if progress_callback:
                progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except RateLimitError as rle:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI API rate limit exceeded: {rle}. Please try again later."
            if progress_callback:
                progress_callback(error_msg, True)
            logging.warning(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except APIConnectionError as ace:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI API connection error: {ace}. Check network connectivity."
            if progress_callback:
                progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except APIError as apie:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI API returned an error: {apie}"
            if progress_callback:
                progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except OpenAIError as oae:
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI SDK Error: {oae}"
            if progress_callback:
                progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except ValueError as ve:
             # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Input Error: {ve}"
            if progress_callback:
                progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except Exception as e:
             # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Unexpected error during {self.API_NAME} transcription: {e}"
            if progress_callback:
                progress_callback(error_msg, True) # Console log
            logging.exception(f"{log_prefix} Unexpected error detail:")
            return None, None
        # --- End of Exception Handling ---


    def _split_and_transcribe(self, audio_file_path: str, language_code: str,
                             progress_callback: ProgressCallback = None,
                             context_prompt: str = "",
                             display_filename: Optional[str] = None
                             ) -> Tuple[Optional[str], Optional[str]]:
        """Handles splitting large files and transcribing chunks in parallel."""
        requested_language = language_code
        log_prefix = f"[{self.API_NAME}:{display_filename or os.path.basename(audio_file_path)}]" # Prefix for internal console logs

        temp_dir = os.path.dirname(audio_file_path)
        chunk_files: list[str] = []
        first_chunk_language = None # Store language from first chunk if 'auto'
        final_language_used: Optional[str] = None

        try:
            chunk_files = file_service.split_audio_file(audio_file_path, temp_dir, progress_callback)
            if not chunk_files:
                raise Exception("Audio splitting failed or resulted in no chunks.")

            total_chunks = len(chunk_files)
            logging.info(f"{log_prefix} Starting transcription of {total_chunks} chunks...")

            max_workers = min(total_chunks, max(1, int(getattr(Config, "OPENAI_MAX_CONCURRENCY", 3))))
            results: list[Optional[str]] = [None] * total_chunks
            error: Optional[Exception] = None

            if progress_callback:
                progress_callback(f"Transcribing {min(max_workers, total_chunks)} chunks in parallel. Already transcribed: 0/{total_chunks}.",False,)

            chunk_compl = 0
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_index = {}
                for idx, chunk_path in enumerate(chunk_files):
                    chunk_num = idx + 1
                    chunk_log_prefix = f"{log_prefix}:Chunk{chunk_num}"
                    future = executor.submit(
                        self._transcribe_single_chunk_with_retry,
                        chunk_path,
                        chunk_num,
                        total_chunks,
                        requested_language,
                        progress_callback,
                        context_prompt,
                        chunk_log_prefix,
                    )
                    future_to_index[future] = idx

                for future in as_completed(future_to_index):
                    idx = future_to_index[future]
                    chunk_num = idx + 1
                    try:
                        chunk_text = future.result()
                    except Exception as e:
                        error = e
                        logging.exception(f"{log_prefix}:Chunk{chunk_num} Unexpected exception during transcription:")
                        break
                    if chunk_text is None:
                        error = Exception(f"Failed to transcribe chunk {chunk_num}.")
                        break
                    results[idx] = chunk_text
                    chunk_compl += 1
                    if progress_callback:
                        progress_callback(f"Transcribing {min(max_workers, total_chunks)} chunks in parallel. Already transcribed: {chunk_compl}/{total_chunks}.", False,)
                    logging.info(f"{log_prefix}:Chunk{chunk_num} Transcription successful.")

            if error is not None or any(r is None for r in results):
                raise Exception(str(error) if error else "One or more chunks failed to transcribe.")

            full_transcription = " ".join(filter(None, results))
            logging.info(f"{log_prefix} Successfully aggregated transcriptions from {total_chunks} chunks.")

            if requested_language == "auto":
                final_language_used = "en"
                log_lang_msg = "Chunked transcription aggregated. Language detected implicitly (logged as 'en')."
                ui_lang_msg = "Aggregated chunk transcriptions. Language detected implicitly by model."
            else:
                final_language_used = requested_language
                log_lang_msg = (
                    f"Chunked transcription aggregated. Used requested language: {final_language_used}"
                )
                ui_lang_msg = (
                    f"Aggregated chunk transcriptions. Used requested language: {final_language_used}"
                )

            logging.info(f"{log_prefix} {log_lang_msg}")
            if progress_callback:
                progress_callback(ui_lang_msg, False)
                progress_callback("Transcription completed.", False)

            return full_transcription, final_language_used

        except Exception as e:
            error_msg = f"ERROR: Error during split and transcribe process: {e}"
            if progress_callback:
                progress_callback(error_msg, True)
            logging.exception(f"{log_prefix} Error detail in _split_and_transcribe:")
            return None, None
        finally:
            if chunk_files:
                if progress_callback:
                    progress_callback("Cleaning up temporary chunk files...", False)
                removed_count = file_service.remove_files(chunk_files)
                logging.info(f"{log_prefix} Cleaned up {removed_count} temporary chunk file(s).")
                if progress_callback:
                    progress_callback(f"Cleaned up {removed_count} temporary chunk file(s).", False)


    def _transcribe_single_chunk_with_retry(
            self, chunk_path: str, idx: int, total_chunks: int,
            language_code: str, response_format: str,
            progress_callback: ProgressCallback = None,
            context_prompt: str = "", log_prefix: str = "", max_retries: int = 3,
        ) -> Tuple[Optional[str], Optional[str]]:
        """Transcribes a single chunk with retry logic using Whisper."""
        requested_language = language_code
        chunk_base_name = os.path.basename(chunk_path)
        effective_log_prefix = log_prefix or f"[{self.API_NAME}:Chunk{idx}]"

        abs_path = os.path.abspath(chunk_path)
        temp_dir = os.path.dirname(abs_path)
        if not file_service.validate_file_path(abs_path, temp_dir):
            error_detail = (
                f"ERROR: Chunk file path is not allowed or outside expected directory: {abs_path}"
            )
            if progress_callback:
                progress_callback(error_detail, True)
            logging.error(f"{effective_log_prefix} {error_detail}")
            return None

        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                with open(abs_path, "rb") as audio_file:
                    api_params = {
                        "model": self.MODEL_NAME,
                        "file": audio_file,
                        "response_format": "text",
                        "prompt": context_prompt,
                    }

                    if requested_language != "auto":
                        api_params["temperature"] = 0

                    lang_note = ""
                    if requested_language == "auto":
                        lang_note = " (Language: 'auto' requested - implicit detection by model)"
                    elif requested_language in Config.SUPPORTED_LANGUAGE_CODES:
                        api_params["language"] = requested_language
                    else:
                        logging.warning(f"{effective_log_prefix} Invalid language code '{requested_language}'. Using auto-detection as fallback.")

                    log_params = {k: v for k, v in api_params.items() if k != "file"}
                    logging.info(f"{log_prefix} Calling API with parameters: {log_params}{lang_note}")

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
#                        detected_lang = response.language
#                        # Console log only
#                        logging.info(f"{effective_log_prefix} Detected language in chunk: {detected_lang}")
                    else:
                        text = response if isinstance(response, str) else str(response)
                # Success - return text and detected language (if available)
                # DO NOT send individual chunk success message to UI to reduce noise
                return (text.strip() if text else ""), detected_lang

            # --- Exception Handling for Retries (Similar to GPT4o) ---
            except RateLimitError as rle:
                last_error = rle
                wait_time = 2**attempt
                # Non-fatal error, retry after delay
                if progress_callback: progress_callback(f"Rate limit hit on chunk {idx}, attempt {attempt+1}. Retrying in {wait_time}s...",False,)
                logging.warning(f"{effective_log_prefix} Rate limit hit, attempt {attempt+1}. Retrying in {wait_time}s... ({rle})")
                time.sleep(wait_time)
            except (APIConnectionError, APIError) as e:
                last_error = e
                wait_time = 2**attempt
                # Non-fatal error, retry after delay
                if progress_callback: progress_callback(f"API error on chunk {idx} (Attempt {attempt+1}). Retrying in {wait_time}s...", False,)
                logging.error(f"{effective_log_prefix} API error on chunk {idx}, attempt {attempt+1}: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            except OpenAIError as oae:
                last_error = oae
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: OpenAI SDK error on chunk {idx}: {oae}"
                if progress_callback: progress_callback(error_detail, True)
                logging.error(f"{effective_log_prefix} OpenAI SDK error on chunk {idx}, attempt {attempt+1}: {oae}")
                break
            except ValueError as ve:
                last_error = ve
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Input error processing chunk {idx}: {ve}"
                if progress_callback: progress_callback(error_detail, True)
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
        final_error_msg = (f"ERROR: Chunk {idx} ('{chunk_base_name}') failed after {max_retries} attempts. Last error: {last_error}")
        if progress_callback:
            progress_callback(final_error_msg, True)
        # Console log
        logging.error(f"{effective_log_prefix} Chunk {idx} failed after {max_retries} attempts. Last error: {last_error}")
        return None