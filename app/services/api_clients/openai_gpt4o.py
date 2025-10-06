# app/services/api_clients/openai_gpt4o.py

import os
import logging
import time
from typing import Tuple, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI, OpenAIError, APIError, APIConnectionError, RateLimitError
from app.services import file_service
from app.config import Config

# Raised when a JSON response hits the text token limit and we want to retry
class OutputTokenLimitExceededError(Exception): pass

OUTPUT_TEXT_TOKENS_LIMIT = 2048

# Define a type hint for the progress callback
ProgressCallback = Optional[Callable[[str, bool], None]] # Message, IsError

class OpenAIGPT4oTranscriptionAPI:
    """
    Integration with OpenAI GPT4o Transcribe using synchronous requests.
    Handles large file splitting. Reports progress via callback.
    Supports parallel chunk transcription with bounded concurrency.
    """
    MODEL_NAME = "gpt-4o-transcribe" # Use constant for model name
    API_NAME = "OpenAI_GPT4o" # For logging

    def __init__(self, api_key: str) -> None:
        """Initializes the OpenAI GPT-4o API client."""
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

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: ProgressCallback = None,
                   context_prompt: str = "",
                   original_filename: Optional[str] = None
                   ) -> Tuple[Optional[str], Optional[str]]:
        """
        Transcribes the audio file using OpenAI GPT-4o Transcribe. Handles splitting.

        Returns:
            A tuple containing (transcription_text, detected_language) or (None, None) on failure.
            'detected_language' is based on request ('en' for 'auto', or the requested code).
        """
        requested_language = language_code # Store the original request
        display_filename = original_filename or os.path.basename(audio_file_path)
        log_prefix = f"[{self.API_NAME}:{display_filename}]" # Prefix for internal console logs

        # Report start via callback - SIMPLE UI MESSAGE
        # (The service layer already sent "Starting transcription of file: ...")
        # if progress_callback:
        #     progress_callback(f"Starting transcription with {self.API_NAME}...", False)
        # else:
        #     logging.info(f"{log_prefix} Starting transcription (no callback)...")

        transcription_text = None
        final_language_used = None # Track the language assumption/result

        try:
            # Check file existence before getting size
            if not os.path.exists(audio_file_path):
                 # SIMPLE UI ERROR MESSAGE
                 msg = f"ERROR: Audio file not found at path: {audio_file_path}"
                 if progress_callback: progress_callback(msg, True)
                 logging.error(f"{log_prefix} {msg}") # Console log
                 return None, None

            file_size = os.path.getsize(audio_file_path)
            file_length = file_service.get_audio_file_length(audio_file_path)
            # Check if splitting is needed (progress message handled by service layer)
            if file_size > file_service.OPENAI_MAX_FILE_SIZE or file_length > file_service.OPENAI_MAX_LENGTH_MS_4O:
                # Delegate to splitting method - it will use callback for progress
                if file_size > file_service.OPENAI_MAX_FILE_SIZE:
                    logging.info(f"{log_prefix} File size ({file_size / 1024 / 1024:.2f}MB) exceeds limit. Starting chunked transcription.") # Console log
                else: logging.info(f"{log_prefix} File length ({file_length / 1000:.2f}sec) exceeds limit. Starting chunked transcription.") # Console log
                # The splitting function will send its own UI messages
                return self._split_and_transcribe(audio_file_path, requested_language, progress_callback, context_prompt, display_filename) # Pass display_filename
            else:
                # Transcribe single file
                logging.info(f"{log_prefix} File size ({file_size / 1024 / 1024:.2f}MB) within limit, record within limit. Processing as single file.") # Console log
                abs_path = os.path.abspath(audio_file_path)
                temp_dir = os.path.dirname(abs_path)
                if not file_service.validate_file_path(abs_path, temp_dir):
                     # SIMPLE UI ERROR MESSAGE
                     msg = f"ERROR: Audio file path is not allowed or outside expected directory: {abs_path}"
                     if progress_callback: progress_callback(msg, True)
                     logging.error(f"{log_prefix} {msg}") # Console log
                     raise ValueError(msg) # Raise to be caught below

                # High-level UI message for single-file transcription
                if progress_callback:
                    progress_callback(f"Transcribing with OpenAI {self.MODEL_NAME}...", False)

                # Delegate the actual API call to the retry-enabled helper
                chunk_text = self._transcribe_single_chunk_with_retry(
                    abs_path,
                    1, 1,
                    requested_language,
                    progress_callback,
                    context_prompt,
                    f"{log_prefix}:Single",
                )

                # If helper failed after retries, it already reported errors; abort early
                if chunk_text is None:
                    return None, None

                transcription_text = chunk_text

            # Language Detection Note & Logging:
            if requested_language == 'auto':
                 final_language_used = 'en' # Our placeholder/default assumption for logging when 'auto'
                 # Console log message
                 log_lang_msg = "Transcription finished. Language detected implicitly (logged as 'en' default for 'auto' request)."
                 # SIMPLE UI Message
                 ui_lang_msg = f"OpenAI {self.MODEL_NAME} transcription finished. Language detected implicitly by model."
            else:
                 final_language_used = requested_language # Assume the requested language guided the model
                 # Console log message
                 log_lang_msg = f"Transcription finished. Used requested language: {final_language_used}"
                 # SIMPLE UI Message
                 ui_lang_msg = f"OpenAI {self.MODEL_NAME} transcription finished. Used requested language: {final_language_used}"

            logging.info(f"{log_prefix} {log_lang_msg}") # Console log
            if progress_callback: progress_callback(ui_lang_msg, False) # UI log
            # Add a final "completed" message for UI consistency
            if progress_callback: progress_callback("Transcription completed.", False)


            return transcription_text, final_language_used

        # --- Exception Handling ---
        except FileNotFoundError as fnf_error: # Should be caught earlier
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Audio file disappeared: {fnf_error}"
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}") # Console log
            return None, None
        except RateLimitError as rle:
             # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: OpenAI API rate limit exceeded: {rle}. Please try again later."
            if progress_callback: progress_callback(error_msg, True)
            logging.warning(f"{log_prefix} {error_msg}") # Console log (Warning level)
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
        except ValueError as ve: # Catch config/validation errors
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
                             display_filename: Optional[str] = None # Use display filename for logs
                             ) -> Tuple[Optional[str], Optional[str]]:
        """Handles splitting large files and transcribing chunks."""
        requested_language = language_code
        log_prefix = f"[{self.API_NAME}:{display_filename or os.path.basename(audio_file_path)}]" # Prefix for internal console logs

        temp_dir = os.path.dirname(audio_file_path)
        chunk_files = []
        final_language_used = None # Track language assumption

        try:
            # file_service.split_audio_file uses the progress_callback internally for UI messages
            chunk_files = file_service.split_audio_file(audio_file_path, temp_dir, progress_callback)
            if not chunk_files or len(chunk_files) == 0:
                # Error logged by split_audio_file via callback
                raise Exception("Audio splitting failed or resulted in no chunks.")

            total_chunks = len(chunk_files)
            logging.info(f"{log_prefix} Starting transcription of {total_chunks} chunks...") # Console log only
            # No separate UI message needed here, chunk processing messages will follow

            max_workers = max(1, int(getattr(Config, 'OPENAI_MAX_CONCURRENCY', 3)))
            max_workers = min(max_workers, total_chunks) # Do not exceed total chunks
            results: list[Optional[str]] = [None] * total_chunks
            error: Optional[Exception] = None

            if progress_callback:
                progress_callback(f"Transcribing {max_workers} chunks in parallel. Already transcribed: 0/{total_chunks}.", False)

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
                    # Update progress via callback
                    # Report individual chunk success via callback - SIMPLE UI MESSAGE
                    if progress_callback:
                        progress_callback(f"Transcribing {min(max_workers, total_chunks)} chunks in parallel. Already transcribed: {chunk_compl}/{total_chunks}.", False)
                    # Console log only
                    logging.info(f"{log_prefix}:Chunk{chunk_num} Transcription successful.")

            # If any error occurred, abort
            if error is not None or any(r is None for r in results):
                raise Exception(str(error) if error else "One or more chunks failed to transcribe.")

            full_transcription = " ".join(filter(None, results))
            # Console log only
            logging.info(f"{log_prefix} Successfully aggregated transcriptions from {total_chunks} chunks.")

            # Determine final language assumption
            if requested_language == 'auto':
                 final_language_used = 'en'
                 # Console log message
                 log_lang_msg = "Chunked transcription aggregated. Language detected implicitly (logged as 'en')."
                 # SIMPLE UI Message
                 ui_lang_msg = "Aggregated chunk transcriptions. Language detected implicitly by model."
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
            # Catch errors from splitting or during the chunk loop
            # SIMPLE UI ERROR MESSAGE
            error_msg = f"ERROR: Error during split and transcribe process: {e}"
            if progress_callback: progress_callback(error_msg, True)
            logging.exception(f"{log_prefix} Error detail in _split_and_transcribe:") # Console log with traceback
            return None, None
        finally:
            # Ensure cleanup of chunks
            if chunk_files:
                # Send SIMPLE UI message for cleanup start
                if progress_callback: progress_callback("Cleaning up temporary chunk files...", False)
                removed_count = file_service.remove_files(chunk_files) # remove_files logs specifics to console
                # Console log only
                logging.info(f"{log_prefix} Cleaned up {removed_count} temporary chunk file(s).")
                # Send SIMPLE UI message for cleanup finish
                if progress_callback: progress_callback(f"Cleaned up {removed_count} temporary chunk file(s).", False)


    def _transcribe_single_chunk_with_retry(self, chunk_path: str, idx: int, total_chunks: int,
                                            language_code: str, progress_callback: ProgressCallback = None,
                                            context_prompt: str = "", log_prefix: str = "", max_retries: int = 3) -> Optional[str]:
        """
        Transcribes a single chunk with retry logic using GPT-4o. Reports progress via callback.

        Returns: Transcription text string or None on failure.
        """
        requested_language = language_code
        last_error = None
        last_output_tokens = None
        chunk_base_name = os.path.basename(chunk_path)
        # Use provided log_prefix or construct one for console logs
        effective_log_prefix = log_prefix or f"[{self.API_NAME}:Chunk{idx}]"

        for attempt in range(max_retries):
            # Report chunk processing start via callback - SIMPLE UI MESSAGE
#            if progress_callback:
#                progress_callback(f"Transcribing chunk {idx}/{total_chunks}", False)

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
                        # Request JSON to access usage tokens and text
                        "response_format": "json",
                    }

                    # focus model on specific language
                    if requested_language != 'auto':
                        api_params["temperature"] = 0

                    if last_output_tokens is not None and last_output_tokens >= OUTPUT_TEXT_TOKENS_LIMIT:
                        api_params["temperature"] = 0.01

                    # If user imput non-blank prompt, add it to parameters
                    # Otherwise, results with blank prompt can be worse
                    if context_prompt != "":
                        api_params["prompt"] = context_prompt

                    # Language param omitted

                    # Log API call parameters internally (console only)
                    lang_note = ""
                    if requested_language == 'auto':
                        lang_note = " (Lang: 'auto' requested - implicit detection)"
                    elif requested_language in Config.SUPPORTED_LANGUAGE_CODES:
                        api_params["language"] = requested_language
                        ui_lang_msg = f"Language set to '{requested_language}'."
 #                       if progress_callback: progress_callback(ui_lang_msg, False)
                        logging.info(f"{log_prefix} anguage set to '{requested_language}'. Using auto-detection as fallback.")
                    else:
                        # Console log
                        logging.warning(f"{log_prefix} Invalid language code '{requested_language}'. Using auto-detection as fallback.")
                        # SIMPLE UI Message for fallback
                        ui_lang_msg = f"Invalid language code '{requested_language}'. Using implicit detection."
 #                       if progress_callback: progress_callback(ui_lang_msg, False) # Report as info/warning

                    # Log the parameters being sent (console only)
                    log_params = {k: v for k, v in api_params.items() if k != 'file'}
                    logging.info(f"{log_prefix} Calling API with parameters: {log_params}{lang_note}") # Console log

                    start_time = time.time()
                    # Console log only
                    logging.info(f"{effective_log_prefix} Attempt {attempt+1}: Calling OpenAI API...")
                    response = self.client.audio.transcriptions.create(**api_params)
                    duration = time.time() - start_time

                    # Parse JSON response for text and usage tokens
                    text = None
                    output_tokens = None
                    try:
                        # openai.types.audio.transcription.Transcription has .text and optional .usage
                        if hasattr(response, "text"):
                            text = getattr(response, "text", None)
                        # usage can be tokens or duration; check for token usage
                        usage = getattr(response, "usage", None)
                        if usage is not None and getattr(usage, "type", None) == "tokens":
                            output_tokens = getattr(usage, "output_tokens", None)
                    except Exception as parse_err:
                        logging.warning(f"{effective_log_prefix} Could not parse JSON response fields: {parse_err}")

                    # Console log only
                    if output_tokens is not None:
                        last_output_tokens = output_tokens
                        logging.info(f"{effective_log_prefix} Attempt {attempt+1}: API call successful. Duration: {duration:.2f}s. Output tokens: {output_tokens}")
                        # If token cap reached and we have retries left, raise to trigger a retry
                        if output_tokens >= OUTPUT_TEXT_TOKENS_LIMIT and attempt < max_retries - 1:
                            raise OutputTokenLimitExceededError(f"Output tokens {output_tokens} >= limit {OUTPUT_TEXT_TOKENS_LIMIT}")
                        if output_tokens >= OUTPUT_TEXT_TOKENS_LIMIT:
                            logging.warning(f"{effective_log_prefix} Output tokens {output_tokens} reached or exceeded limit {OUTPUT_TEXT_TOKENS_LIMIT}. Result may be truncated.")
                    else:
                        logging.info(f"{effective_log_prefix} Attempt {attempt+1}: API call successful. Duration: {duration:.2f}s")
                # Success
                # DO NOT send individual chunk success message to UI to reduce noise
                return text.strip() if text else "" # Return empty string for empty transcript

            # --- Exception Handling for Retries ---
            except RateLimitError as rle:
                last_error = rle
                wait_time = 2 ** attempt # Exponential backoff
                # SIMPLE UI Message for retry
                error_detail = f"Rate limit hit on chunk {idx}, attempt {attempt+1}. Retrying in {wait_time}s..."
                if progress_callback: progress_callback(error_detail, False) # Not fatal yet
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
            except OutputTokenLimitExceededError as tle:
                last_error = tle
                wait_time = 2 ** attempt
                # SIMPLE UI Message for retry
                error_detail = (f"Output token limit reached on chunk {idx}, attempt {attempt+1}. Retrying in {wait_time}s...")
                if progress_callback: progress_callback(error_detail, False)
                # Console log
                logging.warning(f"{effective_log_prefix} Output tokens limit reached (attempt {attempt+1}). Retrying in {wait_time}s... ({tle})")
                time.sleep(wait_time)
            except OpenAIError as oae:
                last_error = oae
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: OpenAI SDK error on chunk {idx}: {oae}"
                if progress_callback: progress_callback(error_detail, True)
                # Console log
                logging.error(f"{effective_log_prefix} OpenAI SDK error on chunk {idx}, attempt {attempt+1}: {oae}")
                break # Exit retry loop
            except ValueError as ve: # Catch path validation errors
                last_error = ve
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Input error processing chunk {idx}: {ve}"
                if progress_callback: progress_callback(error_detail, True)
                # Console log
                logging.error(f"{effective_log_prefix} {error_detail}")
                break # Exit retry loop
            except FileNotFoundError as fnf_error:
                last_error = fnf_error
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Chunk file not found: {chunk_base_name}. Error: {fnf_error}"
                if progress_callback: progress_callback(error_detail, True)
                # Console log
                logging.error(f"{effective_log_prefix} Chunk file not found on attempt {attempt+1}: {chunk_base_name}. Error: {fnf_error}")
                break # Exit retry loop
            except Exception as e:
                last_error = e
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Unexpected error transcribing chunk {idx}: {e}"
                if progress_callback: progress_callback(error_detail, True)
                # Console log
                logging.exception(f"{effective_log_prefix} Unexpected error detail on attempt {attempt+1}:")
                break # Exit retry loop
            # --- End of Exception Handling for Retries ---

        # If loop finishes without returning text
        # SIMPLE UI Message for final failure
        final_error_msg = f"ERROR: Chunk {idx} ('{chunk_base_name}') failed after {max_retries} attempts. Last error: {last_error}"
        if progress_callback: progress_callback(final_error_msg, True)
        # Console log
        logging.error(f"{effective_log_prefix} Chunk {idx} failed after {max_retries} attempts. Last error: {last_error}")
        return None # Indicate failure for this chunk