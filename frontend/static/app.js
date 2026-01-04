// Tacit Frontend Application

const API_BASE = '/api';
let currentSessionId = generateSessionId();

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    initializeEventListeners();
    addWelcomeMessage();
});

function initializeEventListeners() {
    // Chat
    document.getElementById('sendBtn').addEventListener('click', sendMessage);
    document.getElementById('messageInput').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            sendMessage();
        }
    });

    // Context Form
    document.getElementById('contextForm').addEventListener('submit', submitContext);

    // File Upload
    document.getElementById('selectFileBtn').addEventListener('click', () => {
        document.getElementById('fileInput').click();
    });

    document.getElementById('fileInput').addEventListener('change', handleFileSelect);
    document.getElementById('uploadBtn').addEventListener('click', uploadDocument);

    // Tabs
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });

    // Header Actions
    document.getElementById('statsBtn').addEventListener('click', showStats);
    document.getElementById('contextsBtn').addEventListener('click', showContexts);
    document.getElementById('docsBtn').addEventListener('click', showDocuments);

    // Edit Context Form
    document.getElementById('editContextForm').addEventListener('submit', updateContext);

    // Modal close buttons
    document.querySelectorAll('.close').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const modalId = e.target.dataset.modal;
            if (modalId) {
                closeModal(modalId);
            }
        });
    });
}

// ==================== CHAT ====================

async function sendMessage(retryMessage = null) {
    const input = document.getElementById('messageInput');
    const message = retryMessage || input.value.trim();

    if (!message) return;

    // Add user message to chat (only if not retrying)
    if (!retryMessage) {
        addMessage('user', message);
        input.value = '';
    }

    // Show loading spinner
    const loadingId = addLoadingMessage();

    try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 30000); // 30s timeout

        const response = await fetch(`${API_BASE}/chat`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: message,
                session_id: currentSessionId
            }),
            signal: controller.signal
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
            throw new Error(`Server error: ${response.status}`);
        }

        const data = await response.json();

        // Remove loading message
        removeMessage(loadingId);

        // Add assistant response
        addMessage('assistant', data.response, false, data.sources);

    } catch (error) {
        removeMessage(loadingId);

        let errorMessage = 'Sorry, I encountered an error. ';
        if (error.name === 'AbortError') {
            errorMessage = 'Request timed out. ';
        } else if (!navigator.onLine) {
            errorMessage = 'No internet connection. ';
        }

        addErrorMessageWithRetry(errorMessage + 'Please try again.', message);
        console.error('Chat error:', error);
    }
}

function addLoadingMessage() {
    const messagesContainer = document.getElementById('messages');
    const messageId = Date.now().toString();

    const messageDiv = document.createElement('div');
    messageDiv.className = 'message assistant';
    messageDiv.id = `msg-${messageId}`;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.innerHTML = '<div class="loading-spinner"><div class="spinner"></div><span>Thinking...</span></div>';

    contentDiv.appendChild(bubble);
    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    return messageId;
}

function addErrorMessageWithRetry(errorText, originalMessage) {
    const messagesContainer = document.getElementById('messages');
    const messageId = Date.now().toString();

    const messageDiv = document.createElement('div');
    messageDiv.className = 'message assistant';
    messageDiv.id = `msg-${messageId}`;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    bubble.innerHTML = `
        <div class="error-text">${errorText}</div>
        <button class="retry-btn" onclick="retryMessage('${originalMessage.replace(/'/g, "\\'")}')">
            <span>↻</span>
            <span>Retry</span>
        </button>
    `;

    contentDiv.appendChild(bubble);
    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);
    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

function retryMessage(message) {
    sendMessage(message);
}

function addMessage(role, content, isLoading = false, sources = []) {
    const messagesContainer = document.getElementById('messages');
    const messageId = Date.now().toString();

    const messageDiv = document.createElement('div');
    messageDiv.className = `message ${role}`;
    messageDiv.id = `msg-${messageId}`;

    const avatar = document.createElement('div');
    avatar.className = 'message-avatar';
    if (role === 'user') avatar.textContent = '👤';

    const contentDiv = document.createElement('div');
    contentDiv.className = 'message-content';

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';

    // Render markdown for assistant messages, plain text for user messages
    if (role === 'assistant' && typeof marked !== 'undefined') {
        bubble.innerHTML = marked.parse(content);
    } else {
        bubble.textContent = content;
    }

    contentDiv.appendChild(bubble);

    // Add sources if present
    if (sources && sources.length > 0) {
        const sourcesDiv = document.createElement('div');
        sourcesDiv.className = 'message-sources';
        sourcesDiv.innerHTML = '<strong>Sources:</strong>';

        const sourcesList = document.createElement('div');
        sourcesList.className = 'sources-list';

        sources.forEach(source => {
            const sourceItem = document.createElement('div');
            sourceItem.className = 'source-item';

            let sourceHTML = '';
            if (source.type === 'context') {
                const icon = getContextTypeIcon(source.context_type);
                const date = source.date ? new Date(source.date).toLocaleDateString() : '';
                sourceHTML = `
                    <span class="source-icon">${icon}</span>
                    <div class="source-details">
                        <div class="source-title">${source.title}</div>
                        <div class="source-meta">
                            ${source.context_type.replace('_', ' ')}
                            ${date ? `· ${date}` : ''}
                            ${source.relevance ? `· ${Math.round(source.relevance * 100)}% match` : ''}
                        </div>
                    </div>
                `;
            } else {
                const icon = '📄';
                sourceHTML = `
                    <span class="source-icon">${icon}</span>
                    <div class="source-details">
                        <div class="source-title">${source.filename}</div>
                        <div class="source-meta">
                            ${source.page ? `Page ${source.page}` : ''}
                            ${source.relevance ? `· ${Math.round(source.relevance * 100)}% match` : ''}
                        </div>
                    </div>
                `;
            }

            sourceItem.innerHTML = sourceHTML;
            sourcesList.appendChild(sourceItem);
        });

        sourcesDiv.appendChild(sourcesList);
        contentDiv.appendChild(sourcesDiv);
    }

    messageDiv.appendChild(avatar);
    messageDiv.appendChild(contentDiv);

    messagesContainer.appendChild(messageDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    return messageId;
}

function getContextTypeIcon(type) {
    const icons = {
        'decision': '💡',
        'meeting_note': '📋',
        'project_context': '📂',
        'strategy': '🎯',
        'insight': '✨',
        'plan': '📝'
    };
    return icons[type] || '📌';
}

function removeMessage(messageId) {
    const message = document.getElementById(`msg-${messageId}`);
    if (message) {
        message.remove();
    }
}

function addWelcomeMessage() {
    const welcome = `**Welcome to Tacit!** I'm your personal work twin.

I can help you:

- **Capture and query** your decisions, context, and plans
- **Search** through your uploaded documents
- **Coach you** through challenges and decisions

Ask me anything, log context, or upload documents to get started!`;

    addMessage('assistant', welcome);
}

// ==================== CONTEXT ====================

async function submitContext(e) {
    e.preventDefault();

    const form = e.target;
    const submitBtn = form.querySelector('button[type="submit"]');
    const originalText = submitBtn.textContent;

    submitBtn.textContent = 'Saving...';
    submitBtn.disabled = true;

    const contextData = {
        type: document.getElementById('contextType').value,
        title: document.getElementById('contextTitle').value,
        content: document.getElementById('contextContent').value,
        tags: document.getElementById('contextTags').value
            .split(',')
            .map(t => t.trim())
            .filter(t => t)
    };

    try {
        const response = await fetch(`${API_BASE}/context`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(contextData)
        });

        if (response.ok) {
            showSuccess('Context saved successfully!');
            form.reset();
            addMessage('assistant', `Great! I've captured your ${contextData.type.replace('_', ' ')} about "${contextData.title}". I can now reference this in our conversations.`);
        } else {
            throw new Error('Failed to save context');
        }

    } catch (error) {
        showError('Failed to save context. Please try again.');
        console.error('Context error:', error);
    } finally {
        submitBtn.textContent = originalText;
        submitBtn.disabled = false;
    }
}

// ==================== DOCUMENT UPLOAD ====================

function handleFileSelect(e) {
    const file = e.target.files[0];
    if (file) {
        document.getElementById('fileName').textContent = file.name;
        document.getElementById('uploadBtn').disabled = false;
    }
}

async function uploadDocument() {
    const fileInput = document.getElementById('fileInput');
    const file = fileInput.files[0];

    if (!file) return;

    const uploadBtn = document.getElementById('uploadBtn');
    uploadBtn.textContent = 'Uploading...';
    uploadBtn.disabled = true;

    const formData = new FormData();
    formData.append('file', file);

    try {
        const response = await fetch(`${API_BASE}/documents/upload`, {
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            const data = await response.json();
            showSuccess('Document uploaded successfully!');
            fileInput.value = '';
            document.getElementById('fileName').textContent = '';
            addMessage('assistant', `Perfect! I've processed "${file.name}". I can now answer questions about this document.`);
        } else {
            throw new Error('Upload failed');
        }

    } catch (error) {
        showError('Failed to upload document. Please try again.');
        console.error('Upload error:', error);
    } finally {
        uploadBtn.textContent = 'Upload';
        uploadBtn.disabled = true;
    }
}

// ==================== STATS ====================

async function showStats() {
    const modal = document.getElementById('statsModal');
    const content = document.getElementById('statsContent');

    content.innerHTML = '<div class="loading"><div class="loading-spinner"><div class="spinner"></div><span>Loading stats...</span></div></div>';
    modal.classList.add('active');

    try {
        // Get health stats
        const healthRes = await fetch(`${API_BASE}/health`);
        const healthData = await healthRes.json();

        // Get document stats
        const docsRes = await fetch(`${API_BASE}/documents/stats/summary`);
        const docsData = await docsRes.json();

        // Get contexts count
        const contextsRes = await fetch(`${API_BASE}/context?limit=1000`);
        const contextsData = await contextsRes.json();

        const html = `
            <div class="stat-row">
                <span class="stat-label">Status</span>
                <span class="stat-value">${healthData.status}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">AI Model</span>
                <span class="stat-value">${healthData.model}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Contexts Captured</span>
                <span class="stat-value">${contextsData.length}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Documents Uploaded</span>
                <span class="stat-value">${docsData.total_documents}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Total Words</span>
                <span class="stat-value">${docsData.total_words.toLocaleString()}</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Storage Used</span>
                <span class="stat-value">${docsData.total_size_mb} MB</span>
            </div>
            <div class="stat-row">
                <span class="stat-label">Vector DB Items</span>
                <span class="stat-value">${healthData.stats.vector_db.contexts_count + healthData.stats.vector_db.document_chunks_count}</span>
            </div>
        `;

        content.innerHTML = html;

    } catch (error) {
        content.innerHTML = '<div class="error-message">Failed to load stats</div>';
        console.error('Stats error:', error);
    }
}

// ==================== CONTEXT MANAGEMENT ====================

async function showContexts() {
    const modal = document.getElementById('contextsModal');
    const content = document.getElementById('contextsContent');

    content.innerHTML = '<div class="loading"><div class="loading-spinner"><div class="spinner"></div><span>Loading contexts...</span></div></div>';
    modal.classList.add('active');

    try {
        const response = await fetch(`${API_BASE}/context?limit=1000`);
        const contexts = await response.json();

        if (contexts.length === 0) {
            content.innerHTML = '<div class="empty-state">No contexts captured yet. Use Quick Capture to add your first context!</div>';
            return;
        }

        const html = contexts.map(ctx => {
            const date = new Date(ctx.created_at).toLocaleDateString();
            const typeLabel = ctx.type.replace('_', ' ').replace(/\b\w/g, l => l.toUpperCase());
            const preview = ctx.content.length > 150 ? ctx.content.substring(0, 150) + '...' : ctx.content;
            const tags = ctx.tags && ctx.tags.length > 0
                ? ctx.tags.map(t => `<span class="tag">${t}</span>`).join('')
                : '';

            return `
                <div class="context-card">
                    <div class="context-header">
                        <div>
                            <div class="context-type">${typeLabel}</div>
                            <div class="context-title">${ctx.title}</div>
                        </div>
                        <div class="context-actions">
                            <button class="edit-btn" data-ctx-id="${ctx.id}">Edit</button>
                            <button class="delete-btn" data-ctx-id="${ctx.id}" data-ctx-title="${ctx.title}">Delete</button>
                        </div>
                    </div>
                    <div class="context-content">${preview}</div>
                    <div class="context-footer">
                        ${tags ? `<div class="context-tags">${tags}</div>` : '<div></div>'}
                        <div class="context-date">${date}</div>
                    </div>
                </div>
            `;
        }).join('');

        content.innerHTML = html;

        // Add event listeners to buttons
        content.querySelectorAll('.edit-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                editContext(btn.getAttribute('data-ctx-id'));
            });
        });

        content.querySelectorAll('.delete-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const ctxId = btn.getAttribute('data-ctx-id');
                const ctxTitle = btn.getAttribute('data-ctx-title');
                deleteContext(ctxId, ctxTitle);
            });
        });

    } catch (error) {
        content.innerHTML = '<div class="error-message">Failed to load contexts</div>';
        console.error('Context loading error:', error);
    }
}

async function editContext(contextId) {
    try {
        const response = await fetch(`${API_BASE}/context/${contextId}`);
        const context = await response.json();

        document.getElementById('editContextId').value = context.id;
        document.getElementById('editContextType').value = context.type;
        document.getElementById('editContextTitle').value = context.title;
        document.getElementById('editContextContent').value = context.content;
        document.getElementById('editContextTags').value = (context.tags || []).join(', ');

        closeModal('contextsModal');
        document.getElementById('editContextModal').classList.add('active');

    } catch (error) {
        showError('Failed to load context for editing');
        console.error('Edit context error:', error);
    }
}

async function updateContext(e) {
    e.preventDefault();

    const contextId = document.getElementById('editContextId').value;
    const submitBtn = e.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.textContent;

    submitBtn.textContent = 'Saving...';
    submitBtn.disabled = true;

    const updateData = {
        type: document.getElementById('editContextType').value,
        title: document.getElementById('editContextTitle').value,
        content: document.getElementById('editContextContent').value,
        tags: document.getElementById('editContextTags').value
            .split(',')
            .map(t => t.trim())
            .filter(t => t)
    };

    try {
        const response = await fetch(`${API_BASE}/context/${contextId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(updateData)
        });

        if (response.ok) {
            showSuccess('Context updated successfully!');
            closeModal('editContextModal');
            // Refresh the contexts list if it's open
            const contextsModal = document.getElementById('contextsModal');
            if (contextsModal.classList.contains('active')) {
                showContexts();
            }
        } else {
            throw new Error('Failed to update context');
        }

    } catch (error) {
        showError('Failed to update context. Please try again.');
        console.error('Update context error:', error);
    } finally {
        submitBtn.textContent = originalText;
        submitBtn.disabled = false;
    }
}

async function deleteContext(contextId, contextTitle) {
    console.log('Delete context called for:', contextId, contextTitle);

    if (!confirm(`Are you sure you want to delete "${contextTitle}"? This cannot be undone.`)) {
        console.log('Delete cancelled by user');
        return;
    }

    console.log('Sending DELETE request to:', `${API_BASE}/context/${contextId}`);

    try {
        const response = await fetch(`${API_BASE}/context/${contextId}`, {
            method: 'DELETE'
        });

        console.log('Delete response status:', response.status);
        const data = await response.json();
        console.log('Delete response data:', data);

        if (response.ok) {
            alert('Context deleted successfully');
            showContexts(); // Refresh the list
        } else {
            alert('Failed to delete context: ' + (data.detail || 'Unknown error'));
            throw new Error('Failed to delete context');
        }

    } catch (error) {
        alert('Error deleting context: ' + error.message);
        console.error('Delete context error:', error);
    }
}

// ==================== DOCUMENT MANAGEMENT ====================

async function showDocuments() {
    const modal = document.getElementById('docsModal');
    const content = document.getElementById('docsContent');

    content.innerHTML = '<div class="loading"><div class="loading-spinner"><div class="spinner"></div><span>Loading documents...</span></div></div>';
    modal.classList.add('active');

    try {
        const response = await fetch(`${API_BASE}/documents?limit=1000`);
        const documents = await response.json();

        if (documents.length === 0) {
            content.innerHTML = '<div class="empty-state">No documents uploaded yet. Use the Upload tab to add your first document!</div>';
            return;
        }

        const html = documents.map(doc => {
            const date = new Date(doc.upload_date).toLocaleDateString();
            const sizeMB = (doc.size_bytes / (1024 * 1024)).toFixed(2);
            const typeIcon = getDocumentIcon(doc.type);

            return `
                <div class="doc-card">
                    <div class="doc-header">
                        <div>
                            <div class="doc-icon">${typeIcon}</div>
                            <div class="doc-title">${doc.original_filename}</div>
                            <div class="doc-type">${doc.type.toUpperCase()}</div>
                        </div>
                        <div class="doc-actions">
                            <button class="delete-btn" data-doc-id="${doc.id}" data-doc-name="${doc.original_filename}">Delete</button>
                        </div>
                    </div>
                    <div class="doc-footer">
                        <div class="doc-meta">
                            <span>📅 ${date}</span>
                            <span>💾 ${sizeMB} MB</span>
                            ${doc.page_count ? `<span>📄 ${doc.page_count} pages</span>` : ''}
                            ${doc.word_count ? `<span>📝 ${doc.word_count.toLocaleString()} words</span>` : ''}
                        </div>
                    </div>
                </div>
            `;
        }).join('');

        content.innerHTML = html;

        // Add event listeners to delete buttons
        content.querySelectorAll('.delete-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const docId = btn.getAttribute('data-doc-id');
                const docName = btn.getAttribute('data-doc-name');
                deleteDocument(docId, docName);
            });
        });

    } catch (error) {
        content.innerHTML = '<div class="error-message">Failed to load documents</div>';
        console.error('Document loading error:', error);
    }
}

async function deleteDocument(documentId, filename) {
    console.log('Delete called for:', documentId, filename);

    if (!confirm(`Are you sure you want to delete "${filename}"? This cannot be undone.`)) {
        console.log('Delete cancelled by user');
        return;
    }

    console.log('Sending DELETE request to:', `${API_BASE}/documents/${documentId}`);

    try {
        const response = await fetch(`${API_BASE}/documents/${documentId}`, {
            method: 'DELETE'
        });

        console.log('Delete response status:', response.status);
        const data = await response.json();
        console.log('Delete response data:', data);

        if (response.ok) {
            alert('Document deleted successfully');
            showDocuments(); // Refresh the list
        } else {
            alert('Failed to delete document: ' + (data.detail || 'Unknown error'));
            throw new Error('Failed to delete document');
        }

    } catch (error) {
        alert('Error deleting document: ' + error.message);
        console.error('Delete document error:', error);
    }
}

function getDocumentIcon(type) {
    const icons = {
        'pdf': '📕',
        'docx': '📘',
        'txt': '📄',
        'md': '📝'
    };
    return icons[type] || '📄';
}

// ==================== HELPERS ====================

function switchTab(tabName) {
    // Update buttons
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Update content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.remove('active');
    });

    const targetTab = tabName === 'quick' ? 'quickTab' : 'uploadTab';
    document.getElementById(targetTab).classList.add('active');
}

function showSuccess(message) {
    const messagesContainer = document.getElementById('messages');
    const successDiv = document.createElement('div');
    successDiv.className = 'success-message';
    successDiv.textContent = message;
    messagesContainer.appendChild(successDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    setTimeout(() => successDiv.remove(), 3000);
}

function showError(message) {
    const messagesContainer = document.getElementById('messages');
    const errorDiv = document.createElement('div');
    errorDiv.className = 'error-message';
    errorDiv.textContent = message;
    messagesContainer.appendChild(errorDiv);
    messagesContainer.scrollTop = messagesContainer.scrollHeight;

    setTimeout(() => errorDiv.remove(), 5000);
}

function generateSessionId() {
    return `session-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.remove('active');
}
