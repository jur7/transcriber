/* app/static/js/main.js */

document.addEventListener('DOMContentLoaded', function() {
    // Initialize Materialize selects
    var elems = document.querySelectorAll('select');
    M.FormSelect.init(elems);
    loadTranscriptions();
  
    // Mapping for friendly API and language names
    var apiNameMap = {
      'gpt4o': 'OpenAI GPT 4o Transcribe',
      'assemblyai': 'AssemblyAI',
      'whisper': 'OpenAI Whisper'
    };
    var languageNameMap = {
      'auto': 'Auto-detect',
      'en': 'English',
      'nl': 'Dutch',
      'fr': 'French',
      'es': 'Spanish'
    };
  
    document.getElementById('transcribeBtn').addEventListener('click', function() {
      var fileInput = document.getElementById('audioFile');
      var languageSelect = document.getElementById('languageSelect');
      var apiSelect = document.getElementById('apiSelect');
      var file = fileInput.files[0];
      var languageCode = languageSelect.value;
      var apiChoice = apiSelect.value;
      if (file) {
        // Set the status card fields
        document.getElementById('progressFile').textContent = file.name;
        document.getElementById('progressService').textContent = apiNameMap[apiChoice] || apiChoice;
        document.getElementById('progressLanguage').textContent = languageNameMap[languageCode] || languageCode;
        document.getElementById('progressActivity').innerHTML =
          '<i class="material-icons">hourglass_empty</i> Starting...';
  
        var formData = new FormData();
        formData.append('audio_file', file);
        formData.append('language_code', languageCode);
        formData.append('api_choice', apiChoice);
  
        // Show the progress bar and disable the transcribe button
        document.querySelector('.progress').style.display = 'block';
        document.getElementById('transcribeBtn').disabled = true;
        
        // Show the progress status card
        document.getElementById('progressContainer').style.display = 'block';
  
        fetch('/api/transcribe', {
          method: 'POST',
          body: formData
        })
        .then(response => {
          if (!response.ok) {
            throw new Error('Network response was not ok: ' + response.statusText);
          }
          return response.json();
        })
        .then(data => {
          if (data.error) {
            M.toast({html: 'Error: ' + data.error, classes: 'red'});
            document.querySelector('.progress').style.display = 'none';
            document.getElementById('transcribeBtn').disabled = false;
          } else {
            // Start polling progress using the returned job_id
            pollProgress(data.job_id);
          }
        })
        .catch(error => {
          console.error('Error:', error);
          document.querySelector('.progress').style.display = 'none';
          document.getElementById('transcribeBtn').disabled = false;
          M.toast({html: 'An error occurred during transcription: ' + error.message, classes: 'red'});
        });
      } else {
        M.toast({html: 'Please select an audio file.', classes: 'red'});
      }
    });
  
    document.getElementById('clearAllBtn').addEventListener('click', function() {
      if (confirm('Are you sure you want to clear all transcriptions?')) {
        fetch('/api/transcriptions/clear', { method: 'DELETE' })
        .then(response => response.json())
        .then(data => {
          M.toast({html: data.message});
          document.getElementById('transcriptionHistory').innerHTML = '';
          document.getElementById('clearAllBtn').style.display = 'none';
        })
        .catch(error => {
          console.error('Error:', error);
          M.toast({html: 'An error occurred while clearing transcriptions.'});
        });
      }
    });
  });
  
  function loadTranscriptions() {
    fetch('/api/transcriptions')
    .then(response => response.json())
    .then(transcriptions => {
      transcriptions.reverse();
      if (transcriptions.length > 0) {
        transcriptions.forEach(transcription => {
          addTranscriptionToHistory(transcription);
        });
        document.getElementById('clearAllBtn').style.display = 'block';
      }
    })
    .catch(error => {
      console.error('Error:', error);
      M.toast({html: 'An error occurred while loading transcriptions.'});
    });
  }
  
  function addTranscriptionToHistory(transcription) {
    console.log("Adding transcription to history:", transcription);
    const date = new Date(transcription.recording_date);
    const formattedDate = `${String(date.getDate()).padStart(2, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}-${date.getFullYear()}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;
    const apiNameMap = {
        'gpt4o': 'OpenAI GPT 4o Transcribe',
        'assemblyai': 'AssemblyAI',
        'whisper': 'OpenAI Whisper'
    };
    const apiName = apiNameMap[transcription.api_used] || transcription.api_used;
    var transcriptionItem = document.createElement('li');
    transcriptionItem.classList.add('collection-item');
    // Store the full transcript for downloads
    transcriptionItem.dataset.fullText = transcription.transcription_text;
    var contentDiv = document.createElement('div');
    contentDiv.innerHTML = `
        <b>${transcription.filename}</b> - ${formattedDate} - ${apiName}
        <div class="secondary-content">
            <button class="btn-flat waves-effect waves-light copy-btn" style="padding: 0 0.5rem;">
                <i class="material-icons">content_copy</i>
            </button>
            <button class="btn-flat waves-effect waves-light download-btn" style="padding: 0 0.5rem;">
                <i class="material-icons">download</i>
            </button>
            <button class="btn-flat waves-effect waves-light delete-btn" style="padding: 0 0.5rem;">
                <i class="material-icons">delete</i>
            </button>
        </div>
        <p class="transcription-text"></p>
    `;
    transcriptionItem.appendChild(contentDiv);
    transcriptionItem.querySelector('.copy-btn').addEventListener('click', function() {
        const fullText = transcriptionItem.dataset.fullText;
        console.log("Copying full text:", fullText);
        copyToClipboard(fullText);
    });
    transcriptionItem.querySelector('.download-btn').addEventListener('click', function() {
        const text = transcriptionItem.dataset.fullText;
        downloadTranscription(text, transcription.filename);
    });
    transcriptionItem.querySelector('.delete-btn').addEventListener('click', function() {
        deleteTranscription(transcription.id, transcriptionItem);
    });
    var transcriptionTextElement = contentDiv.querySelector('.transcription-text');
    const words = transcription.transcription_text.split(/\s+/).filter(word => word.length > 0);
    if (words.length > 140) {
        const truncatedText = words.slice(0, 140).join(' ') + '...';
        transcriptionTextElement.textContent = truncatedText;
        var readMoreLink = document.createElement('a');
        readMoreLink.href = '#!';
        readMoreLink.classList.add('read-more');
        readMoreLink.textContent = 'Read More';
        contentDiv.appendChild(readMoreLink);
        readMoreLink.addEventListener('click', function() {
            if (transcriptionTextElement.textContent === truncatedText) {
                transcriptionTextElement.textContent = transcription.transcription_text;
                readMoreLink.textContent = 'Read Less';
            } else {
                transcriptionTextElement.textContent = truncatedText;
                readMoreLink.textContent = 'Read More';
            }
        });
    } else {
        transcriptionTextElement.textContent = transcription.transcription_text;
    }
    var historyList = document.getElementById('transcriptionHistory');
    historyList.insertBefore(transcriptionItem, historyList.firstChild);
    document.getElementById('clearAllBtn').style.display = 'block';
  }
  
  function copyToClipboard(text) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        M.toast({html: 'Copied to clipboard!'});
    } catch (err) {
        M.toast({html: 'Failed to copy text'});
    }
    document.body.removeChild(textarea);
  }
  
  function downloadTranscription(text, filename) {
    var element = document.createElement('a');
    element.setAttribute('href', 'data:text/plain;charset=utf-8,' + encodeURIComponent(text));
    element.setAttribute('download', filename + '.txt');
    element.style.display = 'none';
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  }
  
  function deleteTranscription(transcriptionId, transcriptionItem) {
    fetch(`/api/transcriptions/${transcriptionId}`, { method: 'DELETE' })
    .then(response => response.json())
    .then(data => {
        M.toast({html: data.message});
        transcriptionItem.remove();
        if (document.getElementById('transcriptionHistory').children.length === 0) {
            document.getElementById('clearAllBtn').style.display = 'none';
        }
    })
    .catch(error => {
        console.error('Error:', error);
        M.toast({html: 'An error occurred while deleting the transcription.'});
    });
  }
  
  function pollProgress(jobId) {
    var interval = setInterval(function() {
        fetch('/api/progress/' + jobId)
        .then(response => response.json())
        .then(jobData => {
            if (jobData.progress && jobData.progress.length > 0) {
                var latestMessage = jobData.progress[jobData.progress.length - 1];
                var icon = "flash_on";
                if (latestMessage.includes("Splitting")) {
                    icon = "call_split";
                } else if (latestMessage.includes("Transcribing")) {
                    icon = "record_voice_over";
                } else if (latestMessage.includes("successful")) {
                    icon = "check_circle";
                } else if (latestMessage.toLowerCase().includes("error")) {
                    icon = "error";
                }
                document.getElementById('progressActivity').innerHTML =
                  '<i class="material-icons">' + icon + '</i> ' + latestMessage;
            }
            if (jobData.finished) {
                clearInterval(interval);
                document.getElementById('transcribeBtn').disabled = false;
                document.querySelector('.progress').style.display = 'none';
                if (jobData.result && !jobData.result.error) {
                    M.toast({html: 'Transcription completed!', classes: 'green'});
                    addTranscriptionToHistory(jobData.result);
                } else if (jobData.result && jobData.result.error) {
                    M.toast({html: 'Error: ' + jobData.result.error, classes: 'red'});
                }
            }
        })
        .catch(error => {
            console.error('Error polling progress:', error);
            clearInterval(interval);
        });
    }, 1000);
  }
  