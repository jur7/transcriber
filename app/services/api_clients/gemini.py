# app/services/api_clients/gemini.py

import os
import logging
import time
from typing import Tuple, Optional, Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

# Google Gemini (google.genai) client
from google import genai
from google.genai import types as genai_types

from google.api_core.exceptions import GoogleAPIError, ResourceExhausted, ServiceUnavailable, InternalServerError
from google.api_core.exceptions import DeadlineExceeded, InvalidArgument, PermissionDenied, Unauthenticated 

# Error classes (fallback shims if google.api_core isn't present yet)
#try:
#    from google.api_core.exceptions import (
#        GoogleAPIError,
#        ResourceExhausted,
#        ServiceUnavailable,
#        InternalServerError,
#        DeadlineExceeded,
#        InvalidArgument,
#        PermissionDenied,
#        Unauthenticated,
#    )
#except Exception:  # pragma: no cover - fallback for type hints when package missing
#    class GoogleAPIError(Exception): pass
#    class ResourceExhausted(GoogleAPIError): pass
#    class ServiceUnavailable(GoogleAPIError): pass
#    class InternalServerError(GoogleAPIError): pass
#    class DeadlineExceeded(GoogleAPIError): pass
#    class InvalidArgument(GoogleAPIError): pass
#    class PermissionDenied(GoogleAPIError): pass
#    class Unauthenticated(GoogleAPIError): pass

from app.services import file_service
from app.config import Config

# Define a type hint for the progress callback
ProgressCallback = Optional[Callable[[str, bool], None]]  # Message, IsError


def _guess_mime_type(file_path: str) -> str:
    """Very small helper to guess common audio MIME types from extension."""
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    if ext in ("mp3",):
        return "audio/mpeg"
    if ext in ("m4a", "mp4", "aac"):
        return "audio/mp4"
    if ext in ("wav",):
        return "audio/wav"
    if ext in ("ogg",):
        return "audio/ogg"
    if ext in ("webm",):
        return "audio/webm"
    # Fallback generic
    return "application/octet-stream"


class GeminiTranscriptionAPI:
    """
    Integration with Google Gemini 2.5 Pro using google.genai.
    Mirrors the structure and behavior of OpenAIGPT4oTranscriptionAPI:
    - Handles large file splitting and parallel chunk transcription.
    - Reports progress via callback with the same simple UI messages.
    - Uses inline audio bytes (no separate upload step).
    """

    MODEL_NAME = os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-pro")
    API_NAME = "Gemini_2.5_Pro"

    def __init__(self, api_key: Optional[str] = None) -> None:
        """Initializes the Gemini API client for either Google AI or Vertex AI."""
        provider = (getattr(Config, "GEMINI_PROVIDER", "google") or "google").strip().lower()

        # Initialize client depending on provider
        try:
            if provider in ("google", "gemini"):
                # Prefer explicit key if passed, else from Config
                use_key = api_key or getattr(Config, "GEMINI_API_KEY", None) or os.environ.get("GEMINI_API_KEY")
                if not use_key:
                    logging.error(f"[{self.API_NAME}] API key is required for Google AI provider but not provided.")
                    raise ValueError("Gemini API key is required for Google AI provider.")
                self.client = genai.Client(api_key=use_key)
                self.provider = "google"
                logging.info(f"[{self.API_NAME}] Client initialized for Google AI with model {self.MODEL_NAME}.")
            elif provider == "vertex":
                project = getattr(Config, "VERTEXAI_PROJECT_ID", None) or os.environ.get("VERTEXAI_PROJECT_ID")
                location = getattr(Config, "VERTEXAI_LOCATION", None) or os.environ.get("VERTEXAI_LOCATION")
                if not project or not location:
                    logging.error(f"[{self.API_NAME}] Vertex configuration missing project/location.")
                    raise ValueError("Vertex provider requires VERTEXAI_PROJECT_ID and VERTEXAI_LOCATION.")
                self.client = genai.Client(vertex={"project": project, "location": location})
                self.provider = "vertex"
                logging.info(f"[{self.API_NAME}] Client initialized for Vertex AI with model {self.MODEL_NAME}.")
            else:
                logging.error(f"[{self.API_NAME}] Unknown GEMINI_PROVIDER: {provider}")
                raise ValueError(f"Invalid GEMINI_PROVIDER: {provider}")
        except GoogleAPIError as e:
            logging.error(f"[{self.API_NAME}] Failed to initialize Gemini client: {e}")
            raise ValueError(f"Gemini client initialization failed: {e}") from e
        except Exception as e:
            logging.error(f"[{self.API_NAME}] Unexpected error initializing client: {e}")
            raise

    def transcribe(
        self,
        audio_file_path: str,
        language_code: str,
        progress_callback: ProgressCallback = None,
        context_prompt: str = "",
        original_filename: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Transcribes the audio file using Gemini. Handles splitting similarly to GPT-4o client.

        Returns:
            (transcription_text, detected_language) or (None, None) on failure.
        """
        requested_language = language_code
        display_filename = original_filename or os.path.basename(audio_file_path)
        log_prefix = f"[{self.API_NAME}:{display_filename}]"

        transcription_text = None
        final_language_used = None

        try:
            if not os.path.exists(audio_file_path):
                msg = f"ERROR: Audio file not found at path: {audio_file_path}"
                if progress_callback:
                    progress_callback(msg, True)
                logging.error(f"{log_prefix} {msg}")
                return None, None

            file_size = os.path.getsize(audio_file_path)
            file_length = file_service.get_audio_file_length(audio_file_path)

            # Use same limits and behavior as GPT-4o client for consistency
            if file_size > file_service.OPENAI_MAX_FILE_SIZE or file_length > file_service.OPENAI_MAX_LENGTH_MS_4O:
                if file_size > file_service.OPENAI_MAX_FILE_SIZE:
                    logging.info(f"{log_prefix} File size ({file_size / 1024 / 1024:.2f}MB) exceeds limit. Starting chunked transcription.")
                else: logging.info(f"{log_prefix} File length ({file_length / 1000:.2f}sec) exceeds limit. Starting chunked transcription.")

                # The splitting function will send its own UI messages
                return self._split_and_transcribe(audio_file_path, requested_language, progress_callback, context_prompt, display_filename,)
            else:
                logging.info(f"{log_prefix} File within limits. Processing as single file.")
                # Single file transcription
                abs_path = os.path.abspath(audio_file_path)
                temp_dir = os.path.dirname(abs_path)
                # Validate path is within expected temp dir
                if not file_service.validate_file_path(abs_path, temp_dir):
                    msg = f"ERROR: Audio file path is not allowed or outside expected directory: {abs_path}"
                    if progress_callback: progress_callback(msg, True)
                    logging.error(f"{log_prefix} {msg}")
                    raise ValueError(msg)

                if progress_callback:
                    progress_callback(f"Transcribing with Google {self.MODEL_NAME}...", False)

                chunk_text = self._transcribe_single_chunk_with_retry(
                    abs_path,
                    1,
                    1,
                    requested_language,
                    progress_callback,
                    context_prompt,
                    f"{log_prefix}:Single",
                )
                if chunk_text is None:
                    return None, None

                transcription_text = chunk_text

            # Language reporting mirrors GPT-4o client
            if requested_language == "auto":
                final_language_used = "en"
                log_lang_msg = ("Transcription finished. Language detected implicitly (logged as 'en' default for 'auto' request).")
                ui_lang_msg = f"Gemini {self.MODEL_NAME} transcription finished. Language detected implicitly by model."
            else:
                final_language_used = requested_language
                log_lang_msg = f"Transcription finished. Used requested language: {final_language_used}"
                ui_lang_msg = (f"Gemini {self.MODEL_NAME} transcription finished. Used requested language: {final_language_used}")

            logging.info(f"{log_prefix} {log_lang_msg}")
            if progress_callback:
                progress_callback(ui_lang_msg, False)
                progress_callback("Transcription completed.", False)

            return transcription_text, final_language_used

        except FileNotFoundError as fnf_error:
            error_msg = f"ERROR: Audio file disappeared: {fnf_error}"
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}")
            return None, None
        except ResourceExhausted as rle:  # Rate/Quota limits
            error_msg = f"ERROR: Gemini API rate limit exceeded: {rle}. Please try again later."
            if progress_callback: progress_callback(error_msg, True)
            logging.warning(f"{log_prefix} {error_msg}")
            return None, None
        except (ServiceUnavailable, InternalServerError, DeadlineExceeded) as ace:
            error_msg = f"ERROR: Gemini API connection/service error: {ace}. Check network or try again."
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}")
            return None, None
        except (InvalidArgument, PermissionDenied, Unauthenticated, GoogleAPIError) as apie:
            error_msg = f"ERROR: Gemini API returned an error: {apie}"
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}")
            return None, None
        except ValueError as ve:
            error_msg = f"ERROR: Input Error: {ve}"
            if progress_callback: progress_callback(error_msg, True)
            logging.error(f"{log_prefix} {error_msg}")
            return None, None
        except Exception as e:
            error_msg = f"ERROR: Unexpected error during {self.API_NAME} transcription: {e}"
            if progress_callback: progress_callback(error_msg, True)
            logging.exception(f"{log_prefix} Unexpected error detail:")
            return None, None

    def _split_and_transcribe(
        self,
        audio_file_path: str,
        language_code: str,
        progress_callback: ProgressCallback = None,
        context_prompt: str = "",
        display_filename: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """Handles splitting large files and transcribing chunks using parallel workers."""
        requested_language = language_code
        log_prefix = f"[{self.API_NAME}:{display_filename or os.path.basename(audio_file_path)}]"

        temp_dir = os.path.dirname(audio_file_path)
        chunk_files = []
        final_language_used = None

        try:
            chunk_files = file_service.split_audio_file(audio_file_path, temp_dir, progress_callback)
            if not chunk_files or len(chunk_files) == 0:
                raise Exception("Audio splitting failed or resulted in no chunks.")

            total_chunks = len(chunk_files)
            logging.info(f"{log_prefix} Starting transcription of {total_chunks} chunks...")

            max_workers = max(1, int(getattr(Config, "GEMINI_MAX_CONCURRENCY", getattr(Config, "OPENAI_MAX_CONCURRENCY", 3))))
            max_workers = min(max_workers, total_chunks)  # Do not exceed total chunks
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
                log_lang_msg = f"Chunked transcription aggregated. Used requested language: {final_language_used}"
                ui_lang_msg = f"Aggregated chunk transcriptions. Used requested language: {final_language_used}"

            logging.info(f"{log_prefix} {log_lang_msg}")
            if progress_callback:
                progress_callback(ui_lang_msg, False)
                progress_callback("Transcription completed.", False)

            return full_transcription, final_language_used

        except Exception as e:
            error_msg = f"ERROR: Error during split and transcribe process: {e}"
            if progress_callback: progress_callback(error_msg, True)
            logging.exception(f"{log_prefix} Error detail in _split_and_transcribe:")
            return None, None
        finally:
            if chunk_files:
                if progress_callback: progress_callback("Cleaning up temporary chunk files...", False)
                removed_count = file_service.remove_files(chunk_files)
                logging.info(f"{log_prefix} Cleaned up {removed_count} temporary chunk file(s).")
                if progress_callback: progress_callback(f"Cleaned up {removed_count} temporary chunk file(s).", False)


    def _transcribe_single_chunk_with_retry(
        self,
        chunk_path: str,
        idx: int,
        total_chunks: int,
        language_code: str,
        progress_callback: ProgressCallback = None,
        context_prompt: str = "",
        log_prefix: str = "",
        max_retries: int = 3,
    ) -> Optional[str]:
        """
        Transcribes a single chunk with retry logic using Gemini. Reports progress via callback.

        Returns: Transcription text string or None on failure.
        """
        requested_language = language_code
        last_error = None
        chunk_base_name = os.path.basename(chunk_path)
        effective_log_prefix = log_prefix or f"[{self.API_NAME}:Chunk{idx}]"

        for attempt in range(max_retries):
            try:
                abs_chunk_path = os.path.abspath(chunk_path)
                temp_dir = os.path.dirname(abs_chunk_path)
                if not file_service.validate_file_path(abs_chunk_path, temp_dir):
                    msg = f"Chunk file path is not allowed: {abs_chunk_path}"
                    logging.error(f"{effective_log_prefix} {msg}")
                    raise ValueError(msg)

                with open(abs_chunk_path, "rb") as audio_file:
                    audio_bytes = audio_file.read()

                mime_type = _guess_mime_type(abs_chunk_path)

                # Build a concise instruction prompt. Include context and language guidance.
                instructions = [
                    "Transcribe the following audio to plain text without timestamps.",
                ]
                if context_prompt:
                    instructions.append(f"Context: {context_prompt}")
                if requested_language and requested_language != "auto":
                    instructions.append(
                        f"The expected language is '{requested_language}'. Do not translate. If audio is in other language, transcribe verbatim."
                    )

                contents = [
                    "\n".join(instructions),
                    genai_types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
                ]

                # Log the call (console only)
                if requested_language == "auto": lang_note = " (Lang: 'auto' requested - implicit detection)"  
                else: lang_note = f" (Lang: '{requested_language}')"
                logging.info(f"{effective_log_prefix} Attempt {attempt+1}: Calling Gemini API...{lang_note}")
                start_time = time.time()
                response = self.client.models.generate_content(model=self.MODEL_NAME, contents=contents)
                duration = time.time() - start_time

                text = getattr(response, "text", None)
                logging.info(f"{effective_log_prefix} Attempt {attempt+1}: API call successful. Duration: {duration:.2f}s")
                return text.strip() if text else ""

            # --- Exception Handling for Retries ---
            except ResourceExhausted as rle:
                last_error = rle
                wait_time = 2 ** attempt
                 # SIMPLE UI Message for retry
                if progress_callback: progress_callback(f"Rate limit hit on chunk {idx}, attempt {attempt+1}. Retrying in {wait_time}s...", False,)
                logging.warning(f"{effective_log_prefix} Rate limit hit, attempt {attempt+1}. Retrying in {wait_time}s... ({rle})")
                time.sleep(wait_time)
            except (ServiceUnavailable, InternalServerError, DeadlineExceeded) as e:
                last_error = e
                wait_time = 2 ** attempt
                 # SIMPLE UI Message for retry
                if progress_callback: progress_callback(f"API error on chunk {idx} (Attempt {attempt+1}). Retrying in {wait_time}s...", False,)
                logging.error(f"{effective_log_prefix} API error on chunk {idx}, attempt {attempt+1}: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            except (InvalidArgument, PermissionDenied, Unauthenticated, GoogleAPIError) as ge:
                last_error = ge
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Gemini SDK/API error on chunk {idx}: {ge}"
                if progress_callback: progress_callback(error_detail, True)
                logging.error(f"{effective_log_prefix} Gemini SDK/API error on chunk {idx}, attempt {attempt+1}: {ge}")
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
                error_detail = (f"ERROR: Chunk file not found: {chunk_base_name}. Error: {fnf_error}")
                if progress_callback: progress_callback(error_detail, True)
                logging.error(f"{effective_log_prefix} Chunk file not found on attempt {attempt+1}: {chunk_base_name}. Error: {fnf_error}")
                break
            except Exception as e:
                last_error = e
                # SIMPLE UI Message for fatal error
                error_detail = f"ERROR: Unexpected error transcribing chunk {idx}: {e}"
                if progress_callback: progress_callback(error_detail, True)
                logging.exception(f"{effective_log_prefix} Unexpected error detail on attempt {attempt+1}:")
                break
            # --- End of Exception Handling for Retries ---

        final_error_msg = (
            f"ERROR: Chunk {idx} ('{chunk_base_name}') failed after {max_retries} attempts. Last error: {last_error}"
        )
        if progress_callback:
            progress_callback(final_error_msg, True)
        logging.error(
            f"{effective_log_prefix} Chunk {idx} failed after {max_retries} attempts. Last error: {last_error}"
        )
        return None
