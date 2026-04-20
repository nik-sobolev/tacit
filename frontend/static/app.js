// Tacit — Second Brain Canvas
console.log('[Tacit] app.js v3 loaded (canvas)');

const API_BASE = '/api';

// ==================== CANVAS STATE ====================

let canvasX = 0, canvasY = 0, canvasScale = 1;
let isPanning = false, panStartX = 0, panStartY = 0;
let isDraggingCard = false;
let dragCard = null, dragOffsetX = 0, dragOffsetY = 0;
const nodeElements = {};  // nodeId → DOM card element
let graphData = { nodes: [], edges: [] };
let activeCategory = null; // currently filtered category

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
    initCanvas();
    initIngestion();
    initChat();
    initChatResize();
    initUI();
    initCategorySidebar();
    await restoreOrStartSession();
    await loadGraph();
    await loadCategories();
    await loadInsights();
});

// ==================== CANVAS ENGINE ====================

function initCanvas() {
    const viewport = document.getElementById('canvasViewport');
    const surface = document.getElementById('canvasSurface');

    // Pan: drag on empty canvas
    viewport.addEventListener('mousedown', (e) => {
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
        if (isDraggingCard && dragCard) {
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
            saveNodePosition(nodeId, x, y);
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
    const viewport = document.getElementById('canvasViewport');
    canvasX = -node.canvas_x * canvasScale + viewport.clientWidth / 2;
    canvasY = -node.canvas_y * canvasScale + viewport.clientHeight / 2;
    applyTransform();
}

// ==================== GRAPH LOADING ====================

async function loadGraph() {
    try {
        const res = await fetch(`${API_BASE}/graph`);
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
    card.style.left = (node.canvas_x || 100) + 'px';
    card.style.top = (node.canvas_y || 100) + 'px';

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
    return {
        x: parseFloat(cardEl.style.left) + cardEl.offsetWidth / 2,
        y: parseFloat(cardEl.style.top) + cardEl.offsetHeight / 2,
    };
}

async function saveNodePosition(nodeId, x, y) {
    try {
        await fetch(`${API_BASE}/nodes/${nodeId}`, {
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

    // Allow dropping URLs directly
    document.addEventListener('dragover', (e) => e.preventDefault());
    document.addEventListener('drop', (e) => {
        e.preventDefault();
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

    // Place new card near center of current viewport
    const viewport = document.getElementById('canvasViewport');
    const vw = viewport.clientWidth, vh = viewport.clientHeight;
    const cx = (-canvasX + vw / 2) / canvasScale + (Math.random() * 200 - 100);
    const cy = (-canvasY + vh / 2) / canvasScale + (Math.random() * 200 - 100);

    try {
        const res = await fetch(`${API_BASE}/ingest`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, canvas_x: cx, canvas_y: cy }),
        });

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

function pollNodeStatus(nodeId, attempts = 0) {
    if (attempts > 60) return; // give up after ~2 minutes

    setTimeout(async () => {
        try {
            const res = await fetch(`${API_BASE}/ingest/${nodeId}/status`);
            const data = await res.json();

            if (data.status === 'done') {
                // Reload full graph to get edges too
                const graphRes = await fetch(`${API_BASE}/graph`);
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
        const res = await fetch(`${API_BASE}/nodes/${nodeId}`);
        const node = await res.json();

        typeBadge.textContent = node.type;
        typeBadge.className = `detail-type-badge type-${node.type}`;

        let relatedHTML = '';
        try {
            const relRes = await fetch(`${API_BASE}/nodes/${nodeId}/related`);
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
            ${node.thumbnail_url ? `<img class="detail-thumb" src="${escapeHtml(node.thumbnail_url)}" alt="" onerror="this.style.display='none'" />` : ''}
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
        await fetch(`${API_BASE}/nodes/${nodeId}`, { method: 'DELETE' });
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
            const res = await fetch(`${API_BASE}/chat/history/${saved}`);
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
    panel.classList.remove('history-mode');
    panel.classList.add('people-mode');
    loadPeopleList();
}

function hidePeople() {
    document.getElementById('chatPanel').classList.remove('people-mode');
}

async function loadPeopleList() {
    const list = document.getElementById('peopleList');
    list.innerHTML = '<div class="people-loading">Loading…</div>';
    try {
        const res = await fetch(`${API_BASE}/people`);
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
                    const res = await fetch(`${API_BASE}/people/${encodeURIComponent(personId)}`, { method: 'DELETE' });
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
        const res = await fetch(`${API_BASE}/conversations`);
        const data = await res.json();
        const convs = data.conversations || [];
        if (convs.length === 0) {
            list.innerHTML = '<div class="history-empty">No conversations yet.<br>Start chatting to build history.</div>';
            return;
        }
        list.innerHTML = buildHistoryHTML(convs);
        list.querySelectorAll('.history-item').forEach(el => {
            el.addEventListener('click', () => {
                loadConversation(el.dataset.session);
                hideHistory();
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
    return `<div class="history-item${isActive ? ' active' : ''}" data-session="${escapeHtml(c.session_id)}">
        <div class="history-item-title">${escapeHtml(c.preview || 'Empty conversation')}</div>
        <div class="history-item-meta">${parts.join(' · ')}</div>
    </div>`;
}

async function loadConversation(sessionId) {
    currentSessionId = sessionId;
    localStorage.setItem('tacit_session_id', sessionId);
    document.getElementById('messages').innerHTML = '';
    try {
        const res = await fetch(`${API_BASE}/chat/history/${sessionId}`);
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
        const res = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, session_id: currentSessionId }),
        });
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
    addMessage('assistant', '**Welcome to Tacit!** Drop any URL above to add it to your canvas — YouTube videos, TikTok, Instagram, or any webpage. I\'ll transcribe, summarize, and connect everything automatically.\n\nYou can also ask me anything about the content in your canvas.');
}

function addMessage(role, content, isLoading = false, sources = []) {
    const container = document.getElementById('messages');
    const msgDiv = document.createElement('div');
    msgDiv.className = `message ${role}`;
    const id = 'msg-' + Date.now();
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
}

function toggleChat() {
    document.getElementById('chatPanel').classList.toggle('collapsed');
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
        const res = await fetch(`${API_BASE}/categories`);
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
            item.addEventListener('click', () => filterByCategory(cat.name));
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

function autoArrangeByCategory() {
    // Group nodes by category
    const groups = {};
    graphData.nodes.forEach(node => {
        const cat = (node.metadata && node.metadata.category) || 'Uncategorized';
        if (!groups[cat]) groups[cat] = [];
        groups[cat].push(node);
    });

    const cardW = 280, cardH = 320, gap = 20, colsPerGroup = 3;
    const groupGapX = 100, groupGapY = 80;
    let groupX = 60, groupY = 60;
    const groupNames = Object.keys(groups).sort();
    let col = 0;

    // Remove old cluster labels
    document.querySelectorAll('.cluster-label').forEach(el => el.remove());

    groupNames.forEach(cat => {
        const nodes = groups[cat];
        const rows = Math.ceil(nodes.length / colsPerGroup);

        // Add cluster label
        const label = document.createElement('div');
        label.className = 'cluster-label';
        label.style.left = groupX + 'px';
        label.style.top = (groupY - 30) + 'px';
        label.style.color = categoryColor(cat);
        label.textContent = cat;
        document.getElementById('canvasSurface').appendChild(label);

        nodes.forEach((node, i) => {
            const nx = groupX + (i % colsPerGroup) * (cardW + gap);
            const ny = groupY + Math.floor(i / colsPerGroup) * (cardH + gap);
            const card = nodeElements[node.id];
            if (card) {
                card.style.transition = 'left 0.5s ease, top 0.5s ease';
                card.style.left = nx + 'px';
                card.style.top = ny + 'px';
                setTimeout(() => { card.style.transition = ''; }, 600);
                saveNodePosition(node.id, nx, ny);
            }
        });

        // Advance position for next group
        col++;
        if (col % 2 === 0) {
            groupX = 60;
            groupY += rows * (cardH + gap) + groupGapY;
        } else {
            groupX += colsPerGroup * (cardW + gap) + groupGapX;
        }
    });

    setTimeout(() => drawEdges(graphData.edges), 600);
    showToast('Arranged by category', 'success');
}

// ==================== PROACTIVE INSIGHTS ====================

async function loadInsights() {
    try {
        const res = await fetch(`${API_BASE}/insights`);
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
