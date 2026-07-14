const API_ORIGIN = 'https://www.trytacit.app';

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type !== 'SAVE_TO_TACIT') return false;

  (async () => {
    try {
      const res = await fetch(`${API_ORIGIN}/api/quickadd/html`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token: message.token,
          url: message.url,
          html: message.html,
          title: message.title,
        }),
      });

      if (res.status === 403) {
        sendResponse({ ok: false, error: 'Invalid token — check Settings.' });
        return;
      }
      if (res.status === 413) {
        sendResponse({ ok: false, error: 'Page too large to save.' });
        return;
      }
      if (!res.ok) {
        sendResponse({ ok: false, error: `Server error (${res.status})` });
        return;
      }

      const data = await res.json();
      sendResponse({ ok: true, duplicate: data.status === 'duplicate', nodeId: data.node_id });
    } catch (e) {
      sendResponse({ ok: false, error: e.message || 'Network error' });
    }
  })();

  return true; // keep the message channel open for the async sendResponse above
});
