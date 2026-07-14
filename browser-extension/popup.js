const saveBtn = document.getElementById('saveBtn');
const statusEl = document.getElementById('status');
const settingsToggle = document.getElementById('settingsToggle');
const settingsEl = document.getElementById('settings');
const tokenInput = document.getElementById('tokenInput');
const saveTokenBtn = document.getElementById('saveTokenBtn');

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind || '';
}

async function getToken() {
  const { tacitToken } = await chrome.storage.local.get('tacitToken');
  return tacitToken || null;
}

async function refreshTokenState() {
  const token = await getToken();
  if (!token) {
    saveBtn.disabled = true;
    settingsEl.classList.add('open');
    setStatus('Add your quick-add token below to get started.');
  } else {
    saveBtn.disabled = false;
  }
}

settingsToggle.addEventListener('click', () => {
  settingsEl.classList.toggle('open');
});

saveTokenBtn.addEventListener('click', async () => {
  const value = tokenInput.value.trim();
  if (!value) return;
  await chrome.storage.local.set({ tacitToken: value });
  tokenInput.value = '';
  setStatus('Token saved.', 'success');
  await refreshTokenState();
});

saveBtn.addEventListener('click', async () => {
  const token = await getToken();
  if (!token) return;

  saveBtn.disabled = true;
  setStatus('Capturing page…');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) throw new Error('No active tab');

    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        html: document.documentElement.outerHTML,
        url: location.href,
        title: document.title,
      }),
    });

    setStatus('Saving to Tacit…');
    const response = await chrome.runtime.sendMessage({
      type: 'SAVE_TO_TACIT',
      token,
      ...result,
    });

    if (response && response.ok) {
      setStatus(response.duplicate ? 'Already on your canvas.' : 'Saved — processing…', 'success');
    } else {
      setStatus((response && response.error) || 'Save failed.', 'error');
    }
  } catch (e) {
    setStatus('Save failed: ' + e.message, 'error');
  } finally {
    saveBtn.disabled = false;
  }
});

refreshTokenState();
