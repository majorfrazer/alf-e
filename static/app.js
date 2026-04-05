'use strict';

// ── State ─────────────────────────────────────────────────────────────────────

const state = {
    userId:           localStorage.getItem('alfe_user') || 'fraser',
    conversationId:   null,
    messages:         [],   // { role, content, streaming?, tool? }
    pendingApprovals: [],
    conversations:    [],   // { id, preview, timestamp }
    sensorBarVisible: false,
    sending:          false,
};

// ── DOM ───────────────────────────────────────────────────────────────────────

const el = id => document.getElementById(id);

// ── Utilities ─────────────────────────────────────────────────────────────────

function escHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function generateId() {
    return Math.random().toString(36).slice(2, 10);
}

/** Minimal markdown → HTML converter for AI chat responses. */
function renderMarkdown(raw) {
    // Split on fenced code blocks so we can escape them separately
    const parts = raw.split(/(```[\s\S]*?```)/g);

    return parts.map((part, idx) => {
        if (idx % 2 === 1) {
            // Fenced code block
            const match = part.match(/^```(\w*)\n?([\s\S]*?)```$/s);
            const lang = match ? escHtml(match[1]) : '';
            const code = match ? escHtml(match[2].replace(/\n$/, '')) : escHtml(part.slice(3, -3));
            return `<pre><code${lang ? ` class="lang-${lang}"` : ''}>${code}</code></pre>`;
        }

        // Inline content — escape first, then apply patterns
        return escHtml(part)
            // Inline code (escaped backticks become &#96; — match on escaped version)
            .replace(/`([^`\n]+)`/g, '<code>$1</code>')
            // Bold + italic combinations
            .replace(/\*\*\*(.+?)\*\*\*/gs, '<strong><em>$1</em></strong>')
            .replace(/\*\*(.+?)\*\*/gs,     '<strong>$1</strong>')
            .replace(/\*(.+?)\*/gs,          '<em>$1</em>')
            // Headers (only at line start)
            .replace(/^### (.+)$/gm, '<h3>$1</h3>')
            .replace(/^## (.+)$/gm,  '<h2>$1</h2>')
            .replace(/^# (.+)$/gm,   '<h1>$1</h1>')
            // Blockquote
            .replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>')
            // Unordered list items — collect runs into <ul>
            .replace(/^[*\-] (.+)$/gm, '<li>$1</li>')
            .replace(/(<li>[\s\S]*?<\/li>)/g, m => `<ul>${m}</ul>`)
            // Ordered list items
            .replace(/^\d+\. (.+)$/gm, '<li>$1</li>')
            // Paragraphs
            .replace(/\n\n+/g, '</p><p>')
            .replace(/\n/g,    '<br>');
    }).join('');
}

/** Format a raw sensor value for display. */
function fmtSensor(key, val) {
    if (val === null || val === undefined) return '–';
    if (key === 'tesla_soc')          return `${Math.round(val)}%`;
    if (key === 'tesla_charging')     return val ? 'Yes' : 'No';
    if (key.endsWith('_watts')) {
        const w = parseFloat(val);
        return w >= 1000 ? `${(w / 1000).toFixed(1)}kW` : `${Math.round(w)}W`;
    }
    return String(val);
}

const SENSOR_META = {
    solar_watts:          { label: 'Solar',    icon: '☀️' },
    house_watts:          { label: 'House',    icon: '🏠' },
    grid_watts:           { label: 'Grid',     icon: '⚡' },
    tesla_soc:            { label: 'Tesla',    icon: '🔋' },
    tesla_charging:       { label: 'Charging', icon: '🔌' },
    tesla_charger_power:  { label: 'Charger',  icon: '🔌' },
};

// ── Rendering ─────────────────────────────────────────────────────────────────

function renderMessages() {
    const container = el('messages');

    if (state.messages.length === 0) {
        container.innerHTML = `
            <div class="welcome">
                <div class="welcome-logo">ALF-E</div>
                <div class="welcome-tagline">Your personal AI agent</div>
                <div class="welcome-hint">Ask about your home, energy, Tesla, or anything else.</div>
            </div>`;
        return;
    }

    const html = state.messages.map((msg, i) => buildMessageHtml(msg, i)).join('');
    const approvalsHtml = state.pendingApprovals.map((a, i) => buildApprovalHtml(a, i)).join('');

    container.innerHTML = html + approvalsHtml;
    scrollToBottom();
}

function buildMessageHtml(msg, index) {
    const isUser = msg.role === 'user';
    const label  = isUser ? escHtml(state.userId) : 'Alf-E';

    // Typing indicator for empty streaming message
    if (!isUser && msg.streaming && msg.content === '') {
        const toolHtml = msg.tool
            ? `<div class="tool-indicator"><div class="tool-spinner"></div> calling ${escHtml(msg.tool)}…</div>`
            : '';
        return `
            <div class="message assistant" data-idx="${index}">
                <div class="message-role">${label}</div>
                ${toolHtml}
                <div class="typing-bubble">
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                    <div class="typing-dot"></div>
                </div>
            </div>`;
    }

    const toolHtml = (!isUser && msg.tool && msg.streaming)
        ? `<div class="tool-indicator"><div class="tool-spinner"></div> calling ${escHtml(msg.tool)}…</div>`
        : '';

    const contentHtml = isUser
        ? `<p>${escHtml(msg.content).replace(/\n/g, '<br>')}</p>`
        : `<p>${renderMarkdown(msg.content)}</p>`;

    return `
        <div class="message ${msg.role}" data-idx="${index}">
            <div class="message-role">${label}</div>
            ${toolHtml}
            <div class="message-bubble">${contentHtml}</div>
        </div>`;
}

function buildApprovalHtml(a, index) {
    const dataRows = (a.data && Object.keys(a.data).length)
        ? `<div class="approval-row"><strong>Data</strong>${escHtml(JSON.stringify(a.data))}</div>`
        : '';
    return `
        <div class="approval-card" id="approval-${index}">
            <div class="approval-title">⚙️ Action Required</div>
            <div class="approval-row"><strong>Service</strong>${escHtml(a.domain)}.${escHtml(a.service)}</div>
            <div class="approval-row"><strong>Entity</strong>${escHtml(a.entity_id)}</div>
            ${dataRows}
            <div class="approval-actions">
                <button class="btn-reject"  onclick="handleApproval(${index}, false)">Reject</button>
                <button class="btn-approve" onclick="handleApproval(${index}, true)">Approve ✓</button>
            </div>
        </div>`;
}

/** Re-render only the last message element (used during streaming). */
function updateLastMessage() {
    const idx = state.messages.length - 1;
    const msg = state.messages[idx];
    if (!msg) return;

    const existing = el('messages').querySelector(`[data-idx="${idx}"]`);
    const newHtml   = buildMessageHtml(msg, idx);

    if (existing) {
        existing.outerHTML = newHtml;
    } else {
        el('messages').insertAdjacentHTML('beforeend', newHtml);
    }
    scrollToBottom();
}

function renderSensors(sensors, connected) {
    const dot = el('status-dot');
    dot.className = 'status-dot ' + (connected ? 'connected' : 'disconnected');
    dot.title     = connected ? 'HA Connected' : 'HA Offline';

    const grid = el('sensor-grid');

    if (!connected || !sensors || Object.keys(sensors).length === 0) {
        grid.innerHTML = `<span style="font-size:13px;color:var(--text-dim)">Home Assistant not connected</span>`;
        return;
    }

    grid.innerHTML = Object.entries(sensors).map(([key, val]) => {
        const meta    = SENSOR_META[key] || { label: key, icon: '📊' };
        const display = fmtSensor(key, val);
        return `
            <div class="sensor-card${val === null ? ' stale' : ''}">
                <div class="sensor-icon">${meta.icon}</div>
                <div class="sensor-label">${escHtml(meta.label)}</div>
                <div class="sensor-value">${escHtml(display)}</div>
            </div>`;
    }).join('');
}

function renderConversationList() {
    const list = el('conversation-list');
    if (state.conversations.length === 0) {
        list.innerHTML = `<div style="padding:12px;color:var(--text-dim);font-size:12px">No conversations yet</div>`;
        return;
    }
    list.innerHTML = state.conversations.map(c => `
        <div class="conv-item ${c.id === state.conversationId ? 'active' : ''}"
             onclick="loadConversation('${escHtml(c.id)}')"
             title="${escHtml(c.preview)}">
            ${escHtml(c.preview)}
        </div>`).join('');
}

function scrollToBottom() {
    requestAnimationFrame(() => {
        const m = el('messages');
        m.scrollTop = m.scrollHeight;
    });
}

// ── API ───────────────────────────────────────────────────────────────────────

async function sendMessage(text) {
    if (state.sending || !text.trim()) return;
    state.sending = true;
    el('send-btn').disabled = true;

    const convId = state.conversationId || generateId();

    state.messages.push({ role: 'user', content: text.trim() });

    const assistantMsg = { role: 'assistant', content: '', streaming: true, tool: null };
    state.messages.push(assistantMsg);
    renderMessages();

    try {
        const response = await fetch('api/chat/stream', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({
                message:         text.trim(),
                user_id:         state.userId,
                conversation_id: convId,
            }),
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const reader  = response.body.getReader();
        const decoder = new TextDecoder();
        let   buffer  = '';

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep the incomplete trailing line

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                let data;
                try { data = JSON.parse(line.slice(6)); } catch { continue; }

                if (data.type === 'token') {
                    assistantMsg.content += data.content;
                    updateLastMessage();

                } else if (data.type === 'tool') {
                    assistantMsg.tool = data.content;
                    if (!assistantMsg.content) updateLastMessage(); // show spinner

                } else if (data.type === 'done') {
                    assistantMsg.streaming      = false;
                    assistantMsg.tool           = null;
                    state.conversationId        = data.conversation_id || convId;
                    state.pendingApprovals      = data.pending_approvals || [];
                    saveConvPreview(state.conversationId, text.trim());
                    saveMessages(state.conversationId);
                    renderMessages();

                } else if (data.type === 'error') {
                    assistantMsg.content  = `Error: ${escHtml(data.content)}`;
                    assistantMsg.streaming = false;
                    renderMessages();
                }
            }
        }

    } catch (err) {
        assistantMsg.content   = 'Connection error — is the server running?';
        assistantMsg.streaming = false;
        renderMessages();
        console.error('[Alf-E] sendMessage error:', err);
    }

    state.sending = false;
    el('send-btn').disabled = false;
    el('message-input').focus();
}

async function handleApproval(index, approved) {
    const card = el(`approval-${index}`);
    if (card) { card.style.opacity = '0.5'; card.style.pointerEvents = 'none'; }

    try {
        const res  = await fetch('api/approve', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ index, approved, user_id: state.userId }),
        });
        const data = await res.json();

        state.pendingApprovals.splice(index, 1);

        let resultText;
        if (approved) {
            resultText = data.success !== false ? '✓ Done.' : '✗ Action failed — check HA logs.';
        } else {
            resultText = '✗ Action rejected.';
        }
        state.messages.push({ role: 'assistant', content: resultText });
        saveMessages(state.conversationId);
        renderMessages();

    } catch (err) {
        if (card) { card.style.opacity = '1'; card.style.pointerEvents = 'auto'; }
        console.error('[Alf-E] approval error:', err);
    }
}

async function loadSensors() {
    try {
        const res  = await fetch('api/sensors');
        const data = await res.json();
        renderSensors(data.sensors || {}, data.connected || false);
    } catch {
        renderSensors({}, false);
    }
}

async function loadStatus() {
    try {
        const res  = await fetch('api/status');
        const data = await res.json();
        const dot  = el('status-dot');
        dot.className = 'status-dot ' + (data.ha_connected ? 'connected' : 'disconnected');
        dot.title     = data.ha_connected ? `HA Connected — ${data.name} v${data.version}` : 'HA Offline';
    } catch {
        el('status-dot').className = 'status-dot disconnected';
    }
}

// ── Conversations ─────────────────────────────────────────────────────────────

function saveConvPreview(id, firstMessage) {
    const preview = firstMessage.slice(0, 45) + (firstMessage.length > 45 ? '…' : '');
    const existing = state.conversations.findIndex(c => c.id === id);
    const entry = { id, preview, timestamp: Date.now() };
    if (existing >= 0) {
        state.conversations[existing] = entry;
    } else {
        state.conversations.unshift(entry);
        if (state.conversations.length > 30) state.conversations.pop();
    }
    try { localStorage.setItem('alfe_conversations', JSON.stringify(state.conversations)); } catch {}
    renderConversationList();
}

function saveMessages(convId) {
    if (!convId) return;
    try {
        localStorage.setItem(
            `alfe_msgs_${convId}`,
            JSON.stringify(state.messages.slice(-60))
        );
    } catch {}
}

function loadStoredMessages(convId) {
    try {
        const raw = localStorage.getItem(`alfe_msgs_${convId}`);
        return raw ? JSON.parse(raw) : [];
    } catch { return []; }
}

// Exposed globally for inline onclick handlers
window.loadConversation = function (id) {
    state.conversationId   = id;
    state.messages         = loadStoredMessages(id);
    state.pendingApprovals = [];
    renderMessages();
    renderConversationList();
    closeSidebar();
};

function startNewChat() {
    state.conversationId   = null;
    state.messages         = [];
    state.pendingApprovals = [];
    renderMessages();
    renderConversationList();
    closeSidebar();
    el('message-input').focus();
}

// ── UI Controls ───────────────────────────────────────────────────────────────

function openSidebar() {
    el('sidebar').classList.add('open');
    el('sidebar-overlay').classList.add('visible');
}

function closeSidebar() {
    el('sidebar').classList.remove('open');
    el('sidebar-overlay').classList.remove('visible');
}

function toggleSidebar() {
    el('sidebar').classList.contains('open') ? closeSidebar() : openSidebar();
}

function toggleSensorBar() {
    state.sensorBarVisible = !state.sensorBarVisible;
    el('sensor-bar').classList.toggle('hidden', !state.sensorBarVisible);
    el('sensors-btn').classList.toggle('active', state.sensorBarVisible);
    if (state.sensorBarVisible) loadSensors();
}

function autoResizeInput() {
    const ta = el('message-input');
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 160) + 'px';

    // Enable send button only when there's content
    el('send-btn').disabled = ta.value.trim() === '' || state.sending;
}

// ── Events ────────────────────────────────────────────────────────────────────

function onSubmit(e) {
    e.preventDefault();
    const text = el('message-input').value.trim();
    if (!text || state.sending) return;
    el('message-input').value = '';
    el('message-input').style.height = 'auto';
    el('send-btn').disabled = true;
    sendMessage(text);
}

function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        onSubmit(e);
    }
}

// ── PWA ───────────────────────────────────────────────────────────────────────

function registerSW() {
    if ('serviceWorker' in navigator) {
        navigator.serviceWorker.register('sw.js')
            .then(() => console.log('[Alf-E] Service worker registered'))
            .catch(err => console.warn('[Alf-E] SW registration failed:', err));
    }
}

// ── Init ──────────────────────────────────────────────────────────────────────

function init() {
    // Restore user
    el('user-selector').value = state.userId;

    // Restore conversations index
    try {
        const raw = localStorage.getItem('alfe_conversations');
        if (raw) state.conversations = JSON.parse(raw);
    } catch {}

    renderConversationList();
    renderMessages();

    // Wire up events
    el('chat-form').addEventListener('submit', onSubmit);
    el('message-input').addEventListener('keydown', onKeyDown);
    el('message-input').addEventListener('input', autoResizeInput);
    el('new-chat-btn').addEventListener('click', startNewChat);
    el('menu-btn').addEventListener('click', toggleSidebar);
    el('sensors-btn').addEventListener('click', toggleSensorBar);
    el('sidebar-overlay').addEventListener('click', closeSidebar);
    el('user-selector').addEventListener('change', e => {
        state.userId = e.target.value;
        localStorage.setItem('alfe_user', state.userId);
        startNewChat();
    });

    // Status + sensor polling
    loadStatus();
    setInterval(() => {
        loadStatus();
        if (state.sensorBarVisible) loadSensors();
    }, 10_000);

    registerSW();

    el('message-input').focus();
}

// Expose approval handler for inline onclick
window.handleApproval = handleApproval;

document.addEventListener('DOMContentLoaded', init);
