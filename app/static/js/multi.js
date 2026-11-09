/* app/static/js/multi.js */

const activeJobCards = new Map();
const pendingUploads = [];
let uploadingFiles = false;

function updateTranscribeButton() {
  const multipleSelected = document.getElementById('audioFile').files.length > 1;
  const busy = uploadingFiles || pendingUploads.length > 0 ||
    Array.from(activeJobCards.values()).some(ui => !ui.finished);
  document.getElementById('transcribeBtnLabel').textContent = multipleSelected || busy ? 'Add to queue' : 'Transcribe';
  document.getElementById('transcribeBtnIcon').textContent = multipleSelected || busy ? 'playlist_add' : 'send';
}

function setJobActivity(ui, message, icon = 'info_outline', color = '') {
  ui.activityEl.innerHTML = `<i class="material-icons left ${color}">${icon}</i> ${escapeHtml(message)}`;
}

function removeProgressCard(ui) {
  ui.cardEl.remove();
  if (ui.jobId) activeJobCards.delete(ui.jobId);
  if (document.getElementById('activeJobsList').children.length === 0) {
    document.getElementById('activeJobsContainer').style.display = 'none';
  }
  updateTranscribeButton();
}

function makeErrorDismissible(ui) {
  ui.finished = true;
  if (ui.dismissButton) return;
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'btn dismiss-error-btn waves-effect waves-light';
  button.textContent = 'Dismiss';
  button.setAttribute('aria-label', 'Dismiss error for ' + ui.filename);
  button.addEventListener('click', () => removeProgressCard(ui));
  ui.cardEl.appendChild(button);
  ui.dismissButton = button;
}

function progressIcon(message) {
  const lower = message.toLowerCase();
  if (lower.includes('silence') || lower.includes('level')) return 'blur_linear';
  if (lower.includes('split')) return 'call_split';
  if (lower.includes('created') && lower.includes('chunk')) return 'layers';
  if (lower.includes('extracting') && (lower.includes('video') || lower.includes('audio'))) return 'local_movies';
  if (lower.includes('transcribing chunk') || lower.includes('already transcribed') || lower.includes('transcribing with')) return 'record_voice_over';
  if (lower.includes('calling api') || lower.includes('uploading')) return 'cloud_upload';
  if (lower.includes('aggregat')) return 'merge_type';
  if (lower.includes('cleaning up')) return 'cleaning_services';
  if (lower.includes('successful') || lower.includes('completed')) return 'check_circle';
  if (lower.includes('error') || lower.includes('failed')) return 'error';
  if (lower.includes('queued') || lower.includes('waiting')) return 'hourglass_empty';
  if (lower.includes('start')) return 'play_arrow';
  return 'info_outline';
}

function queueFilesForUpload(files, options) {
  for (const file of files) {
    const ui = addProgressCard(file.name, options.serviceName, options.languageDisplay);
    setJobActivity(ui, 'Waiting to upload...', 'hourglass_empty');
    pendingUploads.push({file, options: {...options}, ui});
  }
  uploadQueuedFiles();
}

async function uploadQueuedFiles() {
  if (uploadingFiles) return;
  uploadingFiles = true;
  updateTranscribeButton();
  try {
    while (pendingUploads.length) {
      const {file, options, ui} = pendingUploads.shift();
      try {
        setJobActivity(ui, 'Uploading...', 'cloud_upload');
        const body = new FormData();
        body.append('audio_file', file);
        body.append('api_choice', options.apiChoice);
        if (options.modelName) body.append('model_name', options.modelName);
        body.append('language_code', options.languageCode);
        body.append('context_prompt', options.contextPrompt);
        const response = await fetch('./api/transcribe', {method: 'POST', body});
        const data = await response.json();
        if (!response.ok || data.error || !data.job_id) {
          throw new Error(data.error || 'Upload failed. Please select this file again to retry.');
        }
        setJobActivity(ui, 'Uploaded. Queued for transcription.', 'hourglass_empty');
        pollProgressForCard(data.job_id, ui);
      } catch (error) {
        setJobActivity(ui, 'Upload failed: ' + error.message, 'error', 'red-text');
        makeErrorDismissible(ui);
      }
    }
  } finally {
    uploadingFiles = false;
    updateTranscribeButton();
  }
}

// Adds a per-job progress card to the Active Jobs list and returns refs to update it.
function addProgressCard(filename, serviceName, languageDisplay) {
  const list = document.getElementById('activeJobsList');
  document.getElementById('activeJobsContainer').style.display = 'block';
  const card = document.createElement('div');
  card.className = 'card-panel teal lighten-5 transcription-progress-card';
  card.style.marginTop = '10px';

  const activityId = 'act_' + Math.random().toString(36).slice(2, 9);
  card.innerHTML = `
    <div>
      <p><strong>File:</strong> <span>${escapeHtml(filename)}</span></p>
      <p><strong>Service:</strong> <span>${escapeHtml(serviceName)}</span></p>
      <p><strong>Language:</strong> <span>${escapeHtml(languageDisplay)}</span></p>
      <hr>
      <p><strong>Current Activity:</strong></p>
      <p id="${activityId}" class="transcription-activity"><i class="material-icons left">hourglass_empty</i> Starting...</p>
    </div>
  `;
  list.appendChild(card);
  const activityEl = card.querySelector('#' + activityId);
  activityEl.dataset.lastMessageIndex = -1;
  return { cardEl: card, activityEl, filename };
}

// Simple HTML escape for injected text nodes
function escapeHtml(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Polls progress for a specific job and updates a given UI card.
function pollProgressForCard(jobId, ui) {
  if (activeJobCards.has(jobId)) {
    if (activeJobCards.get(jobId) !== ui) ui.cardEl.remove();
    return;
  }
  activeJobCards.set(jobId, ui);
  ui.jobId = jobId;
  updateTranscribeButton();
  const progressElement = ui.activityEl;
  let lastMessageIndex = -1;
  let jobIsFinished = false;
  let pollInFlight = false;

  const interval = setInterval(() => {
    if (jobIsFinished || pollInFlight) return;
    pollInFlight = true;
    fetch(`./api/progress/${jobId}`)
      .then(r => {
        if (!r.ok) throw new Error(`Status check failed (${r.status}).`);
        return r.json();
      })
      .then(jobData => {
        if (jobIsFinished) return;
        const progressLog = Array.isArray(jobData.progress) ? jobData.progress : [];
        if (progressLog.length > 0) {
          for (let i = lastMessageIndex + 1; i < progressLog.length; i++) {
            const message = progressLog[i];
            if (!message) continue;
            if (!jobIsFinished) {
              setJobActivity(ui, message, progressIcon(message));
            }
          }
          lastMessageIndex = progressLog.length - 1;
        }

        const isFinished = jobData.status === 'finished';
        const isError = jobData.status === 'error';
        if (isFinished || isError) {
          jobIsFinished = true;
          ui.finished = true;
          updateTranscribeButton();
          clearInterval(interval);
          if (isFinished) {
            progressElement.innerHTML = `<i class="material-icons left green-text">check_circle</i> Transcription completed successfully!`;
            M.toast({html: escapeHtml(ui.filename) + ': transcription completed!', classes: 'green'});
            if (jobData.result) {
              addTranscriptionToHistory(jobData.result, true);
            } else {
              loadTranscriptions();
            }
            setTimeout(() => removeProgressCard(ui), 3000);
          } else {
            const errorMessage = jobData.error_message || 'An unknown error occurred.';
            const lastProgress = progressLog.length > 0 ? progressLog[progressLog.length - 1] : '(No specific step logged)';
            progressElement.innerHTML = `<span class="grey-text">Last step: ${escapeHtml(lastProgress)}</span><br><i class="material-icons left red-text">error</i> Error: ${escapeHtml(errorMessage)}`;
            makeErrorDismissible(ui);
          }
        }
      })
      .catch(error => {
        if (jobIsFinished) return;
        console.error('Error polling progress:', error);
        // Processing is server-owned. A dropped status request must not lose the job.
        setJobActivity(ui, 'Status unavailable; retrying. ' + error.message, 'error', 'red-text');
      })
      .finally(() => {
        pollInFlight = false;
      });
  }, 1500);
}

