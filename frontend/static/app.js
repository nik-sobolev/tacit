// Tacit — Second Brain Canvas
console.log('[Tacit] app.js v3 loaded (canvas)');

const API_BASE = '/api';

// ==================== FEATURE FLAGS ====================

let featureFlags = { notes_enabled: false, people_enabled: false };

async function loadFeatureFlags() {
    try {
        const res = await fetch(`${API_BASE}/features`);
        featureFlags = await res.json();
    } catch (e) {
        console.warn('[Tacit] failed to load feature flags:', e);
    }
    applyFeatureFlags();
}

function applyFeatureFlags() {
    document.getElementById('notesBtn').style.display =
        featureFlags.notes_enabled ? '' : 'none';
    document.getElementById('peopleBtn').style.display =
        featureFlags.people_enabled ? '' : 'none';
    document.getElementById('notesModeHeader').style.display =
        featureFlags.notes_enabled ? '' : 'none';
    document.getElementById('peopleModeHeader').style.display =
        featureFlags.people_enabled ? '' : 'none';
}

// ==================== SPLASH SCREEN ====================

function initSplash() {
    const splashShown = localStorage.getItem('tacit_splash_shown');
    if (!splashShown) {
        document.getElementById('splashScreen').style.display = 'flex';
        document.getElementById('splashButton').addEventListener('click', () => {
            document.getElementById('splashButton').style.display = 'none';
            document.getElementById('splashLoading').style.display = 'flex';
        });
    } else {
        document.getElementById('splashScreen').style.display = 'none';
    }
}

// Show splash on first load
initSplash();

// ==================== CLERK AUTH ====================

let clerkInstance = null;
let getAuthToken = async () => null; // overridden after Clerk loads

async function initAuth() {
    document.querySelector('.app-root').style.visibility = 'hidden';
    try {
        // Clerk loads async from clerk.trytacit.app — wait for window load then use global Clerk
        await new Promise(resolve => {
            if (document.readyState === 'complete') resolve();
            else window.addEventListener('load', resolve, { once: true });
        });
        // Poll briefly for Clerk global (set by the async script after load)
        let attempts = 0;
        while (!window.Clerk && attempts++ < 50) {
            await new Promise(r => setTimeout(r, 100));
        }
        if (!window.Clerk) throw new Error('Clerk JS failed to load from clerk.trytacit.app');
        await window.Clerk.load();
        const clerk = window.Clerk;
        clerkInstance = clerk;

        if (!clerk.user) {
            await clerk.redirectToSignIn({ redirectUrl: window.location.href });
            return false;
        }

        document.querySelector('.app-root').style.visibility = 'visible';
        localStorage.setItem('tacit_splash_shown', 'true');
        document.getElementById('splashScreen').style.display = 'none';
        getAuthToken = () => clerk.session.getToken();
        addUserMenuToHeader(clerk);
        return true;
    } catch (e) {
        // Show error — don't fail open in production
        document.body.style.visibility = 'visible';
        document.body.innerHTML = `<div style="color:#e6edf3;padding:60px;font-family:system-ui;background:#0f1419;min-height:100vh">
            <h2 style="color:#c05621">Authentication error</h2>
            <p style="color:#8b949e">${e.message}</p>
            <p style="color:#6e7681;font-size:12px">${e.stack || ''}</p>
        </div>`;
        return false;
    }
}

function addUserMenuToHeader(clerk) {
    const actions = document.querySelector('.header-actions');
    if (!actions) return;

    // Sync Clerk name to Tacit settings
    const firstName = clerk.user.firstName || '';
    const lastName = clerk.user.lastName || '';
    const fullName = [firstName, lastName].filter(Boolean).join(' ') || clerk.user.primaryEmailAddress?.emailAddress?.split('@')[0] || 'User';
    apiFetch('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ user_name: fullName })
    }).catch(() => {});

    // User avatar button
    const userBtn = document.createElement('button');
    userBtn.className = 'icon-btn';
    userBtn.title = fullName;
    userBtn.textContent = (firstName || fullName || '?')[0].toUpperCase();
    userBtn.style.fontWeight = '700';
    userBtn.addEventListener('click', () => clerk.openUserProfile());
    actions.insertBefore(userBtn, actions.firstChild);

    // Mobile profile icon — personalize with user initial
    const mobileIcon = document.getElementById('mobileProfileIcon');
    if (mobileIcon) {
        mobileIcon.textContent = (firstName || fullName || '?')[0].toUpperCase();
        mobileIcon.style.fontWeight = '700';
    }

    // Inject sign-out button into Clerk's UserProfile modal sidebar when it opens
    const observer = new MutationObserver(async () => {
        const navbar = document.querySelector('.cl-navbar');
        if (!navbar || navbar.querySelector('.tacit-signout-item')) return;

        // iOS quick-add shortcut button
        const mobileItem = document.createElement('button');
        mobileItem.className = 'tacit-mobile-item cl-navbarButton';
        mobileItem.setAttribute('type', 'button');
        mobileItem.style.cssText = 'display:flex;align-items:center;gap:8px;width:100%;padding:8px 12px;background:none;border:none;cursor:pointer;color:var(--text-secondary);font-size:14px;font-family:inherit;border-radius:6px;';
        mobileItem.innerHTML = '<span style="font-size:16px">📱</span> iOS Shortcut';
        mobileItem.addEventListener('click', async () => {
            try {
                const res = await apiFetch(`${API_BASE}/quickadd/token`);
                const data = await res.json();
                showIOSShortcutModal(data.token);
            } catch (e) {
                showToast('Could not load token. Try again.', 'error');
            }
        });
        navbar.appendChild(mobileItem);

        // Sign out button
        const item = document.createElement('button');
        item.className = 'tacit-signout-item cl-navbarButton';
        item.setAttribute('type', 'button');
        item.style.cssText = 'display:flex;align-items:center;gap:8px;width:100%;padding:8px 12px;background:none;border:none;cursor:pointer;color:#bf4d28;font-size:14px;font-family:inherit;border-radius:6px;margin-top:auto;';
        item.innerHTML = '<span style="font-size:16px">↩</span> Sign out';
        item.addEventListener('mouseenter', () => item.style.background = 'rgba(191,77,40,0.08)');
        item.addEventListener('mouseleave', () => item.style.background = 'none');
        item.addEventListener('click', async () => {
            if (confirm('Sign out?')) await clerk.signOut();
        });
        navbar.appendChild(item);
    });
    observer.observe(document.body, { childList: true, subtree: true });
}

// Authenticated fetch — wraps all API calls with Bearer token
async function apiFetch(url, opts = {}) {
    const token = await getAuthToken();
    return fetch(url, {
        ...opts,
        headers: {
            ...(opts.headers || {}),
            ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
            'Content-Type': opts.body && typeof opts.body === 'string' ? 'application/json' : (opts.headers?.['Content-Type'] || 'application/json'),
        }
    });
}

// ==================== CANVAS STATE ====================

let canvasX = 0, canvasY = 0, canvasScale = 1;
let isPanning = false, panStartX = 0, panStartY = 0;
let isDraggingCard = false;
let dragCard = null, dragOffsetX = 0, dragOffsetY = 0;
const nodeElements = {};  // nodeId → DOM card element
let graphData = { nodes: [], edges: [] };
let activeCategory = null;
let activeSearch = '';

// ==================== CHAT STATE ====================

let currentSessionId = null;

// ==================== CATEGORY COLORS ====================

function categoryColor(name) {
    if (!name) return '#6e7681';
    let hash = 0;
    for (let i = 0; i < name.length; i++) hash = name.charCodeAt(i) + ((hash << 5) - hash);
    const h = ((hash % 360) + 360) % 360;
    return `hsl(${h}, 55%, 55%)`;
}

function normalizeUrl(url) {
    try {
        const u = new URL(url);
        return (u.origin + u.pathname).toLowerCase().replace(/\/$/, '');
    } catch {
        return url.toLowerCase().replace(/\/$/, '');
    }
}

function flagDuplicates() {
    const urlMap = {};
    graphData.nodes.forEach(n => {
        if (!n.url) return;
        const k = normalizeUrl(n.url);
        if (!urlMap[k]) urlMap[k] = [];
        urlMap[k].push(n.id);
    });
    Object.values(nodeElements).forEach(el => el.classList.remove('card-duplicate'));
    Object.values(urlMap).filter(ids => ids.length > 1).forEach(ids => {
        ids.forEach(id => {
            if (nodeElements[id]) nodeElements[id].classList.add('card-duplicate');
        });
    });
}

// ==================== INIT ====================

document.addEventListener('DOMContentLoaded', async () => {
    const authed = await initAuth();
    if (!authed) return; // waiting for sign-in redirect

    await loadFeatureFlags();
    setupEmptyState();
    initCanvas();
    initIngestion();
    initChat();
    initChatResize();
    initUI();
    initCategorySidebar();
    await loadUsageMeter();
    await restoreOrStartSession();
    await loadGraph();
    await loadCategories();
    await loadInsights();
    if (featureFlags.notes_enabled) {
        loadNotesList();
    }

    // Handle PWA share target — Android shares URL via ?share_url=
    const sharedUrl = new URLSearchParams(window.location.search).get('share_url');
    if (sharedUrl && sharedUrl.startsWith('http')) {
        window.history.replaceState({}, '', '/');
        document.getElementById('urlInput').value = sharedUrl;
        await submitUrl();
    }
});

// ==================== CANVAS ENGINE ====================

function initCanvas() {
    const viewport = document.getElementById('canvasViewport');
    const surface = document.getElementById('canvasSurface');

    // Pan: drag on empty canvas (desktop only — mobile uses scroll)
    viewport.addEventListener('mousedown', (e) => {
        if (isMobile()) return;
        if (e.target === viewport || e.target === surface || e.target.id === 'edgesLayer') {
            isPanning = true;
            panStartX = e.clientX - canvasX;
            panStartY = e.clientY - canvasY;
            viewport.style.cursor = 'grabbing';
            e.preventDefault();
        }
    });

    document.addEventListener('mousemove', (e) => {
        if (isPanning) {
            canvasX = e.clientX - panStartX;
            canvasY = e.clientY - panStartY;
            applyTransform();
        }
        if (isDraggingCard && dragCard && !isMobile()) {
            const rect = document.getElementById('canvasViewport').getBoundingClientRect();
            const x = (e.clientX - rect.left - canvasX) / canvasScale - dragOffsetX;
            const y = (e.clientY - rect.top - canvasY) / canvasScale - dragOffsetY;
            dragCard.style.left = x + 'px';
            dragCard.style.top = y + 'px';
            drawEdges(graphData.edges);
        }
    });

    document.addEventListener('mouseup', (e) => {
        if (isPanning) {
            isPanning = false;
            viewport.style.cursor = 'grab';
        }
        if (isDraggingCard && dragCard) {
            const nodeId = dragCard.dataset.nodeId;
            const x = parseFloat(dragCard.style.left);
            const y = parseFloat(dragCard.style.top);
            if (!isNaN(x) && !isNaN(y)) saveNodePosition(nodeId, x, y);
            isDraggingCard = false;
            dragCard.style.cursor = 'grab';
            dragCard = null;
        }
    });

    // Zoom: scroll wheel
    viewport.addEventListener('wheel', (e) => {
        e.preventDefault();
        const rect = viewport.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        const newScale = Math.min(3, Math.max(0.15, canvasScale * delta));

        // Zoom toward mouse position
        canvasX = mouseX - (mouseX - canvasX) * (newScale / canvasScale);
        canvasY = mouseY - (mouseY - canvasY) * (newScale / canvasScale);
        canvasScale = newScale;
        applyTransform();
    }, { passive: false });
}

function applyTransform() {
    if (isMobile()) {
        document.getElementById('canvasSurface').style.transform = '';
        return;
    }
    document.getElementById('canvasSurface').style.transform =
        `translate(${canvasX}px, ${canvasY}px) scale(${canvasScale})`;
}

function resetView() {
    canvasX = 60;
    canvasY = 60;
    canvasScale = 1;
    applyTransform();
}

function scrollToNode(node) {
    if (isMobile()) {
        // Mobile: scroll the viewport to the card element
        const card = nodeElements[node.id];
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'center' });
        return;
    }
    const viewport = document.getElementById('canvasViewport');
    canvasX = -node.canvas_x * canvasScale + viewport.clientWidth / 2;
    canvasY = -node.canvas_y * canvasScale + viewport.clientHeight / 2;
    applyTransform();
}

// ==================== GRAPH LOADING ====================

async function loadGraph() {
    try {
        const res = await apiFetch(`${API_BASE}/graph`);
        const data = await res.json();
        graphData = data;
        renderGraph(data);
        updateEmptyState(data.nodes.length);
        flagDuplicates();
    } catch (e) {
        console.error('[Tacit] loadGraph error:', e);
    }
}

function renderGraph(data) {
    // Create cards for all nodes
    data.nodes.forEach(node => {
        if (!nodeElements[node.id]) {
            createCard(node);
        } else {
            updateCardContent(node.id, node);
        }
        // Resume polling for any nodes still processing
        if (node.status === 'processing' || node.status === 'pending') {
            pollNodeStatus(node.id);
        }
    });

    // Draw edges
    drawEdges(data.edges);
}

// ==================== CARDS ====================

function createCard(node) {
    const card = document.createElement('div');
    card.className = `canvas-card card-${node.type}`;
    card.dataset.nodeId = node.id;
    const cat = (node.metadata && node.metadata.category) || '';
    card.dataset.category = cat;
    if (cat) card.style.borderLeftColor = categoryColor(cat);
    if (isMobile()) {
        card.style.left = '';
        card.style.top = '';
    } else {
        card.style.left = (node.canvas_x || 100) + 'px';
        card.style.top = (node.canvas_y || 100) + 'px';
    }

    card.innerHTML = buildCardHTML(node);

    // Drag events
    card.addEventListener('mousedown', (e) => {
        if (e.target.classList.contains('card-delete-btn')) return;
        isDraggingCard = true;
        dragCard = card;
        const rect = card.getBoundingClientRect();
        const vpRect = document.getElementById('canvasViewport').getBoundingClientRect();
        dragOffsetX = (e.clientX - rect.left) / canvasScale;
        dragOffsetY = (e.clientY - rect.top) / canvasScale;
        card.style.cursor = 'grabbing';
        e.stopPropagation();
    });

    // Click to open detail (only if not dragged)
    card.addEventListener('click', (e) => {
        if (e.target.classList.contains('card-delete-btn')) {
            deleteNode(node.id, card);
            return;
        }
        if (!isDraggingCard) {
            openDetail(node.id);
        }
    });

    document.getElementById('canvasSurface').appendChild(card);
    nodeElements[node.id] = card;
}

function buildCardHTML(node) {
    const isProcessing = node.status === 'processing' || node.status === 'pending';
    const isError = node.status === 'error';
    const typeIcon = getTypeIcon(node.type);
    const typeLabel = node.type.charAt(0).toUpperCase() + node.type.slice(1);

    const category = (node.metadata && node.metadata.category) ? node.metadata.category : '';

    let thumbHTML = '';
    if (node.thumbnail_url) {
        thumbHTML = `<img class="card-thumb" src="${escapeHtml(node.thumbnail_url)}" alt="" onerror="this.style.display='none'" />`;
    }

    let bodyHTML = '';
    if (isProcessing) {
        bodyHTML = `<div class="card-processing-indicator"><div class="spinner-small"></div><span>Processing…</span></div>`;
    } else if (isError) {
        bodyHTML = `<div class="card-error">⚠ Processing failed</div>`;
    } else {
        const summary = node.summary ? `<p class="card-summary">${escapeHtml(node.summary)}</p>` : '';
        const tags = (node.tags || []).slice(0, 4).map(t =>
            `<span class="card-tag">${escapeHtml(t)}</span>`
        ).join('');
        bodyHTML = summary + (tags ? `<div class="card-tags">${tags}</div>` : '');
    }

    return `
        <div class="card-header">
            <span class="card-type-icon">${typeIcon}</span>
            <span class="card-type-label">${typeLabel}</span>
            ${category ? `<span class="card-category" style="background:${categoryColor(category)}22;color:${categoryColor(category)}">${escapeHtml(category)}</span>` : ''}
            <button class="card-delete-btn" title="Delete">✕</button>
        </div>
        ${thumbHTML}
        <div class="card-body">
            <h3 class="card-title">${escapeHtml(node.title || 'Processing…')}</h3>
            ${bodyHTML}
        </div>
        ${node.url ? `<div class="card-footer"><a class="card-url" href="${escapeHtml(node.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${escapeHtml(getDomain(node.url))}</a></div>` : ''}
    `;
}

function updateCardContent(nodeId, node) {
    const card = nodeElements[nodeId];
    if (!card) return;
    card.className = `canvas-card card-${node.type}`;
    const cat = (node.metadata && node.metadata.category) || '';
    card.dataset.category = cat;
    if (cat) card.style.borderLeftColor = categoryColor(cat);
    card.innerHTML = buildCardHTML(node);
}

// ==================== EDGES (SVG) ====================

function drawEdges(edges) {
    const svg = document.getElementById('edgesLayer');
    svg.innerHTML = '';

    edges.forEach(edge => {
        const sourceEl = nodeElements[edge.source_id];
        const targetEl = nodeElements[edge.target_id];
        if (!sourceEl || !targetEl) return;

        const sc = getCardCenter(sourceEl);
        const tc = getCardCenter(targetEl);

        const bothDimmed = sourceEl.classList.contains('dimmed') && targetEl.classList.contains('dimmed');
        const eitherDimmed = sourceEl.classList.contains('dimmed') || targetEl.classList.contains('dimmed');
        const baseOpacity = bothDimmed ? 0.05 : (eitherDimmed ? 0.15 : 0.3 + (edge.strength || 0.5) * 0.4);
        const opacity = baseOpacity;
        const strokeWidth = 1 + (edge.strength || 0.5) * 1.5;

        const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', sc.x);
        line.setAttribute('y1', sc.y);
        line.setAttribute('x2', tc.x);
        line.setAttribute('y2', tc.y);
        line.setAttribute('stroke', '#c05621');
        line.setAttribute('stroke-width', strokeWidth);
        line.setAttribute('stroke-opacity', opacity);
        line.setAttribute('stroke-dasharray', edge.auto_generated ? '6 4' : 'none');

        svg.appendChild(line);
    });

    // Size SVG to canvas surface
    const surface = document.getElementById('canvasSurface');
    svg.setAttribute('width', surface.scrollWidth + 500);
    svg.setAttribute('height', surface.scrollHeight + 500);
}

function getCardCenter(cardEl) {
    const left = parseFloat(cardEl.style.left);
    const top = parseFloat(cardEl.style.top);
    if (isNaN(left) || isNaN(top)) {
        // Mobile: card has no inline position, use DOM offset
        const surface = document.getElementById('canvasSurface');
        const surfaceRect = surface.getBoundingClientRect();
        const cardRect = cardEl.getBoundingClientRect();
        return {
            x: (cardRect.left - surfaceRect.left) + cardEl.offsetWidth / 2,
            y: (cardRect.top - surfaceRect.top) + cardEl.offsetHeight / 2,
        };
    }
    return {
        x: left + cardEl.offsetWidth / 2,
        y: top + cardEl.offsetHeight / 2,
    };
}

async function saveNodePosition(nodeId, x, y) {
    try {
        await apiFetch(`${API_BASE}/nodes/${nodeId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ canvas_x: x, canvas_y: y }),
        });
    } catch (e) {
        console.error('[Tacit] saveNodePosition error:', e);
    }
}

// ==================== INGESTION ====================

function initIngestion() {
    const input = document.getElementById('urlInput');
    const btn = document.getElementById('ingestBtn');

    btn.addEventListener('click', () => submitUrl());
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') submitUrl();
    });

    // Allow dropping URLs and images
    document.addEventListener('dragover', (e) => e.preventDefault());
    document.addEventListener('drop', (e) => {
        e.preventDefault();
        // Check for image files first
        const imageFiles = Array.from(e.dataTransfer.files).filter(f => f.type.startsWith('image/'));
        if (imageFiles.length) {
            imageFiles.forEach(uploadImageFile);
            return;
        }
        // Existing URL handling
        const text = e.dataTransfer.getData('text/plain') || e.dataTransfer.getData('text/uri-list');
        if (text && text.startsWith('http')) {
            document.getElementById('urlInput').value = text;
            submitUrl();
        }
    });
}

async function submitUrl() {
    const input = document.getElementById('urlInput');
    const url = input.value.trim();
    if (!url || !url.startsWith('http')) {
        showToast('Please enter a valid URL starting with http', 'error');
        return;
    }

    input.value = '';
    input.disabled = true;
    document.getElementById('ingestBtn').disabled = true;

    // Layer 1: instant duplicate check against in-memory graph
    const normalizedInput = normalizeUrl(url);
    const existingNode = graphData.nodes.find(n => n.url && normalizeUrl(n.url) === normalizedInput);
    if (existingNode) {
        showToast('Already on your canvas', 'error');
        scrollToNode(existingNode);
        const existingCard = nodeElements[existingNode.id];
        if (existingCard) {
            existingCard.classList.add('card-highlight-pulse');
            setTimeout(() => existingCard.classList.remove('card-highlight-pulse'), 1500);
        }
        input.value = '';
        input.disabled = false;
        document.getElementById('ingestBtn').disabled = false;
        return;
    }

    // Place new card near center of current viewport (desktop) or stacked (mobile)
    const viewport = document.getElementById('canvasViewport');
    const vw = viewport.clientWidth, vh = viewport.clientHeight;
    const cx = isMobile() ? 100 + graphData.nodes.length * 10 : (-canvasX + vw / 2) / canvasScale + (Math.random() * 200 - 100);
    const cy = isMobile() ? 100 + graphData.nodes.length * 10 : (-canvasY + vh / 2) / canvasScale + (Math.random() * 200 - 100);

    try {
        const res = await apiFetch(`${API_BASE}/ingest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, canvas_x: cx, canvas_y: cy }),
        });

        if (res.status === 402) {
            showToast('Token limit reached. Upgrade to Pro for $9/mo.', 'error');
            show402Modal();
            await loadUsageMeter();
            throw new Error('Token limit reached');
        }
        if (!res.ok) throw new Error(`Server error: ${res.status}`);
        const data = await res.json();

        // Layer 2: backend safety net — duplicate returned from DB
        if (data.duplicate) {
            showToast('Already on your canvas', 'error');
            const dupNode = graphData.nodes.find(n => n.id === data.node_id);
            if (dupNode) {
                scrollToNode(dupNode);
                const dupCard = nodeElements[data.node_id];
                if (dupCard) {
                    dupCard.classList.add('card-highlight-pulse');
                    setTimeout(() => dupCard.classList.remove('card-highlight-pulse'), 1500);
                }
            }
            return;
        }

        // Create placeholder card
        const placeholderNode = {
            id: data.node_id,
            type: data.type || 'webpage',
            title: data.title || url,
            summary: null,
            thumbnail_url: null,
            url,
            canvas_x: cx,
            canvas_y: cy,
            status: 'processing',
            tags: [],
            metadata: {},
        };
        createCard(placeholderNode);
        graphData.nodes.push(placeholderNode);
        updateEmptyState(graphData.nodes.length);

        // Poll for completion
        pollNodeStatus(data.node_id);

        showToast('Added to canvas — processing…', 'success');
    } catch (e) {
        showToast('Failed to ingest URL: ' + e.message, 'error');
        console.error('[Tacit] ingest error:', e);
    } finally {
        input.disabled = false;
        document.getElementById('ingestBtn').disabled = false;
        input.focus();
    }
}

function showBulkAddModal() {
    const modal = document.createElement('div');
    modal.className = 'bulk-add-modal';
    modal.innerHTML = `
        <div class="bulk-add-overlay"></div>
        <div class="bulk-add-panel">
            <div class="bulk-add-header">
                <h3>Add Multiple URLs</h3>
                <button class="bulk-add-close">✕</button>
            </div>
            <div class="bulk-add-body">
                <p style="font-size:13px;color:var(--text-secondary);margin-bottom:12px">Paste URLs (one per line)</p>
                <textarea id="bulkUrlInput" placeholder="https://example.com&#10;https://youtube.com/watch?v=...&#10;https://..."></textarea>
            </div>
            <div class="bulk-add-footer">
                <button id="bulkSubmitBtn" class="bulk-submit-btn">Add to Canvas</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    const input = modal.querySelector('#bulkUrlInput');
    setTimeout(() => input?.focus(), 100);

    modal.querySelector('.bulk-add-close').addEventListener('click', () => modal.remove());
    modal.querySelector('.bulk-add-overlay').addEventListener('click', () => modal.remove());

    modal.querySelector('#bulkSubmitBtn').addEventListener('click', async () => {
        const urls = input.value
            .split('\n')
            .map(u => u.trim())
            .filter(u => u.startsWith('http'));

        if (!urls.length) {
            showToast('No valid URLs found', 'error');
            return;
        }

        modal.querySelector('#bulkSubmitBtn').disabled = true;
        let added = 0, failed = 0;

        for (const url of urls) {
            try {
                document.getElementById('urlInput').value = url;
                await submitUrl();
                added++;
                await new Promise(r => setTimeout(r, 300)); // small delay between requests
            } catch (e) {
                failed++;
            }
        }

        modal.remove();
        showToast(`Added ${added} URLs${failed ? ` (${failed} failed)` : ''}`, added > 0 ? 'success' : 'error');
    });
}

function showIOSShortcutModal(token) {
    const modal = document.createElement('div');
    modal.className = 'ios-shortcut-modal';
    const addUrl = `https://www.trytacit.app/api/quickadd?token=${token}&url=`;
    modal.innerHTML = `
        <div class="ios-shortcut-overlay"></div>
        <div class="ios-shortcut-panel">
            <div class="ios-shortcut-header">
                <h3>iOS Quick-Add Shortcut</h3>
                <button class="ios-shortcut-close">✕</button>
            </div>
            <div class="ios-shortcut-body">
                <p style="font-size:13px;margin-bottom:16px"><strong>Setup Instructions</strong></p>
                <ol style="font-size:13px;margin-bottom:16px;line-height:1.6">
                    <li>Open iOS Shortcuts app</li>
                    <li>Tap "+" to create new shortcut</li>
                    <li>Add "Open URL" action</li>
                    <li>Paste the URL below</li>
                    <li>Add "Ask for Text" for user to paste URL</li>
                    <li>Save as "Add to Tacit"</li>
                </ol>
                <label style="font-size:12px;display:block;margin-bottom:8px">API URL:</label>
                <div style="display:flex;gap:8px">
                    <input type="text" id="iosUrlInput" readonly value="${addUrl}" style="flex:1;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;font-size:12px;font-family:monospace" />
                    <button id="copyUrlBtn" style="padding:8px 12px;background:var(--primary);color:white;border:none;border-radius:4px;cursor:pointer;white-space:nowrap">Copy</button>
                </div>
                <p style="font-size:12px;color:var(--text-secondary);margin-top:12px">Your API token (keep secret):<br><code style="background:var(--bg);padding:4px;border-radius:2px;word-break:break-all">${token}</code></p>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    modal.querySelector('.ios-shortcut-close').addEventListener('click', () => modal.remove());
    modal.querySelector('.ios-shortcut-overlay').addEventListener('click', () => modal.remove());

    modal.querySelector('#copyUrlBtn').addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(addUrl);
            showToast('URL copied to clipboard!', 'success');
        } catch (e) {
            showToast('Could not copy to clipboard', 'error');
        }
    });
}

async function uploadImageFile(file) {
    try {
        const formData = new FormData();
        formData.append('file', file);
        const token = await window.Clerk.session.getToken();
        const resp = await fetch('/api/images/upload', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: formData,
        });
        if (!resp.ok) {
            const err = await resp.json();
            showToast('Failed to upload image: ' + (err.detail || 'Unknown error'), 'error');
            return;
        }
        const data = await resp.json();
        const canvasNode = {
            id: data.node_id, type: data.type, title: data.title,
            thumbnail_url: data.thumbnail_url, status: data.status,
            canvas_x: data.canvas_x, canvas_y: data.canvas_y,
            tags: [], metadata: {}, summary: null,
        };
        createCard(canvasNode);
        graphData.nodes.push(canvasNode);
        updateEmptyState(graphData.nodes.length);
        showToast('Image added to canvas', 'success');
    } catch (e) {
        showToast('Failed to upload image: ' + e.message, 'error');
        console.error('[Tacit] image upload error:', e);
    }
}

function pollNodeStatus(nodeId, attempts = 0) {
    if (attempts > 60) return; // give up after ~2 minutes

    setTimeout(async () => {
        try {
            const res = await apiFetch(`${API_BASE}/ingest/${nodeId}/status`);
            const data = await res.json();

            if (data.status === 'done') {
                // Reload full graph to get edges too
                const graphRes = await apiFetch(`${API_BASE}/graph`);
                const graph = await graphRes.json();
                graphData = graph;

                // Update this card
                const node = graph.nodes.find(n => n.id === nodeId);
                if (node) updateCardContent(nodeId, node);

                drawEdges(graph.edges);
                loadCategories(); // refresh sidebar
                flagDuplicates();
                showToast('✓ Content processed and linked', 'success');

                // Proactive chat message about the new node
                if (node) {
                    const cat = (node.metadata && node.metadata.category) || '';
                    const purpose = (node.metadata && node.metadata.purpose) || '';
                    const connectedEdges = graph.edges.filter(e => e.source_id === nodeId || e.target_id === nodeId);
                    let msg = `**Processed:** ${escapeHtml(node.title || 'New node')}`;
                    if (cat) msg += `\n**Category:** ${cat}`;
                    if (purpose) msg += `\n**Purpose:** ${purpose}`;
                    if (connectedEdges.length > 0) {
                        const connTitles = connectedEdges.slice(0, 3).map(e => {
                            const otherId = e.source_id === nodeId ? e.target_id : e.source_id;
                            const other = graph.nodes.find(n => n.id === otherId);
                            return other ? other.title : 'Unknown';
                        });
                        msg += `\n**Connected to:** ${connTitles.join(', ')}`;
                    }
                    addMessage('assistant', msg);
                }
            } else if (data.status === 'error') {
                updateCardContent(nodeId, { ...graphData.nodes.find(n => n.id === nodeId), status: 'error' });
                showToast('Processing failed for this URL', 'error');
            } else {
                pollNodeStatus(nodeId, attempts + 1);
            }
        } catch (e) {
            pollNodeStatus(nodeId, attempts + 1);
        }
    }, 2000);
}

// ==================== NODE DETAIL ====================

async function openDetail(nodeId) {
    document.getElementById('categorySidebar').classList.remove('open');
    const panel = document.getElementById('nodeDetailPanel');
    const content = document.getElementById('detailContent');
    const typeBadge = document.getElementById('detailType');

    content.innerHTML = '<div class="detail-loading"><div class="spinner-small"></div> Loading…</div>';
    panel.classList.add('open');

    try {
        const res = await apiFetch(`${API_BASE}/nodes/${nodeId}`);
        const node = await res.json();

        typeBadge.textContent = node.type;
        typeBadge.className = `detail-type-badge type-${node.type}`;

        let relatedHTML = '';
        try {
            const relRes = await apiFetch(`${API_BASE}/nodes/${nodeId}/related`);
            const relData = await relRes.json();
            if (relData.nodes && relData.nodes.length > 0) {
                relatedHTML = `
                    <div class="detail-section">
                        <h4>Connected Nodes</h4>
                        <div class="related-nodes">
                            ${relData.nodes.map(n => `
                                <div class="related-node" onclick="openDetail('${n.id}')">
                                    <span class="related-icon">${getTypeIcon(n.type)}</span>
                                    <span class="related-title">${escapeHtml(n.title || 'Untitled')}</span>
                                </div>
                            `).join('')}
                        </div>
                    </div>`;
            }
        } catch (e) {}

        const tagsHTML = (node.tags || []).map(t => `<span class="card-tag">${escapeHtml(t)}</span>`).join('');

        content.innerHTML = `
            ${node.thumbnail_url ? `<img class="${node.type === 'image' ? 'detail-thumb-image' : 'detail-thumb'}" src="${escapeHtml(node.thumbnail_url)}" alt="" onerror="this.style.display='none'" />` : ''}
            <div class="detail-section">
                <h2 class="detail-title">${escapeHtml(node.title || 'Untitled')}</h2>
                ${node.url ? `<a class="detail-url" href="${escapeHtml(node.url)}" target="_blank" rel="noopener">${escapeHtml(node.url)}</a>` : ''}
            </div>
            ${node.summary ? `<div class="detail-section"><h4>Summary</h4><p class="detail-summary">${escapeHtml(node.summary)}</p></div>` : ''}
            ${tagsHTML ? `<div class="detail-section"><div class="card-tags">${tagsHTML}</div></div>` : ''}
            ${relatedHTML}
            ${node.content ? `
                <div class="detail-section">
                    <h4>Full Content</h4>
                    <div class="detail-transcript">${escapeHtml(node.content)}</div>
                </div>` : ''}
        `;
    } catch (e) {
        content.innerHTML = '<p class="detail-error">Failed to load node details.</p>';
    }
}

async function deleteNode(nodeId, cardEl) {
    if (!confirm('Delete this node and its connections?')) return;
    try {
        await apiFetch(`${API_BASE}/nodes/${nodeId}`, { method: 'DELETE' });
        cardEl.remove();
        delete nodeElements[nodeId];
        graphData.nodes = graphData.nodes.filter(n => n.id !== nodeId);
        graphData.edges = graphData.edges.filter(e => e.source_id !== nodeId && e.target_id !== nodeId);
        drawEdges(graphData.edges);
        updateEmptyState(graphData.nodes.length);
        // Close detail panel if showing this node
        document.getElementById('nodeDetailPanel').classList.remove('open');
    } catch (e) {
        showToast('Failed to delete node', 'error');
    }
}

// ==================== MOBILE ====================

function isMobile() {
    return window.innerWidth <= 768;
}

function mobileTab(tab) {
    if (!isMobile()) return;
    document.querySelectorAll('.mobile-tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`[data-tab="${tab}"]`)?.classList.add('active');

    const chatPanel = document.getElementById('chatPanel');
    if (tab === 'chat') {
        chatPanel.classList.add('mobile-visible');
        // Focus input
        setTimeout(() => document.getElementById('messageInput')?.focus(), 100);
    } else {
        chatPanel.classList.remove('mobile-visible');
    }
}

function mobileOpenProfile() {
    const modal = document.createElement('div');
    modal.className = 'mobile-add-modal';
    modal.innerHTML = `
        <div class="mobile-add-sheet">
            <p style="font-size:14px;color:var(--text-secondary);margin-bottom:16px">Account</p>
            <button class="mobile-add-option" id="mobileAccountBtn">👤 My Profile</button>
            <button class="mobile-add-option" id="mobileSignOutBtn" style="color:#bf4d28">↩ Sign Out</button>
        </div>
    `;
    document.body.appendChild(modal);

    modal.querySelector('#mobileAccountBtn').addEventListener('click', () => {
        modal.remove();
        if (window.Clerk) {
            window.Clerk.openUserProfile();
        }
    });

    modal.querySelector('#mobileSignOutBtn').addEventListener('click', async () => {
        modal.remove();
        if (confirm('Sign out?')) {
            if (window.Clerk) {
                await window.Clerk.signOut();
            }
        }
    });

    modal.addEventListener('click', e => {
        if (e.target === modal) modal.remove();
    });
}

function mobileShowAdd() {
    if (!isMobile()) return;
    const modal = document.createElement('div');
    modal.className = 'mobile-add-modal';
    let mode = 'choice'; // 'choice' | 'url' | 'note'

    const noteButton = featureFlags.notes_enabled ? '<button class="mobile-add-option" data-mode="note">📝 Write Note</button>' : '';
    const choiceUI = `
        <div class="mobile-add-sheet">
            <p style="font-size:14px;color:var(--text-secondary);margin-bottom:16px">What would you like to add?</p>
            <button class="mobile-add-option" data-mode="url">📎 Add URL</button>
            ${noteButton}
        </div>
    `;

    const urlUI = `
        <div class="mobile-add-sheet">
            <p style="font-size:14px;color:var(--text-secondary);margin-bottom:12px">Add URL to canvas</p>
            <input id="mobileUrlInput" type="url" placeholder="https://..." inputmode="url" autofocus>
            <button id="mobileAddBtn">Add to Canvas</button>
        </div>
    `;

    const noteUI = `
        <div class="mobile-add-sheet">
            <p style="font-size:14px;color:var(--text-secondary);margin-bottom:12px">Write a note</p>
            <input id="mobileNoteTitle" type="text" placeholder="Title (optional)" style="margin-bottom:8px">
            <textarea id="mobileNoteContent" placeholder="Your note..." style="min-height:120px;margin-bottom:12px"></textarea>
            <button id="mobileAddNoteBtn">Save Note</button>
        </div>
    `;

    modal.innerHTML = choiceUI;
    document.body.appendChild(modal);

    const updateUI = (newMode) => {
        mode = newMode;
        const sheet = modal.querySelector('.mobile-add-sheet');
        if (newMode === 'url') {
            sheet.innerHTML = urlUI.replace(/id="mobile/g, 'id="new-mobile');
            setTimeout(() => modal.querySelector('#new-mobileUrlInput')?.focus(), 100);
            modal.querySelector('#new-mobileAddBtn')?.addEventListener('click', handleAddUrl);
        } else if (newMode === 'note') {
            sheet.innerHTML = noteUI.replace(/id="mobile/g, 'id="new-mobile');
            setTimeout(() => modal.querySelector('#new-mobileNoteContent')?.focus(), 100);
            modal.querySelector('#new-mobileAddNoteBtn')?.addEventListener('click', handleAddNote);
        }
    };

    const handleAddUrl = async () => {
        const url = modal.querySelector('#new-mobileUrlInput')?.value.trim();
        if (!url) return;
        document.getElementById('urlInput').value = url;
        modal.remove();
        await submitUrl();
        mobileTab('canvas');
    };

    const handleAddNote = async () => {
        const title = modal.querySelector('#new-mobileNoteTitle')?.value.trim() || '';
        const content = modal.querySelector('#new-mobileNoteContent')?.value.trim() || '';
        if (!content) return showToast('Note cannot be empty', 'error');

        try {
            const token = await window.Clerk.session.getToken();
            const resp = await fetch('/api/ingest/note', {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${token}`,
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ title, content, canvas_x: 300, canvas_y: 300 }),
            });
            if (!resp.ok) {
                const err = await resp.json();
                showToast('Failed to save note: ' + (err.detail || 'Unknown error'), 'error');
                return;
            }
            const node = await resp.json();
            createCard(node);
            graphData.nodes.push(node);
            updateEmptyState(graphData.nodes.length);
            modal.remove();
            showToast('Note saved', 'success');
            loadNotesList();
            mobileTab('canvas');
        } catch (e) {
            showToast('Failed to save note: ' + e.message, 'error');
            console.error('[Tacit] note creation error:', e);
        }
    };

    modal.querySelectorAll('.mobile-add-option').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const newMode = e.target.closest('button').dataset.mode;
            updateUI(newMode);
        });
    });

    modal.addEventListener('click', (e) => {
        if (e.target === modal) modal.remove();
    });
}

// ==================== BILLING ====================

async function loadUsageMeter() {
    try {
        const res = await apiFetch(`${API_BASE}/billing/status`);
        if (!res.ok) return;
        const data = await res.json();
        const meter = document.getElementById('usageMeter');
        const text = document.getElementById('usageText');

        const pct = data.pct_used || 0;
        const limit = data.tokens_limit || 100000;
        const used = data.tokens_used || 0;
        const formatted = `${Math.round(used / 1000)}k / ${Math.round(limit / 1000)}k`;
        text.textContent = formatted;
        meter.style.display = 'block';

        // Color based on usage
        meter.classList.remove('near-limit', 'at-limit');
        if (pct >= 95) {
            meter.classList.add('at-limit');
        } else if (pct >= 80) {
            meter.classList.add('near-limit');
        }
    } catch (e) {
        console.error('[Tacit] loadUsageMeter error:', e);
    }
}

function show402Modal() {
    const modal = document.createElement('div');
    modal.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center;
        z-index: 10000;
    `;
    modal.innerHTML = `
        <div style="
            background: var(--surface); border: 1px solid var(--border);
            border-radius: 12px; padding: 24px; max-width: 400px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        ">
            <h2 style="margin: 0 0 12px 0; color: var(--text); font-size: 18px;">Token Limit Reached</h2>
            <p style="margin: 0 0 20px 0; color: var(--text-secondary); font-size: 14px;">
                You've used your monthly token allowance. Upgrade to Pro for $9/mo to continue.
            </p>
            <div style="display: flex; gap: 12px;">
                <button id="upgradeBtn" style="
                    flex: 1; padding: 10px; background: #bf4d28; color: white;
                    border: none; border-radius: 6px; cursor: pointer; font-weight: 500;
                ">Go Pro</button>
                <button id="closeModal" style="
                    flex: 1; padding: 10px; background: var(--surface-hover); color: var(--text);
                    border: 1px solid var(--border); border-radius: 6px; cursor: pointer;
                ">Cancel</button>
            </div>
        </div>
    `;
    document.body.appendChild(modal);

    document.getElementById('upgradeBtn').addEventListener('click', async () => {
        try {
            const res = await apiFetch(`${API_BASE}/billing/checkout`, { method: 'POST' });
            if (res.ok) {
                const data = await res.json();
                window.location = data.url;
            }
        } catch (e) {
            showToast('Failed to open checkout', 'error');
        }
    });
    document.getElementById('closeModal').addEventListener('click', () => modal.remove());
}

// ==================== CHAT ====================

function initChat() {
    document.getElementById('sendBtn').addEventListener('click', sendMessage);
    document.getElementById('messageInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
    });
    document.getElementById('newChatBtn').addEventListener('click', startNewChat);
    document.getElementById('historyBtn').addEventListener('click', showHistory);
    document.getElementById('backBtn').addEventListener('click', hideHistory);
    document.getElementById('newChatFromHistoryBtn').addEventListener('click', () => {
        startNewChat();
        hideHistory();
    });
    document.getElementById('peopleBtn').addEventListener('click', showPeople);
    document.getElementById('backFromPeopleBtn').addEventListener('click', hidePeople);
    document.getElementById('notesBtn').addEventListener('click', showNotes);
    document.getElementById('backFromNotesBtn').addEventListener('click', hideNotes);
}

function initChatResize() {
    const handle = document.getElementById('chatResizeHandle');
    const panel  = document.getElementById('chatPanel');
    let startX, startW;

    const saved = localStorage.getItem('tacit_chat_width');
    if (saved) { panel.style.width = saved; panel.style.minWidth = saved; }

    handle.addEventListener('mousedown', (e) => {
        startX = e.clientX;
        startW = panel.offsetWidth;
        handle.classList.add('dragging');
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';

        function onMove(e) {
            const w = Math.max(240, Math.min(520, startW + e.clientX - startX));
            panel.style.width = w + 'px';
            panel.style.minWidth = w + 'px';
        }
        function onUp() {
            handle.classList.remove('dragging');
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            localStorage.setItem('tacit_chat_width', panel.style.width);
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        e.preventDefault();
    });
}


async function restoreOrStartSession() {
    const saved = localStorage.getItem('tacit_session_id');
    if (saved) {
        currentSessionId = saved;
        try {
            const res = await apiFetch(`${API_BASE}/chat/history/${saved}`);
            const data = await res.json();
            if (data.messages && data.messages.length > 0) {
                data.messages.forEach(msg =>
                    addMessage(msg.role, msg.content, false, msg.sources || [])
                );
                return;
            }
        } catch (e) {
            console.error('[Tacit] restore error:', e);
        }
        addWelcomeMessage();
        return;
    }

    // No local session — fall back to most recent conversation on server (cross-device sync)
    try {
        const histRes = await apiFetch(`${API_BASE}/conversations?limit=1`);
        if (histRes.ok) {
            const histData = await histRes.json();
            const convos = histData.conversations || [];
            if (convos.length > 0 && convos[0].message_count > 0) {
                const sessionId = convos[0].session_id;
                currentSessionId = sessionId;
                localStorage.setItem('tacit_session_id', currentSessionId);
                const msgRes = await apiFetch(`${API_BASE}/chat/history/${sessionId}`);
                if (msgRes.ok) {
                    const msgData = await msgRes.json();
                    if (msgData.messages && msgData.messages.length > 0) {
                        msgData.messages.forEach(msg =>
                            addMessage(msg.role, msg.content, false, msg.sources || [])
                        );
                        return;
                    }
                }
            }
        }
    } catch (e) {
        console.error('[Tacit] cross-device restore error:', e);
    }

    currentSessionId = generateSessionId();
    localStorage.setItem('tacit_session_id', currentSessionId);
    addWelcomeMessage();
}

function startNewChat() {
    currentSessionId = generateSessionId();
    localStorage.setItem('tacit_session_id', currentSessionId);
    document.getElementById('messages').innerHTML = '';
    addWelcomeMessage();
}

// ==================== HISTORY ====================

async function showHistory() {
    document.getElementById('chatPanel').classList.add('history-mode');
    await loadConversationHistory();
}

function hideHistory() {
    document.getElementById('chatPanel').classList.remove('history-mode');
}

// ==================== PEOPLE ====================

function showPeople() {
    const panel = document.getElementById('chatPanel');
    panel.classList.remove('history-mode', 'notes-mode');
    panel.classList.add('people-mode');
    loadPeopleList();
}

function hidePeople() {
    document.getElementById('chatPanel').classList.remove('people-mode');
}

function showNotes() {
    const panel = document.getElementById('chatPanel');
    panel.classList.remove('history-mode', 'people-mode');
    panel.classList.add('notes-mode');
    loadNotesList();
}

function hideNotes() {
    document.getElementById('chatPanel').classList.remove('notes-mode');
}

async function loadNotesList() {
    const list = document.getElementById('notesList');
    list.innerHTML = '<div class="notes-loading">Loading…</div>';
    try {
        const res = await apiFetch('/api/notes');
        const data = await res.json();
        const notes = data.notes || [];
        if (!notes.length) {
            list.innerHTML = '<div class="notes-empty">No notes yet.<br>Paste text in chat to create one.</div>';
            return;
        }
        list.innerHTML = notes.map(buildNoteCard).join('');
        list.querySelectorAll('.note-delete-btn').forEach(btn => {
            btn.addEventListener('click', async e => {
                e.stopPropagation();
                const card = btn.closest('[data-note-id]');
                const id = card.dataset.noteId;
                btn.disabled = true;
                try {
                    const r = await apiFetch(`/api/nodes/${encodeURIComponent(id)}`, { method: 'DELETE' });
                    if (r.ok) {
                        card.remove();
                        if (!list.querySelector('.note-card')) {
                            list.innerHTML = '<div class="notes-empty">No notes yet.<br>Paste text in chat to create one.</div>';
                        }
                    } else {
                        btn.disabled = false;
                        showToast('Could not delete note', 'error');
                    }
                } catch {
                    btn.disabled = false;
                    showToast('Could not delete note', 'error');
                }
            });
        });
    } catch {
        list.innerHTML = '<div class="notes-empty">Failed to load notes.</div>';
    }
}

function buildNoteCard(n) {
    const date = n.created_at ? new Date(n.created_at).toLocaleDateString() : '';
    const isProcessing = n.status === 'processing' || n.status === 'pending';
    const summaryHtml = isProcessing
        ? '<div class="notes-loading" style="padding:4px 0;text-align:left">Processing…</div>'
        : n.summary ? `<div class="note-card-summary">${escapeHtml(n.summary)}</div>` : '';
    const expandHtml = n.content ? `
        <details>
            <summary class="note-card-expand">Show full text</summary>
            <div class="note-card-full">${escapeHtml(n.content)}</div>
        </details>` : '';
    const tags = (n.tags || []).map(t => `<span class="note-card-tag">${escapeHtml(t)}</span>`).join('');
    return `<div class="note-card" data-note-id="${escapeHtml(n.id)}">
        <div class="note-card-header">
            <span class="note-card-title">${escapeHtml(n.title)}</span>
            <button class="note-delete-btn" title="Delete note">✕</button>
        </div>
        ${date ? `<div class="note-card-date">${date}</div>` : ''}
        ${summaryHtml}
        ${expandHtml}
        ${tags ? `<div class="note-card-tags">${tags}</div>` : ''}
    </div>`;
}

async function loadPeopleList() {
    const list = document.getElementById('peopleList');
    list.innerHTML = '<div class="people-loading">Loading…</div>';
    try {
        const res = await apiFetch(`${API_BASE}/people`);
        const data = await res.json();
        const people = data.people || [];
        if (!people.length) {
            list.innerHTML = '<div class="people-empty">No people recorded yet.<br>Mention someone in chat to add them.</div>';
            return;
        }
        list.innerHTML = people.map(buildPersonCard).join('');
        list.querySelectorAll('.person-delete-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                const card = btn.closest('.person-card');
                const personId = card.dataset.personId;
                btn.disabled = true;
                try {
                    const res = await apiFetch(`${API_BASE}/people/${encodeURIComponent(personId)}`, { method: 'DELETE' });
                    if (res.ok) {
                        card.remove();
                        if (!list.querySelector('.person-card')) {
                            list.innerHTML = '<div class="people-empty">No people recorded yet.<br>Mention someone in chat to add them.</div>';
                        }
                    } else {
                        btn.disabled = false;
                        showToast('Could not delete person', 'error');
                    }
                } catch (err) {
                    btn.disabled = false;
                    showToast('Could not delete person', 'error');
                }
            });
        });
    } catch (e) {
        list.innerHTML = '<div class="people-empty">Failed to load people.</div>';
    }
}

function buildPersonCard(p) {
    const meta = [p.role, p.organization].filter(Boolean).join(' · ');
    const relBadge = p.relationship ? `<span class="person-rel">${escapeHtml(p.relationship)}</span>` : '';
    const actions = (p.action_items || []).length
        ? `<div class="person-section-label">Action Items</div>
           <ul class="person-action-items">
             ${p.action_items.map(a => `<li>${escapeHtml(a)}</li>`).join('')}
           </ul>`
        : '';
    const notes = (p.notes || []).slice(-3).reverse().map(n => {
        const text = typeof n === 'string' ? n : (n.text || '');
        const dateStr = (n && n.date) ? new Date(n.date).toLocaleDateString(undefined, {month:'short', day:'numeric'}) : '';
        return `<div class="person-note">
            ${dateStr ? `<div class="person-note-date">${escapeHtml(dateStr)}</div>` : ''}
            ${escapeHtml(text)}
        </div>`;
    }).join('');
    const noteSection = notes ? `<div class="person-section-label">Notes</div>${notes}` : '';
    return `<div class="person-card" data-person-id="${escapeHtml(p.id)}">
        <div class="person-card-header">
            <div class="person-name">${escapeHtml(p.name)}</div>
            <button class="person-delete-btn" title="Remove from memory">✕</button>
        </div>
        ${meta ? `<div class="person-meta">${escapeHtml(meta)}</div>` : ''}
        ${relBadge}
        ${actions}
        ${noteSection}
    </div>`;
}

async function loadConversationHistory() {
    const list = document.getElementById('historyList');
    list.innerHTML = '<div class="history-loading">Loading…</div>';
    try {
        const res = await apiFetch(`${API_BASE}/conversations`);
        const data = await res.json();
        const convs = data.conversations || [];
        if (convs.length === 0) {
            list.innerHTML = '<div class="history-empty">No conversations yet.<br>Start chatting to build history.</div>';
            return;
        }
        list.innerHTML = buildHistoryHTML(convs);
        list.querySelectorAll('.history-item').forEach(el => {
            el.addEventListener('click', (e) => {
                // Don't load conversation if clicking delete button
                if (e.target.classList.contains('history-item-delete')) return;
                loadConversation(el.dataset.session);
                hideHistory();
            });

            el.querySelector('.history-item-delete').addEventListener('click', async (e) => {
                e.stopPropagation();
                const sessionId = el.dataset.session;
                if (!confirm('Delete this conversation?')) return;

                try {
                    const res = await apiFetch(`${API_BASE}/chat/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
                    if (res.ok) {
                        el.remove();
                        if (sessionId === currentSessionId) {
                            document.getElementById('messages').innerHTML = '';
                            currentSessionId = null;
                        }
                        // If no conversations left, show empty state
                        if (list.querySelectorAll('.history-item').length === 0) {
                            list.innerHTML = '<div class="history-empty">No conversations yet.<br>Start chatting to build history.</div>';
                        }
                    } else {
                        showToast('Could not delete conversation', 'error');
                    }
                } catch (e) {
                    showToast('Failed to delete conversation: ' + e.message, 'error');
                    console.error('[Tacit] delete conversation error:', e);
                }
            });
        });
    } catch (e) {
        list.innerHTML = '<div class="history-empty">Failed to load history.</div>';
    }
}

function buildHistoryHTML(convs) {
    const startOfToday     = new Date(new Date().setHours(0,0,0,0)).getTime();
    const startOfYesterday = startOfToday - 86400000;
    const startOfWeek      = startOfToday - 6 * 86400000;
    const groups = [
        { label: 'Today',     items: [] },
        { label: 'Yesterday', items: [] },
        { label: 'This week', items: [] },
        { label: 'Older',     items: [] },
    ];
    for (const c of convs) {
        const t = c.last_activity ? new Date(c.last_activity).getTime() : 0;
        if      (t >= startOfToday)     groups[0].items.push(c);
        else if (t >= startOfYesterday) groups[1].items.push(c);
        else if (t >= startOfWeek)      groups[2].items.push(c);
        else                            groups[3].items.push(c);
    }
    return groups.filter(g => g.items.length > 0).map(g => `
        <div class="history-group-label">${g.label}</div>
        ${g.items.map(buildHistoryItem).join('')}
    `).join('');
}

function buildHistoryItem(c) {
    const isActive = c.session_id === currentSessionId;
    const parts = [];
    if (c.last_activity) parts.push(formatRelativeTime(c.last_activity));
    if (c.message_count) parts.push(`${c.message_count} ${c.message_count === 1 ? 'msg' : 'msgs'}`);
    const title = c.title || c.preview || 'Empty conversation';
    return `<div class="history-item${isActive ? ' active' : ''}" data-session="${escapeHtml(c.session_id)}">
        <div class="history-item-content">
            <div class="history-item-title">${escapeHtml(title)}</div>
            <div class="history-item-meta">${parts.join(' · ')}</div>
        </div>
        <button class="history-item-delete" title="Delete conversation">✕</button>
    </div>`;
}

async function loadConversation(sessionId) {
    currentSessionId = sessionId;
    localStorage.setItem('tacit_session_id', sessionId);
    document.getElementById('messages').innerHTML = '';
    try {
        const res = await apiFetch(`${API_BASE}/chat/history/${sessionId}`);
        const data = await res.json();
        if (data.messages && data.messages.length > 0) {
            data.messages.forEach(msg => addMessage(msg.role, msg.content, false, msg.sources || []));
        } else { addWelcomeMessage(); }
    } catch (e) { addWelcomeMessage(); }
}

function formatRelativeTime(iso) {
    const diff = Date.now() - new Date(iso).getTime();
    const m = Math.floor(diff / 60000);
    const h = Math.floor(diff / 3600000);
    const d = Math.floor(diff / 86400000);
    if (m < 1)   return 'just now';
    if (m < 60)  return `${m}m ago`;
    if (h < 24)  return `${h}h ago`;
    if (d === 1) return 'yesterday';
    if (d < 7)   return `${d}d ago`;
    return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

async function sendMessage() {
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    if (!message) return;

    addMessage('user', message);
    input.value = '';

    const loadingId = addLoadingMessage();

    try {
        const res = await apiFetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, session_id: currentSessionId }),
        });
        if (res.status === 402) {
            removeMessage(loadingId);
            show402Modal();
            await loadUsageMeter();
            return;
        }
        if (!res.ok) throw new Error(`Server error: ${res.status}`);
        const data = await res.json();
        removeMessage(loadingId);
        localStorage.setItem('tacit_session_id', currentSessionId);
        addMessage('assistant', data.response, false, data.sources || []);

        // Handle canvas actions (e.g. edges created/removed via chat)
        if (data.actions && data.actions.length > 0) {
            let edgesChanged = 0;
            for (const action of data.actions) {
                if (action.type === 'edge_created') {
                    graphData.edges.push({
                        id: action.edge_id,
                        source_id: action.source_id,
                        target_id: action.target_id,
                        label: action.label,
                        strength: 0.8,
                        auto_generated: false
                    });
                    edgesChanged++;
                }
                if (action.type === 'edge_removed') {
                    graphData.edges = graphData.edges.filter(e => e.id !== action.edge_id);
                    edgesChanged++;
                }
                if (action.type === 'arrange_canvas') {
                    autoArrangeByCategory();
                }
                if (action.type === 'chaos_canvas') {
                    triggerCanvasChaos(action.positions || []);
                }
                if (action.type === 'node_created') {
                    if (nodeElements[action.node_id]) continue;
                    const node = {
                        id: action.node_id,
                        type: action.node_type || 'note',
                        title: action.title,
                        summary: null,
                        thumbnail_url: null,
                        url: null,
                        canvas_x: action.canvas_x,
                        canvas_y: action.canvas_y,
                        status: 'done',
                        tags: [],
                        metadata: {},
                    };
                    createCard(node);
                    graphData.nodes.push(node);
                    updateEmptyState(graphData.nodes.length);
                    if (action.node_type === 'note' && document.getElementById('chatPanel').classList.contains('notes-mode')) {
                        setTimeout(loadNotesList, 500);
                    }
                }

                if (action.type === 'ingest_started') {
                    if (nodeElements[action.node_id]) continue;
                    const vp = document.getElementById('canvasViewport');
                    const cx = isMobile() ? 100 + graphData.nodes.length * 10 : (-canvasX + vp.clientWidth / 2) / canvasScale + (Math.random() * 200 - 100);
                    const cy = isMobile() ? 100 + graphData.nodes.length * 10 : (-canvasY + vp.clientHeight / 2) / canvasScale + (Math.random() * 200 - 100);
                    const placeholderNode = {
                        id: action.node_id, type: action.node_type || 'webpage',
                        title: action.title || action.url, summary: null,
                        thumbnail_url: null, url: action.url,
                        canvas_x: cx, canvas_y: cy, status: 'processing',
                        tags: [], metadata: {},
                    };
                    createCard(placeholderNode);
                    graphData.nodes.push(placeholderNode);
                    updateEmptyState(graphData.nodes.length);
                    pollNodeStatus(action.node_id);
                    if (action.node_type === 'note' && document.getElementById('chatPanel').classList.contains('notes-mode')) {
                        setTimeout(loadNotesList, 500);
                    }
                }
            }
            if (edgesChanged > 0) {
                drawEdges(graphData.edges);
            }
        }
    } catch (e) {
        removeMessage(loadingId);
        addMessage('assistant', 'Sorry, something went wrong. Please try again.');
        console.error('[Tacit] chat error:', e);
    }
}

function addWelcomeMessage() {
    const firstName = (clerkInstance && clerkInstance.user && clerkInstance.user.firstName) || 'there';
    const message = `**Hey ${firstName}!** 👋\n\n` +
        `I'm Tacit, your AI work twin. Here's what I can do:\n\n` +
        `📎 **Add content** — Paste any URL (YouTube, TikTok, articles, PDFs) in the bar above\n` +
        `🤖 **Transcribe & summarize** — I'll automatically turn videos into text and create summaries\n` +
        `💬 **Chat & connect** — Ask me questions about your content. I'll find answers and show sources\n` +
        `🔗 **Organize automatically** — I'll tag, categorize, and find hidden connections\n\n` +
        `Ready? Paste your first URL above! 🚀`;
    addMessage('assistant', message);
}

function addMessage(role, content, isLoading = false, sources = []) {
    const container = document.getElementById('messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    const id = 'msg-' + Date.now() + '-' + Math.random().toString(36).substr(2, 9);
    msgDiv.id = id;

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';

    if (isLoading) {
        bubble.innerHTML = '<div class="loading-dots"><span></span><span></span><span></span></div>';
    } else if (role === 'assistant' && typeof marked !== 'undefined') {
        bubble.innerHTML = marked.parse(content);
    } else {
        bubble.textContent = content;
    }

    msgDiv.appendChild(bubble);

    if (sources && sources.length > 0) {
        const details = document.createElement('details');
        details.className = 'message-sources';
        const summary = document.createElement('summary');
        summary.className = 'sources-label';
        summary.textContent = `Sources (${sources.length})`;
        details.appendChild(summary);
        const chipsDiv = document.createElement('div');
        chipsDiv.className = 'sources-chips';
        chipsDiv.innerHTML = sources.map(s => {
            const title = s.title || s.filename || 'Source';
            const icon = s.type === 'youtube' ? '▶' : s.type === 'node' ? '◉' : s.type === 'document' ? '📄' : '◈';
            return `<span class="source-chip">${icon} ${escapeHtml(title)}</span>`;
        }).join('');
        details.appendChild(chipsDiv);
        msgDiv.appendChild(details);
    }

    container.appendChild(msgDiv);
    container.scrollTop = container.scrollHeight;
    return id;
}

function addLoadingMessage() {
    return addMessage('assistant', '', true);
}

function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// ==================== UI ====================

function initUI() {
    document.getElementById('toggleChatBtn').addEventListener('click', toggleChat);
    document.getElementById('resetViewBtn').addEventListener('click', resetView);
    document.getElementById('toggleCategoryBtn').addEventListener('click', toggleCategory);
    document.getElementById('closeDetailBtn').addEventListener('click', () => {
        document.getElementById('nodeDetailPanel').classList.remove('open');
    });

    // Tour
    document.getElementById('startTourBtn').addEventListener('click', showTour);
    document.querySelector('.tour-close').addEventListener('click', closeTour);
    document.querySelector('.tour-btn-prev').addEventListener('click', () => {
        if (currentTourStep > 0) {
            currentTourStep--;
            updateTourStep();
        }
    });
    document.querySelector('.tour-btn-next').addEventListener('click', () => {
        if (currentTourStep < TOUR_STEPS.length - 1) {
            currentTourStep++;
            updateTourStep();
        }
    });

    // Bulk add
    document.getElementById('bulkAddBtn').addEventListener('click', showBulkAddModal);

    // Search
    document.getElementById('searchToggleBtn').addEventListener('click', () => {
        const bar = document.getElementById('headerSearchBar');
        bar.classList.toggle('open');
        if (bar.classList.contains('open')) {
            document.getElementById('canvasSearchInput').focus();
        } else {
            document.getElementById('canvasSearchInput').value = '';
            clearSearchFilter();
        }
    });
    document.getElementById('canvasSearchInput').addEventListener('input', e => {
        filterBySearch(e.target.value);
    });
    document.getElementById('searchClearBtn').addEventListener('click', () => {
        document.getElementById('canvasSearchInput').value = '';
        clearSearchFilter();
        document.getElementById('headerSearchBar').classList.remove('open');
    });
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && activeSearch) {
            document.getElementById('canvasSearchInput').value = '';
            clearSearchFilter();
            document.getElementById('headerSearchBar').classList.remove('open');
        }
    });
}

function toggleChat() {
    document.getElementById('chatPanel').classList.toggle('collapsed');
}

const TOUR_STEPS = [
    {
        title: "Welcome to Tacit",
        text: "Your second brain for capturing and connecting ideas.\n\nLet's get started with a quick tour."
    },
    {
        title: "Add Content",
        text: "Paste any URL in the bar at the top:\n• YouTube videos\n• TikTok clips\n• Articles & websites\n• PDFs & documents\n\nTacit will automatically transcribe, summarize, and tag everything."
    },
    {
        title: "Chat to Connect",
        text: "Click the 💬 button to ask questions about your content.\n\nTacit searches your canvas and gives you answers with sources.\n\nExample: \"What do I have about AI agents?\""
    },
    {
        title: "Organize & Explore",
        text: "Click 'Tags' to see categories.\n\nUse 'Arrange' to organize visually.\n\nClick the ⌕ button to search your cards."
    },
    {
        title: "You're Ready!",
        text: "Add your first URL above and watch Tacit process it.\n\nStart building your second brain!"
    }
];

let currentTourStep = 0;

function showTour() {
    currentTourStep = 0;
    updateTourStep();
    document.getElementById('tourModal').style.display = 'flex';
}

function updateTourStep() {
    const step = TOUR_STEPS[currentTourStep];
    document.getElementById('tourTitle').textContent = step.title;
    document.getElementById('tourText').textContent = step.text;
    document.getElementById('tourStep').textContent = `${currentTourStep + 1} / ${TOUR_STEPS.length}`;

    const prevBtn = document.querySelector('.tour-btn-prev');
    const nextBtn = document.querySelector('.tour-btn-next');

    prevBtn.disabled = currentTourStep === 0;
    nextBtn.textContent = currentTourStep === TOUR_STEPS.length - 1 ? "Done ✓" : "Next →";

    if (currentTourStep === TOUR_STEPS.length - 1) {
        nextBtn.addEventListener('click', closeTour, { once: true });
    }
}

function closeTour() {
    document.getElementById('tourModal').style.display = 'none';
    document.getElementById('emptyState').style.display = 'none';
}

function setupEmptyState() {
    if (clerkInstance && clerkInstance.user) {
        const firstName = clerkInstance.user.firstName || 'there';
        const emptyStateH2 = document.querySelector('.empty-state h2');
        const emptyStateP = document.querySelector('.empty-state p');
        if (emptyStateH2) emptyStateH2.textContent = `Welcome, ${firstName}!`;
        if (emptyStateP) emptyStateP.textContent = "Let's start building your second brain.";
    }
}

function updateEmptyState(nodeCount) {
    document.getElementById('emptyState').style.display = nodeCount === 0 ? 'flex' : 'none';
}

// ==================== CATEGORY SIDEBAR ====================

function initCategorySidebar() {
    document.getElementById('autoArrangeBtn').addEventListener('click', autoArrangeByCategory);
    document.getElementById('showAllBtn').addEventListener('click', clearCategoryFilter);
}

function toggleCategory() {
    const sidebar = document.getElementById('categorySidebar');
    const isOpening = !sidebar.classList.contains('open');
    sidebar.classList.toggle('open');
    if (isOpening) {
        document.getElementById('nodeDetailPanel').classList.remove('open');
    }
}

async function loadCategories() {
    try {
        const res = await apiFetch(`${API_BASE}/categories`);
        const data = await res.json();
        const list = document.getElementById('categoryList');
        list.innerHTML = '';
        (data.categories || []).forEach(cat => {
            const item = document.createElement('div');
            item.className = 'category-item' + (activeCategory === cat.name ? ' active' : '');
            const color = categoryColor(cat.name);
            item.innerHTML = `<span class="category-dot" style="background:${color}"></span>
                <span class="category-name">${escapeHtml(cat.name)}</span>
                <span class="category-count">${cat.count}</span>`;
            item.addEventListener('click', () => {
                filterByCategory(cat.name);
                autoArrangeByCategory();
            });
            list.appendChild(item);
        });
    } catch (e) {
        console.error('[Tacit] loadCategories error:', e);
    }
}

function filterByCategory(name) {
    if (activeCategory === name) {
        clearCategoryFilter();
        return;
    }
    activeCategory = name;
    for (const [id, card] of Object.entries(nodeElements)) {
        if (card.dataset.category === name) {
            card.classList.remove('dimmed');
            card.classList.add('highlighted');
        } else {
            card.classList.add('dimmed');
            card.classList.remove('highlighted');
        }
    }
    // Update sidebar active state
    document.querySelectorAll('.category-item').forEach(el => {
        el.classList.toggle('active', el.querySelector('.category-name').textContent === name);
    });
    drawEdges(graphData.edges); // edges will respect dimmed state
}

function clearCategoryFilter() {
    activeCategory = null;
    for (const card of Object.values(nodeElements)) {
        card.classList.remove('dimmed', 'highlighted');
    }
    document.querySelectorAll('.category-item').forEach(el => el.classList.remove('active'));
    drawEdges(graphData.edges);
}

function filterBySearch(query) {
    activeSearch = query;
    const q = query.toLowerCase().trim();
    if (!q) { clearSearchFilter(); return; }
    for (const [id, card] of Object.entries(nodeElements)) {
        const node = graphData.nodes.find(n => n.id === id);
        if (!node) continue;
        const hit =
            (node.title || '').toLowerCase().includes(q) ||
            (node.summary || '').toLowerCase().includes(q) ||
            (node.url || '').toLowerCase().includes(q) ||
            ((node.metadata && node.metadata.category) || '').toLowerCase().includes(q) ||
            (node.tags || []).some(t => t.toLowerCase().includes(q));
        card.classList.toggle('dimmed', !hit);
        if (hit) card.classList.remove('highlighted');
    }
    drawEdges(graphData.edges);
}

function clearSearchFilter() {
    activeSearch = '';
    for (const card of Object.values(nodeElements)) {
        card.classList.remove('dimmed', 'highlighted');
    }
    drawEdges(graphData.edges);
}

function triggerCanvasChaos(positions) {
    // Add transition class to all cards so they animate smoothly to chaos positions
    Object.values(nodeElements).forEach(card => card.classList.add('card-chaos'));

    positions.forEach(({ id, x, y, rotation }) => {
        const card = nodeElements[id];
        if (!card) return;
        card.style.left = x + 'px';
        card.style.top = y + 'px';
        card.style.transform = `rotate(${rotation}deg)`;
        // Update in-memory graph data
        const node = graphData.nodes.find(n => n.id === id);
        if (node) { node.canvas_x = x; node.canvas_y = y; }
    });

    drawEdges(graphData.edges);

    // Remove transition class after animation completes
    setTimeout(() => {
        Object.values(nodeElements).forEach(card => {
            card.classList.remove('card-chaos');
        });
    }, 1600);

    showToast('🌪 Canvas scattered!', 'success');
}

function autoArrangeByCategory() {
    if (isMobile()) return;
    const groups = {};
    graphData.nodes.forEach(node => {
        const cat = (node.metadata && node.metadata.category) || 'Uncategorized';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(node);
    });

    const cardW = 280, cardH = 320, gap = 20, colsPerGroup = 3;
    const groupGapY = 60, labelH = 34;
    const colStride = colsPerGroup * (cardW + gap) + 80; // horizontal distance between columns

    const groupNames = Object.keys(groups).sort();
    document.querySelectorAll('.cluster-label').forEach(el => el.remove());

    // Two-column masonry: assign each group to the shorter column (greedy)
    const NUM_COLS = 2;
    const colY = Array(NUM_COLS).fill(60); // current y-cursor per column

    groupNames.forEach(cat => {
        const nodes = groups[cat];
        const rows = Math.ceil(nodes.length / colsPerGroup);

        // Pick the shorter column
        const colIdx = colY[0] <= colY[1] ? 0 : 1;
        const groupX = 60 + colIdx * colStride;
        const groupY = colY[colIdx];

        const label = document.createElement('div');
        label.className = 'cluster-label';
        label.style.left = groupX + 'px';
        label.style.top = groupY + 'px';
        label.style.color = categoryColor(cat);
        label.textContent = cat;
        document.getElementById('canvasSurface').appendChild(label);

        nodes.forEach((node, i) => {
            const nx = groupX + (i % colsPerGroup) * (cardW + gap);
            const ny = groupY + labelH + Math.floor(i / colsPerGroup) * (cardH + gap);
            const card = nodeElements[node.id];
            if (card) {
                card.style.transition = 'left 0.5s ease, top 0.5s ease, transform 0.5s ease';
                card.style.left = nx + 'px';
                card.style.top = ny + 'px';
                card.style.transform = '';
                setTimeout(() => { card.style.transition = ''; }, 600);
                saveNodePosition(node.id, nx, ny);
            }
        });

        colY[colIdx] += labelH + rows * (cardH + gap) + groupGapY;
    });

    // Zoom to fit all arranged cards
    setTimeout(() => {
        const totalW = 60 + NUM_COLS * colStride;
        const totalH = Math.max(...colY) + 60;
        const vp = document.getElementById('canvasViewport');
        const scaleX = vp.clientWidth / totalW;
        const scaleY = vp.clientHeight / totalH;
        canvasScale = Math.min(scaleX, scaleY, 1.0);
        canvasX = 0;
        canvasY = 0;
        applyTransform();
    }, 620);

    setTimeout(() => drawEdges(graphData.edges), 600);
    showToast('Arranged by category', 'success');
}

// ==================== PROACTIVE INSIGHTS ====================

async function loadInsights() {
    try {
        const res = await apiFetch(`${API_BASE}/insights`);
        const data = await res.json();
        if (data.total_nodes === 0) return;

        const parts = [`**Welcome back!** Your canvas has **${data.total_nodes} nodes** across **${Object.keys(data.categories).length} categories**.`];

        if (data.new_since_last_visit && data.new_since_last_visit.length > 0) {
            const names = data.new_since_last_visit.slice(0, 3).map(n => `"${n.title}"`).join(', ');
            parts.push(`**New since last visit:** ${names}`);
        }

        if (data.orphan_nodes && data.orphan_nodes.length > 0) {
            parts.push(`**${data.orphan_nodes.length} node${data.orphan_nodes.length > 1 ? 's have' : ' has'} no connections** — ask me to link them.`);
        }

        // Only replace welcome if we have a non-empty canvas
        const messages = document.getElementById('messages');
        const firstMsg = messages.querySelector('.message.assistant');
        if (firstMsg && messages.children.length <= 1) {
            const bubble = firstMsg.querySelector('.message-bubble');
            if (bubble && typeof marked !== 'undefined') {
                bubble.innerHTML = marked.parse(parts.join('\n\n'));
            }
        }
    } catch (e) {
        console.error('[Tacit] loadInsights error:', e);
    }
}

// ==================== HELPERS ====================

function generateSessionId() {
    return `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

function getTypeIcon(type) {
    const icons = {
        youtube: '▶',
        tiktok: '♪',
        instagram: '◈',
        webpage: '◉',
        note: '✎',
        document: '📄',
        text: '✎',
        image: '🖼',
    };
    return icons[type] || '◉';
}

function getDomain(url) {
    try {
        return new URL(url).hostname.replace('www.', '');
    } catch {
        return url.slice(0, 30);
    }
}

function escapeHtml(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function showToast(msg, type = 'info') {
    const toast = document.getElementById('toast');
    toast.textContent = msg;
    toast.className = `toast toast-${type} show`;
    setTimeout(() => toast.classList.remove('show'), 3500);
}
