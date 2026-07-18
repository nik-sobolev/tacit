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

// Chrome blocks content-script injection on its own internal pages and the
// Web Store itself — catch this upfront with a clear message instead of
// letting the cryptic "cannot be scripted" error surface as-is.
function isRestrictedUrl(url) {
  return /^(chrome|chrome-extension|edge|about|devtools):/.test(url) ||
    /^https:\/\/chrome\.google\.com\/webstore/.test(url) ||
    /^https:\/\/chromewebstore\.google\.com/.test(url);
}

// Saving Tacit's own app tab captures its whole rendered canvas (every card's
// title/summary/tags concatenated together) as "page content" instead of a
// real article — that garbled, multi-topic blob is dense enough to blow past
// the summarizer's output-token budget, truncate mid-JSON, and land the node
// on status="error" with a cryptic JSON-parse message. Block it here instead.
function isTacitAppUrl(url) {
  return /^https:\/\/(www\.)?trytacit\.app(\/|$)/.test(url);
}

saveBtn.addEventListener('click', async () => {
  const token = await getToken();
  if (!token) return;

  saveBtn.disabled = true;
  setStatus('Capturing page…');

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) throw new Error('No active tab');
    if (isTacitAppUrl(tab.url || '')) {
      setStatus("That's Tacit itself — switch to the page you want to save.", 'error');
      saveBtn.disabled = false;
      return;
    }
    if (isRestrictedUrl(tab.url || '')) {
      setStatus("Can't save this page — try it on a regular webpage.", 'error');
      saveBtn.disabled = false;
      return;
    }

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
