/* app/static/js/main.js */

document.addEventListener('DOMContentLoaded', function() {
  // Initialize Materialize components
  var elems = document.querySelectorAll('select');
  M.FormSelect.init(elems);
  var tooltips = document.querySelectorAll('.tooltipped'); // Initialize any static tooltips
  M.Tooltip.init(tooltips);

  // Load existing transcriptions on page load
  loadTranscriptions();

  // Map API codes to display names
  var apiNameMap = {
    'gpt4o': 'OpenAI GPT4o Transcribe',
    'assemblyai': 'AssemblyAI',
    'whisper': 'OpenAI Whisper',
    'gemini': 'Gemini 2.5 Pro'
  };

  // Handle API selection change to show/hide context prompt
  var apiSelect = document.getElementById('apiSelect');
  var contextPromptContainer = document.getElementById('contextPromptContainer');
  apiSelect.addEventListener('change', function() {
      if (this.value === 'gpt4o' || this.value === 'whisper' || this.value === 'gemini') {
          contextPromptContainer.style.display = 'block';
      } else {
          contextPromptContainer.style.display = 'none';
      }
  });
  // Trigger change event on load to set initial state
  apiSelect.dispatchEvent(new Event('change'));


  // Handle Transcribe button click
  document.getElementById('transcribeBtn').addEventListener('click', function() {
    var fileInput = document.getElementById('audioFile');
    var languageSelect = document.getElementById('languageSelect');
    var apiSelect = document.getElementById('apiSelect');
    var file = fileInput.files[0];
    var languageCode = languageSelect.value;
    var apiChoice = apiSelect.value;

    // Basic file validation
    if (!file) {
      M.toast({html: 'Please select an audio or video file.', classes: 'red'});
      return;
    }
    // Validate type/extension defensively
    if (!((file.type && (file.type.indexOf('audio/') === 0 || file.type.indexOf('video/') === 0)) || /\.(mp3|m4a|wav|ogg|webm|mp4|mov|mkv|avi|flv|wmv)$/i.test(file.name || ''))) {
      M.toast({html: 'Only audio or video files are allowed.', classes: 'red'});
      return;
    }
    // Update progress display placeholders
    document.getElementById('progressFile').textContent = file.name;
    document.getElementById('progressService').textContent = apiNameMap[apiChoice] || apiChoice;
    // Use SUPPORTED_LANGUAGE_MAP for display name
    const displayLanguage = (typeof SUPPORTED_LANGUAGE_MAP !== 'undefined' && SUPPORTED_LANGUAGE_MAP[languageCode])
                           ? SUPPORTED_LANGUAGE_MAP[languageCode]
                           : languageCode; // Fallback to code
    document.getElementById('progressLanguage').textContent = displayLanguage;

    document.getElementById('progressActivity').innerHTML =
      '<i class="material-icons left">hourglass_empty</i> Starting...';

    // Prepare form data
    var formData = new FormData();
    formData.append('audio_file', file);
    formData.append('language_code', languageCode);
    formData.append('api_choice', apiChoice);

    // Add context prompt if applicable and validate length
    if (apiChoice === 'gpt4o' || apiChoice === 'whisper') {
        var contextPrompt = document.getElementById('contextPrompt').value;
        var words = contextPrompt.match(/\S+/g) || [];
        if (words.length > 120) {
            M.toast({html: 'Context prompt exceeds 120 word limit. Please shorten your prompt.', classes: 'red'});
            // Re-enable button and return if validation fails
            document.getElementById('transcribeBtn').disabled = false;
            return;
        }
        formData.append('context_prompt', contextPrompt);
    }

    // Update UI for processing state
    document.querySelector('.progress').style.display = 'block'; // Show indeterminate progress bar
    document.getElementById('transcribeBtn').disabled = true;
    document.getElementById('progressContainer').style.display = 'block'; // Show progress details box
    // Clear previous progress messages shown in UI if any
    document.getElementById('progressActivity').dataset.lastMessageIndex = -1;


    // Make API call to backend
    fetch('./api/transcribe', {
      method: 'POST',
      body: formData
    })
    .then(response => {
      if (!response.ok) {
        // Try to get error message from response body
        return response.json().then(errData => {
          throw new Error(errData.error || `Network response was not ok: ${response.statusText}`);
        }).catch(() => {
          // Fallback if response body is not JSON or empty
          throw new Error(`Network response was not ok: ${response.statusText}`);
        });
      }
      return response.json();
    })
    .then(data => {
      if (data.error) {
        // Handle specific errors returned by the backend (e.g., too many jobs)
        M.toast({html: 'Error: ' + data.error, classes: 'red'});
        document.querySelector('.progress').style.display = 'none';
        document.getElementById('transcribeBtn').disabled = false;
        document.getElementById('progressContainer').style.display = 'none'; // Hide progress box on immediate error
      } else if (data.job_id) {
        pollProgress(data.job_id); // Start polling if job started successfully
      } else {
         // Handle unexpected successful response without job_id
         throw new Error("Received success response but no Job ID.");
      }
    })
    .catch(error => {
      // Handle fetch errors or errors thrown from .then blocks
      console.error('Error starting transcription:', error);
      document.querySelector('.progress').style.display = 'none';
      document.getElementById('transcribeBtn').disabled = false;
      document.getElementById('progressContainer').style.display = 'none'; // Hide progress box on fetch error
      M.toast({html: 'An error occurred: ' + error.message, classes: 'red'});
    });
  }); // End of transcribeBtn click listener

  // Handle Clear All button click
  document.getElementById('clearAllBtn').addEventListener('click', function() {
    // --- CONFIRMATION ONLY HERE ---
    if (confirm('Are you sure you want to clear all transcriptions? This cannot be undone.')) {
      fetch('./api/transcriptions/clear', { method: 'DELETE' })
      .then(response => response.json())
      .then(data => {
        M.toast({html: data.message});
        // Clear history list and destroy tooltips
        const historyList = document.getElementById('transcriptionHistory');
        const tooltips = historyList.querySelectorAll('.tooltipped');
        tooltips.forEach(tip => {
            var instance = M.Tooltip.getInstance(tip);
            if (instance) instance.destroy();
        });
        historyList.innerHTML = ''; // Clear items
        document.getElementById('clearAllBtn').style.display = 'none'; // Hide button
      })
      .catch(error => {
        console.error('Error clearing transcriptions:', error);
        M.toast({html: 'An error occurred while clearing transcriptions.'});
      });
    }
  }); // End of clearAllBtn click listener

}); // End of DOMContentLoaded listener

/**
 * Capitalizes the first letter of a string.
 * Handles null/undefined input.
 * @param {string} string - The input string.
 * @returns {string} The capitalized string or the original input if invalid.
 */
function capitalizeFirstLetter(string) {
  // No longer lowercasing the rest, just capitalize first letter
  if (!string || typeof string !== 'string') return string;
  return string.charAt(0).toUpperCase() + string.slice(1);
}


/**
 * Validates the context prompt length in real-time.
 */
function validateContextPrompt() {
  var contextField = document.getElementById('contextPrompt');
  var errorSpan = document.getElementById('contextPromptError');
  var words = contextField.value.match(/\S+/g) || [];
  if (words.length > 120) {
      errorSpan.textContent = `Context prompt exceeds 120 word limit (${words.length}/120). Please shorten your prompt.`;
      errorSpan.style.color = "red";
      contextField.classList.add("invalid");
      // Don't automatically truncate, let user fix it
  } else {
      errorSpan.textContent = `${words.length}/120 words`; // Show current count
      errorSpan.style.color = ""; // Reset color
      contextField.classList.remove("invalid");
  }
}

/**
 * Fetches all transcriptions from the backend and displays them.
 */
function loadTranscriptions() {
  fetch('./api/transcriptions')
  .then(response => {
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      return response.json();
    })
  .then(transcriptions => {
    // transcriptions are already sorted DESC by backend
    const historyList = document.getElementById('transcriptionHistory');
    historyList.innerHTML = ''; // Clear existing items before loading

    if (transcriptions.length > 0) {
      transcriptions.forEach(transcription => {
        // Only add completed items during initial load
        if (transcription.status === 'finished' || transcription.status === 'error' || !transcription.status) { // Handle old items without status
             addTranscriptionToHistory(transcription, false); // Append items during initial load
        }
      });
      document.getElementById('clearAllBtn').style.display = 'inline-block'; // Show clear button
    } else {
       document.getElementById('clearAllBtn').style.display = 'none'; // Ensure hidden if empty
       // Optionally display a message indicating no history
       // historyList.innerHTML = '<li class="collection-item grey-text">No transcriptions yet.</li>';
    }
  })
  .catch(error => {
    console.error('Error loading transcriptions:', error);
    M.toast({html: 'An error occurred while loading transcriptions.'});
  });
}

/**
 * Adds a single transcription item to the history list UI.
 * @param {object} transcription - The transcription data object.
 * @param {boolean} [prepend=true] - Whether to add the item to the beginning (true) or end (false) of the list.
 */
function addTranscriptionToHistory(transcription, prepend = true) {
  // Skip adding if it's still processing (should only happen if called directly, loadTranscriptions filters)
  if (transcription.status && transcription.status !== 'finished' && transcription.status !== 'error') {
      console.log("Skipping adding incomplete transcription to history:", transcription.id);
      return;
  }

  console.log("Adding transcription to history:", transcription);

  // --- MODIFIED Date/Time Formatting ---
  let formattedDateTime = 'Date unavailable';
  try {
      // Backend now saves UTC string ending in 'Z'
      const date = new Date(transcription.created_at);
      if (!isNaN(date)) { // Check if date is valid
        // Use localeString with options for dd/mm/yyyy h:mm AM/PM style
        // Requesting specific locale ('en-GB' for dd/mm/yyyy) and options
        formattedDateTime = date.toLocaleString('en-GB', { // Use specific locale for date format
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: 'numeric', // Use 'numeric' or '2-digit'
            minute: '2-digit',
            hour12: true // Use AM/PM
        }).replace(',', '') // Remove comma often inserted between date and time
          .replace(' am', ' AM') // Force uppercase AM/PM
          .replace(' pm', ' PM');
      }
  } catch (e) {
      console.error("Error parsing date:", transcription.created_at, e);
  }
  // --- END Date/Time Formatting ---


  const apiNameMap = {
      'gpt4o': 'OpenAI GPT4o Transcribe',
      'assemblyai': 'AssemblyAI',
      'whisper': 'OpenAI Whisper'
  };
  const apiName = apiNameMap[transcription.api_used] || transcription.api_used;

  // Look up full language name and capitalize
  const detectedLangCode = transcription.detected_language || 'N/A';
  let languageDisplayName = detectedLangCode; // Fallback to code
  // Ensure SUPPORTED_LANGUAGE_MAP is available
  if (typeof SUPPORTED_LANGUAGE_MAP !== 'undefined' && SUPPORTED_LANGUAGE_MAP[detectedLangCode]) {
      languageDisplayName = SUPPORTED_LANGUAGE_MAP[detectedLangCode];
  }
  // Capitalize the display name (handles "English", "Automatic Detection", etc.)
  const languageName = capitalizeFirstLetter(languageDisplayName);


  var transcriptionItem = document.createElement('li');
  // Use standard collection item
  transcriptionItem.classList.add('collection-item');
  transcriptionItem.dataset.transcriptionId = transcription.id; // Store ID for potential future use
  // Use error message if transcription text is missing and status is error
  const itemText = (transcription.status === 'error' && !transcription.transcription_text)
                   ? `[Error: ${transcription.error_message || 'Unknown error'}]`
                   : (transcription.transcription_text || "[Transcription not available]");
  // STORE FULL TEXT IN DATASET
  transcriptionItem.dataset.fullText = itemText;


  // --- MODIFIED Meta Line ---
  // Build inner HTML structure - Moved secondary-content to the top
  var contentHTML = `
      <div> <!-- Wrapper div -->
          <div class="secondary-content history-item-actions"> <!-- Moved actions here -->
              <button class="btn-flat waves-effect waves-light copy-btn tooltipped" data-position="top" data-tooltip="Copy Text" style="padding: 0 0.5rem;">
                  <i class="material-icons">content_copy</i>
              </button>
              <button class="btn-flat waves-effect waves-light download-btn tooltipped" data-position="top" data-tooltip="Download .txt" style="padding: 0 0.5rem;">
                  <i class="material-icons">download</i>
              </button>
              <button class="btn-flat waves-effect waves-light delete-btn tooltipped" data-position="top" data-tooltip="Delete" style="padding: 0 0.5rem;">
                  <i class="material-icons">delete</i>
              </button>
          </div>

          <b>${transcription.filename || 'Unknown Filename'}</b>
          <p class="meta grey-text text-darken-1">
              ${apiName} | ${languageName} | ${formattedDateTime}
              ${transcription.status === 'error' ? '<span class="red-text"> (Failed)</span>' : ''}
          </p>
          <p class="transcription-text grey-text text-darken-3"></p>
      </div>
  `; // Placeholder for text, will be filled below
  // --- END MODIFIED Meta Line ---

  transcriptionItem.innerHTML = contentHTML; // Set the basic structure

  // Select the text element and populate it safely, adding 'Read More' if needed
  var transcriptionTextElement = transcriptionItem.querySelector('.transcription-text');
  const fullTextForDisplay = transcriptionItem.dataset.fullText; // Get text (could be error message)
  const words = fullTextForDisplay.split(/\s+/).filter(word => word.length > 0);
  // USE ORIGINAL PREVIEW LENGTH
  const previewLength = 140; // Number of words for preview

  // Only add Read More if it's not an error message and text is long
  if (transcription.status !== 'error' && words.length > previewLength) {
      const truncatedText = words.slice(0, previewLength).join(' ') + '...';
      transcriptionTextElement.textContent = truncatedText; // Use textContent for safety
      var readMoreLink = document.createElement('a');
      readMoreLink.href = '#!';
      readMoreLink.classList.add('read-more', 'blue-text', 'text-darken-2');
      readMoreLink.textContent = ' Read More'; // Add space
      readMoreLink.style.fontSize = '0.9em';
      // Insert link after the paragraph element
      transcriptionTextElement.parentNode.insertBefore(readMoreLink, transcriptionTextElement.nextSibling);

      readMoreLink.addEventListener('click', function(e) {
          e.preventDefault(); // Prevent page jump
          if (transcriptionTextElement.textContent === truncatedText) {
              transcriptionTextElement.textContent = fullTextForDisplay; // Use textContent
              readMoreLink.textContent = ' Read Less';
          } else {
              transcriptionTextElement.textContent = truncatedText; // Use textContent
              readMoreLink.textContent = ' Read More';
          }
      });
  } else {
      transcriptionTextElement.textContent = fullTextForDisplay; // Use textContent for safety (shows full error or short text)
      if (transcription.status === 'error') {
          transcriptionTextElement.classList.add('red-text'); // Make error text red
      }
  }

  // Add event listeners to buttons
  // Disable copy/download if it was an error with no text
  const disableActions = transcription.status === 'error' && !transcription.transcription_text;
  const copyBtn = transcriptionItem.querySelector('.copy-btn');
  const downloadBtn = transcriptionItem.querySelector('.download-btn');

  copyBtn.disabled = disableActions;
  downloadBtn.disabled = disableActions;

  copyBtn.addEventListener('click', function() {
      if (this.disabled) return;
      // ENSURE COPYING FROM DATASET
      const textToCopy = transcriptionItem.dataset.fullText;
      console.log("Attempting to copy full text from dataset:", textToCopy); // Verify correct text is targeted
      copyToClipboard(textToCopy);
  });
  downloadBtn.addEventListener('click', function() {
      if (this.disabled) return;
      const text = transcriptionItem.dataset.fullText;
      const baseFilename = (transcription.filename || 'transcription').replace(/\.[^/.]+$/, ""); // Remove extension safely
      downloadTranscription(text, baseFilename + "_transcription");
  });
  transcriptionItem.querySelector('.delete-btn').addEventListener('click', function() {
      // NO CONFIRMATION HERE
      deleteTranscription(transcription.id, transcriptionItem);
  });


  // Add item to the list
  var historyList = document.getElementById('transcriptionHistory');
  if (prepend) {
    historyList.insertBefore(transcriptionItem, historyList.firstChild);
  } else {
    historyList.appendChild(transcriptionItem); // Append for initial load
  }
  document.getElementById('clearAllBtn').style.display = 'inline-block'; // Ensure clear button is visible

  // Initialize tooltips for the newly added item
  var tooltippedElems = transcriptionItem.querySelectorAll('.tooltipped');
  M.Tooltip.init(tooltippedElems);
}

/**
 * Copies the given text to the clipboard.
 * Uses modern Clipboard API with fallback.
 * @param {string} text - The text to copy.
 */
function copyToClipboard(text) {
  if (!navigator.clipboard) {
    // Fallback for older browsers or insecure contexts
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed'; // Prevent scrolling to bottom
    textarea.style.opacity = '0'; // Hide the textarea
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    try {
      var successful = document.execCommand('copy');
      if (successful) {
        M.toast({html: 'Copied to clipboard!'});
      } else {
        M.toast({html: 'Failed to copy text (fallback method).'});
      }
    } catch (err) {
      console.error('Fallback copy error:', err);
      M.toast({html: 'Failed to copy text'});
    }
    document.body.removeChild(textarea);
    return;
  }
  // Modern async clipboard API
  navigator.clipboard.writeText(text).then(function() {
    M.toast({html: 'Copied to clipboard!'});
  }, function(err) {
    console.error('Async copy error:', err);
    M.toast({html: 'Failed to copy text: ' + err});
  });
}

/**
 * Triggers a browser download for the given text as a .txt file.
 * @param {string} text - The text content for the file.
 * @param {string} filename - The desired filename (without extension).
 */
function downloadTranscription(text, filename) {
  var element = document.createElement('a');
  // Ensure text is not null/undefined before encoding
  const content = text || "";
  element.setAttribute('href', 'data:text/plain;charset=utf-8,' + encodeURIComponent(content));
  element.setAttribute('download', filename + '.txt');
  element.style.display = 'none';
  document.body.appendChild(element);
  element.click();
  document.body.removeChild(element);
}

/**
 * Sends a request to delete a transcription and removes it from the UI.
 * @param {string} transcriptionId - The ID of the transcription to delete.
 * @param {HTMLElement} transcriptionItem - The list item element to remove.
 */
function deleteTranscription(transcriptionId, transcriptionItem) {
  // REMOVED CONFIRMATION

  // Remove tooltips associated with this item before deleting the item
  var tooltips = transcriptionItem.querySelectorAll('.tooltipped');
  tooltips.forEach(tip => {
    var instance = M.Tooltip.getInstance(tip);
    if (instance) {
      instance.destroy();
    }
  });

  fetch(`./api/transcriptions/${transcriptionId}`, { method: 'DELETE' })
  .then(response => {
      if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
      return response.json();
    })
  .then(data => {
      M.toast({html: data.message || 'Transcription deleted.'});
      transcriptionItem.remove(); // Remove item from list
      // Check if history is now empty
      if (document.getElementById('transcriptionHistory').children.length === 0) {
          document.getElementById('clearAllBtn').style.display = 'none';
      }
  })
  .catch(error => {
      console.error('Error deleting transcription:', error);
      M.toast({html: 'An error occurred while deleting the transcription.'});
  });
}


/**
 * Polls the backend for the progress of a specific transcription job.
 * Updates the UI with the latest status message.
 * @param {string} jobId - The ID of the job to poll.
 */
function pollProgress(jobId) {
  const progressElement = document.getElementById('progressActivity');
  // Store the index of the last message shown to avoid repetition
  let lastMessageIndex = -1; // Use -1 to ensure the first message (index 0) is shown
  let jobIsFinished = false; // Flag to prevent UI updates after final state

  var interval = setInterval(function() {
      // If job is marked finished in UI, stop polling (safety check)
      if (jobIsFinished) {
          clearInterval(interval);
          return;
      }

      fetch('./api/progress/' + jobId)
      .then(response => {
          if (!response.ok) {
              // Handle HTTP errors during polling (like 404 Not Found)
              throw new Error(`Polling failed: ${response.statusText}`); // Use statusText
          }
          return response.json();
      })
      .then(jobData => {
          // NOTE: jobData structure depends on the backend ./api/progress response
          // Assuming it returns { status: '...', progress: [...], error_message: '...', result: {...} }

          // Process and display new progress messages from the log
          const progressLog = jobData.progress || []; // Use progress log from DB
          if (progressLog.length > 0) {
              // Iterate through messages we haven't shown yet
              for (let i = lastMessageIndex + 1; i < progressLog.length; i++) {
                  const message = progressLog[i];
                  if (!message) continue; // Skip null/empty messages

                  let icon = "info_outline"; // Default icon

                  // Determine icon based on message content (case-insensitive)
                  const lowerMessage = message.toLowerCase();
                  if (lowerMessage.includes("silence")) icon = "blur_linear";
                  else if (lowerMessage.includes("split")) icon = "call_split";
                  // USE 'layers' ICON FOR CHUNK CREATION
                  else if (lowerMessage.includes("created") && lowerMessage.includes("chunk")) icon = "layers";
                  else if (lowerMessage.includes("extracting") && lowerMessage.includes("video")) icon = "local_movies";
                  else if (lowerMessage.includes("transcribing chunk")) icon = "record_voice_over"; // Match simplified message
                  else if (lowerMessage.includes("already transcribed")) icon = "record_voice_over"; // Match simplified message
                  else if (lowerMessage.includes("transcribing with")) icon = "record_voice_over"; // Match simplified message
                  else if (lowerMessage.includes("calling api")) icon = "cloud_upload";
                  else if (lowerMessage.includes("aggregat")) icon = "merge_type";
                  else if (lowerMessage.includes("cleaning up")) icon = "cleaning_services";
                  else if (lowerMessage.includes("successful") || lowerMessage.includes("completed")) icon = "check_circle"; // Note: We override this later for final success
                  else if (lowerMessage.includes("error") || lowerMessage.includes("failed")) icon = "error";
                  else if (lowerMessage.includes("start")) icon = "play_arrow";

                  // Update the UI with the current message only if job not marked finished
                  if (!jobIsFinished) {
                     progressElement.innerHTML = `<i class="material-icons left">${icon}</i> ${message}`;
                  }
                  console.log("Progress:", message); // Log progress to console as well
              }
              // Update the index of the last message shown
              lastMessageIndex = progressLog.length - 1;
          }

          // Check if the job is finished based on status field
          const isFinished = jobData.status === 'finished';
          const isError = jobData.status === 'error';

          if (isFinished || isError) {
              jobIsFinished = true; // Set flag to stop UI updates
              clearInterval(interval); // Stop polling
              document.getElementById('transcribeBtn').disabled = false; // Re-enable button
              document.querySelector('.progress').style.display = 'none'; // Hide indeterminate bar
              // Keep progress container visible for final status/error

              if (isFinished) {
                  // Set specific success message
                  progressElement.innerHTML = `<i class="material-icons left green-text">check_circle</i> Transcription completed successfully!`;

                  M.toast({html: 'Transcription completed!', classes: 'green'});
                  var contextField = document.getElementById('contextPrompt');
                  if (contextField) {
                      contextField.value = ""; // Clear context prompt on success
                      validateContextPrompt(); // Reset validation state
                  }
                  // Fetch the full result data (which should be in jobData.result now)
                  if (jobData.result) {
                     addTranscriptionToHistory(jobData.result, true); // Prepend the new result
                  } else {
                     // If result is missing, maybe reload history?
                     console.warn("Job finished but result data missing in progress response.");
                     loadTranscriptions(); // Reload history as fallback
                  }
                  // Hide progress box after a short delay on success
                  setTimeout(() => { document.getElementById('progressContainer').style.display = 'none'; }, 4000);

              } else { // isError
                  const errorMessage = jobData.error_message || "An unknown error occurred.";
                  // Display the last progress message before the error if available
                  const lastProgress = progressLog.length > 0 ? progressLog[progressLog.length - 1] : "(No specific step logged)";
                  // Show error message clearly
                  progressElement.innerHTML = `<span class="grey-text">Last step: ${lastProgress}</span><br><i class="material-icons left red-text">error</i> Error: ${errorMessage}`;
                  M.toast({html: 'Transcription failed: ' + errorMessage, classes: 'red', displayLength: 6000}); // Show error longer
                  // Keep progress box visible on error
              }
          }
      })
      .catch(error => {
          // Handle network errors or issues fetching/parsing progress
          console.error('Error polling progress:', error);
          progressElement.innerHTML = `<i class="material-icons left red-text">error</i> Error polling status: ${error.message}`;
          jobIsFinished = true; // Stop further updates on polling error
          clearInterval(interval); // Stop polling on error
          document.getElementById('transcribeBtn').disabled = false; // Re-enable button
          document.querySelector('.progress').style.display = 'none'; // Hide indeterminate bar
          // Keep progress container potentially visible to show the polling error
      });
  }, 1500); // Poll slightly less frequently (e.g., every 1.5 seconds)
}