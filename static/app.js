'use strict';

// ── State ─────────────────────────────────────────────────────────────────────

const state = {
    userId:             localStorage.getItem('alfe_user') || 'fraser',
    conversationId:     null,
    messages:           [],   // { role, content, streaming?, tool? }
    pendingApprovals:   [],
    conversations:      [],   // { id, preview, timestamp }
    sensorBarVisible:   false,
    insightsVisible:    false,
    insights:           [],
    insightsSeenCount:  parseInt(localStorage.getItem('alfe_insights_seen') || '0'),
    auditVisible:       false,
    sending:            false,
    ttsEnabled:         localStorage.getItem('alfe_tts') === 'true',
    micListening:       false,
    haSites:            [],
    haActiveSite:       'default',
    activeModel:        '',
};

// ── Voice — Speech Recognition (STT) ─────────────────────────────────────────

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;

function initVoice() {
    if (!SpeechRecognition) return; // hide mic on unsupported browsers
    el('mic-btn').classList.remove('hidden');

    recognition = new SpeechRecognition();
    recognition.continuous    = false;
    recognition.interimResults = true;
    recognition.lang          = 'en-AU';

    recognition.onstart = () => {
        state.micListening = true;
        el('mic-btn').classList.add('active');
        el('message-input').placeholder = 'Listening…';
    };

    recognition.onresult = (e) => {
        const transcript = Array.from(e.results)
            .map(r => r[0].transcript).join('');
        el('message-input').value = transcript;
        autoResizeInput();
        el('send-btn').disabled = transcript.trim() === '';
    };

    recognition.onend = () => {
        state.micListening = false;
        el('mic-btn').classList.remove('active');
        el('message-input').placeholder = 'Message Alf-E…';
        // Auto-submit if we got something
        const text = el('message-input').value.trim();
        if (text) el('chat-form').requestSubmit();
    };

    recognition.onerror = (e) => {
        state.micListening = false;
        el('mic-btn').classList.remove('active');
        el('message-input').placeholder = 'Message Alf-E…';
        console.warn('[Voice] STT error:', e.error);
    };
}

function toggleMic() {
    if (!recognition) return;
    if (state.micListening) {
        recognition.stop();
    } else {
        el('message-input').value = '';
        recognition.start();
    }
}

// ── Voice — Text-to-Speech (TTS) ─────────────────────────────────────────────

function stripMarkdown(text) {
    return text
        .replace(/```[\s\S]*?```/g, 'code block omitted')
        .replace(/`[^`]+`/g, '')
        .replace(/#{1,6}\s/g, '')
        .replace(/\*\*([^*]+)\*\*/g, '$1')
        .replace(/\*([^*]+)\*/g, '$1')
        .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
        .replace(/^\s*[-*+]\s/gm, '')
        .replace(/^\s*\d+\.\s/gm, '')
        .replace(/\n{2,}/g, '. ')
        .trim();
}

function speakText(text) {
    if (!state.ttsEnabled || !window.speechSynthesis) return;
    window.speechSynthesis.cancel();
    const clean = stripMarkdown(text);
    if (!clean) return;
    const utt = new SpeechSynthesisUtterance(clean.slice(0, 800));
    utt.lang = 'en-AU';
    utt.rate = 1.05;
    window.speechSynthesis.speak(utt);
}

function toggleTTS() {
    state.ttsEnabled = !state.ttsEnabled;
    localStorage.setItem('alfe_tts', state.ttsEnabled);
    el('tts-btn').classList.toggle('active', state.ttsEnabled);
    if (!state.ttsEnabled) window.speechSynthesis?.cancel();
}

// ── Auth ──────────────────────────────────────────────────────────────────────

function getToken() { return localStorage.getItem('alfe_token') || ''; }

function apiFetch(url, options = {}) {
    const token = getToken();
    const headers = { ...(options.headers || {}) };
    if (token) headers['Authorization'] = `Bearer ${token}`;
    return fetch(url, { ...options, headers });
}

function showLogin() {
    document.body.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:center;height:100vh;background:#0f0f0f;">
            <div style="background:#1a1a1a;border:1px solid #333;border-radius:12px;padding:40px;width:320px;text-align:center;">
                <div style="font-size:28px;margin-bottom:8px;">⚡</div>
                <div style="font-size:20px;font-weight:700;color:#fff;margin-bottom:4px;">Alf-E</div>
                <div style="font-size:13px;color:#888;margin-bottom:28px;">Self-hosted AI Agent</div>
                <input id="token-input" type="password" placeholder="API Token"
                    style="width:100%;box-sizing:border-box;padding:10px 14px;border-radius:8px;
                           border:1px solid #444;background:#111;color:#fff;font-size:14px;margin-bottom:12px;" />
                <button id="token-submit"
                    style="width:100%;padding:10px;border-radius:8px;border:none;
                           background:#7c3aed;color:#fff;font-size:14px;font-weight:600;cursor:pointer;">
                    Connect
                </button>
                <div id="token-error" style="color:#f87171;font-size:12px;margin-top:10px;"></div>
            </div>
        </div>`;
    const input = document.getElementById('token-input');
    const btn   = document.getElementById('token-submit');
    const err   = document.getElementById('token-error');
    const tryLogin = async () => {
        const t = input.value.trim();
        if (!t) return;
        localStorage.setItem('alfe_token', t);
        const res = await fetch('api/status', { headers: { 'Authorization': `Bearer ${t}` } });
        if (res.ok) { location.reload(); }
        else { err.textContent = 'Invalid token — check and try again.'; localStorage.removeItem('alfe_token'); }
    };
    btn.addEventListener('click', tryLogin);
    input.addEventListener('keydown', e => { if (e.key === 'Enter') tryLogin(); });
    input.focus();
}

async function checkAuth() {
    // Accept ?token= in URL (e.g. iframe embed) — save it and strip from URL
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get('token');
    if (urlToken) {
        localStorage.setItem('alfe_token', urlToken);
        params.delete('token');
        const newUrl = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
        history.replaceState(null, '', newUrl);
    }
    const res = await fetch('api/status', { headers: { 'Authorization': `Bearer ${getToken()}` } });
    if (res.status === 401 || res.status === 403) { showLogin(); return false; }
    return true;
}

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
    const approvalsHtml = state.pendingApprovals.map((a, i) => {
        return a.type === 'code_proposal' ? buildProposalHtml(a, i) : buildApprovalHtml(a, i);
    }).join('');

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

function buildProposalHtml(a, index) {
    const codeId = `proposal-code-${index}`;
    const safeCode = escHtml(a.code || '');
    return `
        <div class="proposal-card" id="approval-${index}">
            <div class="proposal-title">⚡ New Connector Proposal</div>
            <dl class="proposal-meta">
                <dt>ID</dt>    <dd>${escHtml(a.connector_id || '')}</dd>
                <dt>File</dt>  <dd>${escHtml(a.file_path || '')}</dd>
                <dt>Desc</dt>  <dd>${escHtml(a.description || '')}</dd>
            </dl>
            <div class="proposal-warning">
                Review the generated code before approving. Once approved, the file will be written,
                committed to git, and Alf-E will restart to load the new connector.
            </div>
            <button class="proposal-code-toggle" onclick="toggleProposalCode('${codeId}', this)">
                Show generated code ▾
            </button>
            <div class="proposal-code" id="${codeId}">
                <code>${safeCode}</code>
            </div>
            <div class="approval-actions">
                <button class="btn-reject"  onclick="handleApproval(${index}, false)">Reject</button>
                <button class="btn-approve" onclick="handleApproval(${index}, true)">Deploy ✓</button>
            </div>
        </div>`;
}

function toggleProposalCode(codeId, btn) {
    const el = document.getElementById(codeId);
    if (!el) return;
    el.classList.toggle('visible');
    btn.textContent = el.classList.contains('visible') ? 'Hide code ▴' : 'Show generated code ▾';
}
window.toggleProposalCode = toggleProposalCode;

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
             title="${escHtml(c.preview || c.title || c.id)}">
            <span class="conv-title" onclick="loadConversation('${escHtml(c.id)}')">${escHtml(c.title || c.preview || c.id)}</span>
            <button class="conv-delete-btn" onclick="deleteConversation('${escHtml(c.id)}')" title="Delete">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
                </svg>
            </button>
        </div>`).join('');
}

function deleteConversation(id) {
    state.conversations = state.conversations.filter(c => c.id !== id);
    try {
        localStorage.setItem('alfe_conversations', JSON.stringify(state.conversations));
        localStorage.removeItem(`alfe_msgs_${id}`);
    } catch {}
    if (state.conversationId === id) {
        state.conversationId   = null;
        state.messages         = [];
        state.pendingApprovals = [];
        renderMessages();
    }
    renderConversationList();
}
window.deleteConversation = deleteConversation;

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
        const response = await apiFetch('api/chat/stream', {
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

                } else if (data.type === 'clear') {
                    assistantMsg.content = '';
                    assistantMsg.tool = null;
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
                    speakText(assistantMsg.content);
                    if (data.model_used) updateModelBadge(data.model_used);

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

    const isProposal = state.pendingApprovals[index]?.type === 'code_proposal';

    try {
        const res  = await apiFetch('api/approve', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ index, approved, user_id: state.userId }),
        });
        const data = await res.json();

        state.pendingApprovals.splice(index, 1);

        let resultText;
        if (approved) {
            if (isProposal) {
                if (data.status === 'deployed') {
                    resultText = `✓ Connector deployed to \`${data.file}\`. Alf-E is restarting to load it — back in ~30 seconds.`;
                    // Show restart banner
                    showRestartBanner();
                } else {
                    resultText = `✗ Deployment failed: ${data.detail || 'unknown error'}`;
                }
            } else {
                resultText = data.success !== false ? '✓ Done.' : '✗ Action failed — check HA logs.';
            }
        } else {
            resultText = isProposal ? '✗ Connector proposal rejected.' : '✗ Action rejected.';
        }
        state.messages.push({ role: 'assistant', content: resultText });
        saveMessages(state.conversationId);
        renderMessages();

    } catch (err) {
        if (card) { card.style.opacity = '1'; card.style.pointerEvents = 'auto'; }
        console.error('[Alf-E] approval error:', err);
    }
}

function showRestartBanner() {
    // Remove existing banner if any
    const existing = document.getElementById('restart-banner');
    if (existing) existing.remove();

    const banner = document.createElement('div');
    banner.id = 'restart-banner';
    banner.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; z-index: 9999;
        background: #7c3aed; color: #fff; text-align: center;
        padding: 10px 16px; font-size: 13px; font-weight: 600;
    `;
    banner.textContent = '⚡ Deploying connector — Alf-E restarting, please wait…';
    document.body.prepend(banner);

    // Poll until the server is back
    let attempts = 0;
    const poll = setInterval(async () => {
        attempts++;
        try {
            const r = await apiFetch('api/status');
            if (r.ok) {
                clearInterval(poll);
                banner.style.background = '#16a34a';
                banner.textContent = '✓ Alf-E is back online with the new connector loaded.';
                setTimeout(() => banner.remove(), 4000);
            }
        } catch {
            // still restarting
        }
        if (attempts > 60) { clearInterval(poll); banner.remove(); }
    }, 2000);
}

async function loadSensors() {
    try {
        const res  = await apiFetch('api/sensors');
        const data = await res.json();
        renderSensors(data.sensors || {}, data.connected || false);
    } catch {
        renderSensors({}, false);
    }
}

async function loadStatus() {
    try {
        const res  = await apiFetch('api/status');
        const data = await res.json();
        const dot  = el('status-dot');

        // Determine overall connection — HA legacy OR registry connectors
        const connectors    = data.connectors || [];
        const anyConnected  = data.ha_connected || connectors.some(c => c.connected);
        dot.className       = 'status-dot ' + (anyConnected ? 'connected' : 'disconnected');

        // Build tooltip: show each connector's status
        let tip = `${data.name} v${data.version}\n`;
        if (data.ha_connected) tip += '✓ HA (legacy)\n';
        for (const c of connectors) {
            tip += `${c.connected ? '✓' : '✗'} ${c.connector_id} — ${c.description}\n`;
        }
        if (!anyConnected) tip += 'No connectors online';
        dot.title = tip.trim();

        // Store connector list for later use (e.g. status panel)
        state.connectors = connectors;

        // Update model badge if we have a model from status
        if (data.active_model) updateModelBadge(data.active_model);
    } catch {
        el('status-dot').className = 'status-dot disconnected';
    }
}

// ── HA Sites ──────────────────────────────────────────────────────────────────

function abbrevModel(model) {
    if (!model) return '';
    if (model.includes('opus'))   return 'Opus';
    if (model.includes('sonnet')) return 'Sonnet';
    if (model.includes('haiku'))  return 'Haiku';
    if (model.includes('gemini-2.0-flash')) return 'Gemini Flash';
    if (model.includes('gemini')) return 'Gemini';
    if (model.includes('llama'))  return 'Llama';
    return model.split('/').pop().split('-').slice(0,2).join('-');
}

function updateModelBadge(model) {
    state.activeModel = model;
    const badge = el('model-badge');
    if (!badge) return;
    const label = abbrevModel(model);
    badge.textContent = label;
    badge.title = model;
    badge.classList.toggle('hidden', !label);
}

async function loadHaSites() {
    try {
        const res  = await apiFetch('api/ha/sites');
        if (!res.ok) return;
        const data = await res.json();
        state.haSites      = data.sites || [];
        state.haActiveSite = data.active || 'default';
        renderHaSiteSelector();
    } catch { /* server may not have the endpoint yet */ }
}

function renderHaSiteSelector() {
    const sel = el('ha-site-select');
    if (!sel) return;
    sel.innerHTML = state.haSites.map(s => {
        const label = s.owner ? `${s.name} (${s.owner})` : s.name;
        return `<option value="${escHtml(s.name)}" ${s.name === state.haActiveSite ? 'selected' : ''}>${escHtml(label)}</option>`;
    }).join('');
    if (state.haSites.length === 0) {
        sel.innerHTML = '<option value="">No sites</option>';
    }
}

async function onHaSiteChange(name) {
    if (!name || name === state.haActiveSite) return;
    try {
        const res = await apiFetch('api/ha/sites/switch', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ name }),
        });
        if (res.ok) {
            state.haActiveSite = name;
        } else {
            const err = await res.json().catch(() => ({ detail: 'Switch failed' }));
            alert(`Could not switch to ${name}: ${err.detail || 'Unknown error'}`);
            renderHaSiteSelector(); // revert selector
        }
    } catch (e) {
        console.error('[Alf-E] ha site switch error:', e);
    }
}

function openAddSiteModal() {
    ['site-name','site-owner','site-url','site-token','site-notes'].forEach(id => {
        const inp = el(id);
        if (inp) inp.value = '';
    });
    const errEl = el('add-site-error');
    if (errEl) errEl.classList.add('hidden');
    el('add-site-modal').classList.remove('hidden');
    const first = el('site-name');
    if (first) setTimeout(() => first.focus(), 50);
}
window.openAddSiteModal = openAddSiteModal;

function closeAddSiteModal() {
    el('add-site-modal').classList.add('hidden');
}
window.closeAddSiteModal = closeAddSiteModal;

function handleModalOverlayClick(e) {
    if (e.target === e.currentTarget) closeAddSiteModal();
}
window.handleModalOverlayClick = handleModalOverlayClick;

async function submitAddSite() {
    const name   = (el('site-name')?.value  || '').trim();
    const owner  = (el('site-owner')?.value || '').trim();
    const url    = (el('site-url')?.value   || '').trim();
    const token  = (el('site-token')?.value || '').trim();
    const notes  = (el('site-notes')?.value || '').trim();

    const errEl  = el('add-site-error');
    const setErr = (msg) => {
        if (errEl) { errEl.textContent = msg; errEl.classList.remove('hidden'); }
    };

    if (!name || !url || !token) { setErr('Name, URL, and token are required.'); return; }
    if (!url.startsWith('http')) { setErr('URL must start with http:// or https://'); return; }

    const btn = el('add-site-submit');
    if (btn) { btn.disabled = true; btn.textContent = 'Adding…'; }

    try {
        const res = await apiFetch('api/ha/sites', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body:    JSON.stringify({ name, owner, url, token, notes }),
        });
        const data = await res.json();
        if (!res.ok) {
            setErr(data.detail || 'Failed to add site');
            return;
        }
        closeAddSiteModal();
        await loadHaSites();
        // Auto-switch to the new site
        await onHaSiteChange(data.name);
        renderHaSiteSelector();
    } catch (e) {
        setErr('Network error — is Alf-E running?');
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Add Site'; }
    }
}
window.submitAddSite = submitAddSite;

// ── Conversations ─────────────────────────────────────────────────────────────

function generateTitle(userMsg, assistantMsg) {
    // Try to create a meaningful title from the user's first message
    let title = userMsg.trim();

    // Strip common prefixes
    title = title.replace(/^(hey |hi |please |can you |could you |what's |what is |how |tell me )/i, '');

    // Capitalize first letter
    title = title.charAt(0).toUpperCase() + title.slice(1);

    // Truncate smartly at word boundary
    if (title.length > 40) {
        title = title.slice(0, 40).replace(/\s+\S*$/, '') + '…';
    }

    return title || 'New chat';
}

function saveConvPreview(id, firstMessage) {
    const existing = state.conversations.findIndex(c => c.id === id);

    // If this conversation already has a title, keep it
    if (existing >= 0 && state.conversations[existing].title) {
        state.conversations[existing].timestamp = Date.now();
        try { localStorage.setItem('alfe_conversations', JSON.stringify(state.conversations)); } catch {}
        renderConversationList();
        return;
    }

    const title = generateTitle(firstMessage, '');
    const entry = { id, title, preview: firstMessage.slice(0, 60), timestamp: Date.now() };

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

// ── Insights ──────────────────────────────────────────────────────────────────

function toggleInsightsDrawer() {
    state.insightsVisible = !state.insightsVisible;
    el('insights-drawer').classList.toggle('hidden', !state.insightsVisible);
    el('insights-btn').classList.toggle('active', state.insightsVisible);

    if (state.insightsVisible) {
        // Mark all current insights as seen
        state.insightsSeenCount = state.insights.length;
        try { localStorage.setItem('alfe_insights_seen', String(state.insightsSeenCount)); } catch {}
        el('insights-badge').classList.add('hidden');
    }
}
window.toggleInsightsDrawer = toggleInsightsDrawer;

function renderInsights() {
    const list = el('insights-list');
    if (!list) return;

    if (state.insights.length === 0) {
        list.innerHTML = '<div class="insights-empty">No insights yet — Alf-E checks every 15 minutes.</div>';
        return;
    }

    list.innerHTML = state.insights.map(insight => {
        const priority = escHtml(insight.priority || 'low');
        const title    = escHtml(insight.title || 'Insight');
        const detail   = escHtml(insight.detail || '');
        const action   = insight.action ? `<div class="insight-action">→ ${escHtml(insight.action)}</div>` : '';
        const ts       = insight.timestamp ? new Date(insight.timestamp).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'}) : '';
        return `
            <div class="insight-card">
                <div class="insight-meta">
                    <span class="insight-priority ${priority}">${priority}</span>
                    <span class="insight-title">${title}</span>
                    <span class="insight-time">${ts}</span>
                </div>
                <div class="insight-detail">${detail}</div>
                ${action}
            </div>`;
    }).join('');
}

// ── Audit Log ─────────────────────────────────────────────────────────────

function toggleAuditDrawer() {
    state.auditVisible = !state.auditVisible;
    el('audit-drawer').classList.toggle('hidden', !state.auditVisible);
    el('audit-btn').classList.toggle('active', state.auditVisible);
}
window.toggleAuditDrawer = toggleAuditDrawer;

async function loadAudit() {
    try {
        const res  = await apiFetch('api/audit?limit=50');
        if (!res.ok) return;
        const data = await res.json();
        const entries = data.entries || [];
        const list = el('audit-list');
        if (!list) return;

        if (entries.length === 0) {
            list.innerHTML = '<div class="insights-empty">No tool calls yet.</div>';
            return;
        }

        list.innerHTML = entries.map(e => {
            const action  = escHtml(e.action || '');
            const result  = escHtml(e.result || '');
            const details = escHtml((e.details || '').slice(0, 200));
            const target  = escHtml((e.target || '').slice(0, 80));
            const ts      = e.timestamp ? new Date(e.timestamp).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '';
            const badgeClass = result === 'ok' ? 'low' : (result === 'error' ? 'high' : 'medium');
            return `
                <div class="insight-card">
                    <div class="insight-meta">
                        <span class="insight-priority ${badgeClass}">${result}</span>
                        <span class="insight-title">${action}</span>
                        <span class="insight-time">${ts}</span>
                    </div>
                    ${target ? `<div class="insight-detail" style="font-family:monospace;font-size:11px;opacity:0.7">${target}</div>` : ''}
                    ${details ? `<div class="insight-detail">${details}</div>` : ''}
                </div>`;
        }).join('');
    } catch (e) {
        // fail quietly
    }
}

async function loadInsights() {
    try {
        const res  = await apiFetch('api/insights?limit=20');
        if (!res.ok) return;
        const data = await res.json();
        const fresh = data.insights || [];

        // Count unseen (new since last open)
        const unseen = fresh.length - state.insightsSeenCount;
        state.insights = fresh;

        // Update badge
        const badge = el('insights-badge');
        if (badge) {
            if (unseen > 0 && !state.insightsVisible) {
                badge.textContent = unseen > 9 ? '9+' : String(unseen);
                badge.classList.remove('hidden');
            } else if (state.insightsVisible || unseen <= 0) {
                badge.classList.add('hidden');
            }
        }

        if (state.insightsVisible) renderInsights();

    } catch { /* server not ready yet */ }
}

// ── Update Check ──────────────────────────────────────────────────────────────

async function checkForUpdate() {
    try {
        const res  = await apiFetch('api/update/check');
        if (!res.ok) return;
        const data = await res.json();
        if (!data.update_available) return;

        // Don't show twice
        if (document.getElementById('update-banner')) return;

        const banner = document.createElement('div');
        banner.id = 'update-banner';
        banner.className = 'update-banner';
        const msg = data.is_addon
            ? `Alf-E ${data.latest} is available — update in HA Add-ons.`
            : `Alf-E ${data.latest} is available — Watchtower will apply it tonight, or restart your container.`;
        banner.innerHTML = `
            <span>⬆️ ${msg}</span>
            <button onclick="this.parentElement.remove()" title="Dismiss">✕</button>`;
        document.getElementById('main').prepend(banner);
    } catch {}
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
    el('insights-btn').addEventListener('click', () => {
        toggleInsightsDrawer();
        if (state.insightsVisible) renderInsights();
    });
    el('audit-btn').addEventListener('click', () => {
        toggleAuditDrawer();
        if (state.auditVisible) loadAudit();
    });
    el('mic-btn').addEventListener('click', toggleMic);
    el('tts-btn').addEventListener('click', toggleTTS);
    el('add-site-btn').addEventListener('click', openAddSiteModal);
    el('ha-site-select').addEventListener('change', e => onHaSiteChange(e.target.value));

    // Voice setup
    initVoice();
    el('tts-btn').classList.toggle('active', state.ttsEnabled);

    // Status + sensor + insights polling
    loadStatus();
    loadInsights();
    loadHaSites();
    setInterval(() => {
        loadStatus();
        if (state.sensorBarVisible) loadSensors();
        if (state.auditVisible)     loadAudit();
    }, 10_000);
    // Check for new insights every 3 minutes (engine runs every 15)
    setInterval(loadInsights, 3 * 60 * 1000);

    registerSW();

    // Check for updates on load, then every 6 hours
    checkForUpdate();
    setInterval(checkForUpdate, 6 * 60 * 60 * 1000);

    el('message-input').focus();
}

// Expose approval handler for inline onclick
window.handleApproval = handleApproval;

document.addEventListener('DOMContentLoaded', async () => {
    if (await checkAuth()) init();
});
