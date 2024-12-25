document.addEventListener('DOMContentLoaded', function() {
    var elems = document.querySelectorAll('select');
    var instances = M.FormSelect.init(elems);

    loadTranscriptions();

    document.getElementById('transcribeBtn').addEventListener('click', function() {
        var fileInput = document.getElementById('audioFile');
        var languageSelect = document.getElementById('languageSelect');
        var file = fileInput.files[0];
        var languageCode = languageSelect.value;

        if (file) {
            var formData = new FormData();
            formData.append('audio_file', file);
            formData.append('language_code', languageCode);

            document.querySelector('.progress').style.display = 'block';

            fetch('/api/transcribe', {
                method: 'POST',
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                document.querySelector('.progress').style.display = 'none';
                if (data.error) {
                    M.toast({html: 'Error: ' + data.error});
                } else {
                    addTranscriptionToHistory(data);
                }
            })
            .catch(error => {
                console.error('Error:', error);
                document.querySelector('.progress').style.display = 'none';
                M.toast({html: 'An error occurred during transcription.'});
            });
        } else {
            M.toast({html: 'Please select an audio file.'});
        }
    });

    document.getElementById('clearAllBtn').addEventListener('click', function() {
        if (confirm('Are you sure you want to clear all transcriptions?')) {
            fetch('/api/transcriptions/clear', {
                method: 'DELETE'
            })
            .then(response => response.json())
            .then(data => {
                M.toast({html: data.message});
                document.getElementById('transcriptionHistory').innerHTML = '<li class="collection-header"><h4>Transcription History</h4></li>';
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
    // Format the date
    const date = new Date(transcription.recording_date);
    const formattedDate = `${String(date.getDate()).padStart(2, '0')}-${String(date.getMonth() + 1).padStart(2, '0')}-${date.getFullYear()}T${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`;

    // Map language codes to full names
    const languageMap = {
        'en': 'English',
        'nl': 'Dutch',
        'fr': 'French',
        'es': 'Spanish'
    };
    const languageName = languageMap[transcription.detected_language] || transcription.detected_language;

    var transcriptionItem = document.createElement('li');
    transcriptionItem.classList.add('collection-item');
    transcriptionItem.innerHTML = `
        <div>
            <b>${transcription.filename}</b> - ${formattedDate} - ${languageName}
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
            <p class="transcription-text">${transcription.transcription_text}</p>
        </div>
    `;

    // Add event listeners
    transcriptionItem.querySelector('.copy-btn').addEventListener('click', function() {
        const text = transcriptionItem.querySelector('.transcription-text').textContent;
        copyToClipboard(text);
    });

    transcriptionItem.querySelector('.download-btn').addEventListener('click', function() {
        const text = transcriptionItem.querySelector('.transcription-text').textContent;
        downloadTranscription(text, transcription.filename);
    });

    transcriptionItem.querySelector('.delete-btn').addEventListener('click', function() {
        deleteTranscription(transcription.id, transcriptionItem);
    });

    document.getElementById('transcriptionHistory').appendChild(transcriptionItem);
    document.getElementById('clearAllBtn').style.display = 'block';
}

function copyToClipboard(text) {
    // Create a temporary textarea element
    const textarea = document.createElement('textarea');
    textarea.value = text;
    document.body.appendChild(textarea);
    
    // Select and copy the text
    textarea.select();
    try {
        document.execCommand('copy');
        M.toast({html: 'Copied to clipboard!'});
    } catch (err) {
        M.toast({html: 'Failed to copy text'});
    }
    
    // Clean up
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
    fetch(`/api/transcriptions/${transcriptionId}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        M.toast({html: data.message});
        transcriptionItem.remove();
        if (document.getElementById('transcriptionHistory').children.length <= 1) {
            document.getElementById('clearAllBtn').style.display = 'none';
        }
    })
    .catch(error => {
        console.error('Error:', error);
        M.toast({html: 'An error occurred while deleting the transcription.'});
    });
}