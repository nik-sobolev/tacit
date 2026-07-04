// Bump this on every meaningful change so `activate` evicts any previously
// cached entries — including any that got corrupted/truncated mid-fetch.
// A fixed cache name here previously meant a bad cached app.js could persist
// forever, surviving hard refreshes and cache/cookie clears (Cache Storage is
// separate from both).
const CACHE = 'tacit-v2';

self.addEventListener('install', e => {
    self.skipWaiting();
});

self.addEventListener('activate', e => {
    e.waitUntil(caches.keys().then(keys =>
        Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ));
    self.clients.claim();
});

self.addEventListener('fetch', e => {
    // Never intercept API calls or the app's own JS/CSS — these must always
    // come straight from the network so a bad deploy can't get stuck cached
    // and a fixed one can't get masked by a stale/corrupted cache entry.
    const url = e.request.url;
    if (url.includes('/api/') || url.includes('/static/app.js') || url.includes('/static/styles.css')) {
        return;
    }
    // Everything else (e.g. the shell page): network-first, cached fallback
    // only for offline support.
    e.respondWith(
        fetch(e.request).then(resp => {
            const copy = resp.clone();
            caches.open(CACHE).then(c => c.put(e.request, copy));
            return resp;
        }).catch(() => caches.match(e.request))
    );
});
