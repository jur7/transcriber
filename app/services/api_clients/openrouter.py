# app/services/api_clients/openrouter.py

import os
import logging
import time
from typing import Tuple, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from collections import Counter
from app.services import file_service
from app.config import Config

# Define a type hint for the progress callback
ProgressCallback = Optional[Callable[[str, bool], None]] # Message, IsError

class OpenRouterTranscriptionAPI:
    """
    Integration with OpenRouter using synchronous requests.
    Handles large file splitting. Reports progress via callback.
    Supports parallel chunk transcription with bounded concurrency.
    """
    MODEL_NAME = Config.OPENROUTER_MODEL_NAME
    API_NAME = "OpenRouter" # For logging

    def __init__(self, api_key: str, model_name: Optional[str] = None) -> None:
        """Initializes the OpenRouter API client."""
        if not api_key:
            logging.error(f"[{self.API_NAME}] API key is required but not provided.")
            raise ValueError("OpenRouter API key is required.")
        self.api_key = api_key
        self.MODEL_NAME = model_name or Config.OPENROUTER_MODEL_NAME
        logging.info(f"[{self.API_NAME}] Client initialized successfully for model {self.MODEL_NAME}.")

    def transcribe(self, audio_file_path: str, language_code: str,
                   progress_callback: ProgressCallback = None,
                   context_prompt: str = "",
                   original_filename: Optional[str] = None
                   ) -> Tuple[Optional[str], Optional[str]]:
        """
        Transcribes the audio file using OpenRouter. Handles splitting.

        Returns:
            A tuple containing (transcription_text, detected_language) or (None, None) on failure.
            'detected_language' comes from the OpenRouter response.
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
        final_language_used = None

        try:
            # Keep the shared client signature, but OpenRouter ignores the standard free-form prompt parameter.
            if context_prompt.strip():
                raise ValueError("OpenRouter file transcription does not support context prompts.")
            # Check file existence before getting size
            if not os.path.exists(audio_file_path):
                 # SIMPLE UI ERROR MESSAGE
                 msg = f"ERROR: Audio file not found at path: {audio_file_path}"
                 if progress_callback: progress_callback(msg, True)
                 logging.error(f"{log_prefix} {msg}") # Console log
                 return None, None

            file_size = os.path.getsize(audio_file_path)
            file_length = file_service.get_audio_file_length(audio_file_path)
            # Preserve the GPT client splitting policy for parallel processing.
            # OpenRouter caps multipart uploads at 25 MB; keep the shared duration threshold.
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
                    progress_callback(f"Transcribing with OpenRouter {self.MODEL_NAME}...", False)

                # Delegate the actual API call to the retry-enabled helper
                chunk_result = self._transcribe_single_chunk_with_retry(
                    abs_path,
                    1, 1,
                    requested_language,
                    progress_callback,
                    context_prompt,
                    f"{log_prefix}:Single",
                )

                # If helper failed after retries, it already reported errors; abort early
                if chunk_result is None:
                    return None, None

                transcription_text, final_language_used = chunk_result
                if not transcription_text:
                    raise ValueError("OpenRouter returned no transcription text.")

            log_lang_msg = f"Transcription finished. Detected language: {final_language_used}"
            ui_lang_msg = f"OpenRouter {self.MODEL_NAME} transcription finished. Detected language: {final_language_used}"

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
        final_language_used = None

        try:
            # file_service.split_audio_file uses the progress_callback internally for UI messages
            chunk_files = file_service.split_audio_file(audio_file_path, temp_dir, progress_callback)
            if not chunk_files or len(chunk_files) == 0:
                # Error logged by split_audio_file via callback
                raise Exception("Audio splitting failed or resulted in no chunks.")

            total_chunks = len(chunk_files)
            logging.info(f"{log_prefix} Starting transcription of {total_chunks} chunks...") # Console log only
            # No separate UI message needed here, chunk processing messages will follow

            max_workers = max(1, int(getattr(Config, 'OPENROUTER_MAX_CONCURRENCY', 4)))
            max_workers = min(max_workers, total_chunks) # Do not exceed total chunks
            results: list[Optional[Tuple[str, str]]] = [None] * total_chunks
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

            full_transcription = " ".join(text for text, _ in results if text)
            if not full_transcription.strip():
                raise ValueError("OpenRouter returned no transcription text for any audio chunk.")
            # Console log only
            logging.info(f"{log_prefix} Successfully aggregated transcriptions from {total_chunks} chunks.")

            # Use the most frequent detected chunk language, with ties in audio order.
            languages = [language for _, language in results if language != 'unknown']
            final_language_used = Counter(languages).most_common(1)[0][0] if languages else 'unknown'
            log_lang_msg = f"Chunked transcription aggregated. Detected language: {final_language_used}"
            ui_lang_msg = f"Aggregated chunk transcriptions. Detected language: {final_language_used}"

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
                                            context_prompt: str = "", log_prefix: str = "", max_retries: int = 3
                                            ) -> Optional[Tuple[str, str]]:
        """Transcribes a chunk with retries, returning text and detected language."""
        last_error = None
        chunk_base_name = os.path.basename(chunk_path)
        effective_log_prefix = log_prefix or f"[{self.API_NAME}:Chunk{idx}]"

        for attempt in range(max_retries):
            try:
                abs_chunk_path = os.path.abspath(chunk_path)
                temp_dir = os.path.dirname(abs_chunk_path)
                if not file_service.validate_file_path(abs_chunk_path, temp_dir):
                    raise ValueError(f"Chunk file path is not allowed: {abs_chunk_path}")

                api_params = {"model": self.MODEL_NAME}
                if language_code and language_code != 'auto':
                    api_params["language"] = language_code

                start_time = time.time()
                logging.info(f"{effective_log_prefix} Attempt {attempt+1}: Calling OpenRouter API...")
                # Reopen the file on every attempt so retries upload all its bytes.
                with open(abs_chunk_path, "rb") as audio_file:
                    with requests.post(
                        "https://openrouter.ai/api/v1/audio/transcriptions",
                        headers={"Authorization": f"Bearer {self.api_key}"},
                        data=api_params,
                        files={"file": (chunk_base_name, audio_file)},
                        timeout=(30, 600),
                    ) as response:
                        response.raise_for_status()
                        result = response.json()

                if not isinstance(result, dict) or not isinstance(result.get("text"), str):
                    raise ValueError("OpenRouter returned an invalid transcription response.")
                text = result["text"]
                detected_language = result.get("language")
                if not isinstance(detected_language, str) or not detected_language.strip():
                    detected_language = language_code if language_code and language_code != 'auto' else 'unknown'
                logging.info(
                    "%s Attempt %s: API call successful. Request time: %.2fs. Audio usage: %s seconds",
                    effective_log_prefix, attempt + 1, time.time() - start_time, (result.get("usage") or {}).get("seconds", result.get("duration")),
                )
                return text.strip(), detected_language

            except requests.HTTPError as e:
                last_error = e
                status = e.response.status_code if e.response is not None else None
                # Authentication, invalid input, etc. will not improve with retries.
                if status != 429 and (status is None or status < 500):
                    error_detail = f"ERROR: OpenRouter rejected chunk {idx}: {e}"
                    logging.error("%s %s", effective_log_prefix, error_detail)
                    if progress_callback: progress_callback(error_detail, True)
                    return None
            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = e
            except (ValueError, OSError, requests.RequestException) as e:
                last_error = e
                break
            except Exception as e:
                last_error = e
                logging.exception(f"{effective_log_prefix} Unexpected error detail on attempt {attempt+1}:")
                break

            if attempt + 1 < max_retries:
                wait_time = 2 ** attempt
                error_detail = f"API error on chunk {idx} (Attempt {attempt+1}). Retrying in {wait_time}s..."
                if progress_callback: progress_callback(error_detail, False)
                logging.warning(f"{effective_log_prefix} {error_detail} ({last_error})")
                time.sleep(wait_time)

        final_error_msg = f"ERROR: Chunk {idx} ('{chunk_base_name}') failed after {attempt + 1} attempts. Last error: {last_error}"
        if progress_callback: progress_callback(final_error_msg, True)
        logging.error(f"{effective_log_prefix} {final_error_msg}")
        return None
