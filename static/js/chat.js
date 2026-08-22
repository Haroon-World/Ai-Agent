let conversationId = localStorage.getItem('ai_business_conv_id');
const chatMessages = document.getElementById('chatMessages');
const chatForm = document.getElementById('chatForm');
const chatInput = document.getElementById('chatInput');
const btnSend = document.getElementById('btnSend');
const btnResetChat = document.getElementById('btnResetChat');
const handoffBanner = document.getElementById('handoffBanner');
const statusDot = document.getElementById('statusDot');
const agentModeLabel = document.getElementById('agentModeLabel');

// Initialize Chat on load
document.addEventListener('DOMContentLoaded', () => {
    if (conversationId) {
        loadHistory(conversationId);
    } else {
        initNewConversation();
    }

    chatForm.addEventListener('submit', handleSendMessage);
    btnResetChat.addEventListener('click', resetChat);

    // Poll every 4 seconds to sync messages if in human handoff mode or waiting for staff
    setInterval(() => {
        if (conversationId) {
            syncHistorySilently(conversationId);
        }
    }, 4000);
});

async function initNewConversation() {
    try {
        const res = await fetch('/api/chat/init', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            conversationId = data.conversation_id;
            localStorage.setItem('ai_business_conv_id', conversationId);
            renderMessages(data.messages);
            updateStatusUI(data.status);
        }
    } catch (e) {
        console.error('Failed to init conversation:', e);
        chatMessages.innerHTML = '<div class="chat-loading">Failed to connect to AI Receptionist. Please refresh.</div>';
    }
}

async function loadHistory(convId) {
    try {
        const res = await fetch(`/api/chat/history/${convId}`);
        const data = await res.json();
        if (data.success) {
            renderMessages(data.messages);
            updateStatusUI(data.status);
        } else {
            initNewConversation();
        }
    } catch (e) {
        console.error('Failed loading history:', e);
        initNewConversation();
    }
}

async function syncHistorySilently(convId) {
    try {
        const res = await fetch(`/api/chat/history/${convId}`);
        const data = await res.json();
        if (data.success) {
            renderMessages(data.messages);
            updateStatusUI(data.status);
        }
    } catch (e) {
        // silent
    }
}

function updateStatusUI(status) {
    if (status === 'HUMAN') {
        handoffBanner.style.display = 'flex';
        statusDot.className = 'status-indicator human';
        agentModeLabel.textContent = 'Human Receptionist • Active';
    } else {
        handoffBanner.style.display = 'none';
        statusDot.className = 'status-indicator online';
        agentModeLabel.textContent = 'AI Receptionist • Active';
    }
}

function renderMessages(messages) {
    chatMessages.innerHTML = '';
    messages.forEach(m => {
        if (m.role === 'user' || m.role === 'assistant') {
            appendMessageBubble(m.role, m.content, m.created_at);
        }
    });
    scrollToBottom();
}

function appendMessageBubble(role, content, timestamp) {
    const row = document.createElement('div');
    row.className = `message-row ${role}`;

    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';

    // Simple markdown formatting helper
    bubble.innerHTML = formatMarkdown(content);

    const meta = document.createElement('div');
    meta.className = 'message-meta';
    const timeStr = timestamp ? new Date(timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    meta.textContent = `${role === 'user' ? 'You' : 'SmileCare AI'} • ${timeStr}`;

    row.appendChild(bubble);
    row.appendChild(meta);
    chatMessages.appendChild(row);
}

function formatMarkdown(text) {
    if (!text) return '';
    let formatted = text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.*?)\*/g, '<em>$1</em>')
        .replace(/•/g, '&bull;')
        .replace(/\n/g, '<br>');
    return formatted;
}

async function handleSendMessage(e) {
    e.preventDefault();
    const text = chatInput.value.trim();
    if (!text) return;

    // Append user message immediately to UI
    appendMessageBubble('user', text);
    chatInput.value = '';
    scrollToBottom();

    // Show typing / thinking state
    const thinkingRow = document.createElement('div');
    thinkingRow.className = 'message-row assistant';
    thinkingRow.id = 'thinkingBubble';
    thinkingRow.innerHTML = '<div class="message-bubble"><em>SmileCare AI is checking clinic records... ⏳</em></div>';
    chatMessages.appendChild(thinkingRow);
    scrollToBottom();

    try {
        const res = await fetch('/api/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                conversation_id: conversationId,
                message: text
            })
        });

        const data = await res.json();
        const thinkingElem = document.getElementById('thinkingBubble');
        if (thinkingElem) thinkingElem.remove();

        if (data.success) {
            appendMessageBubble('assistant', data.reply);
            if (data.ui_action) {
                renderUIAction(data.ui_action);
            }
            updateStatusUI(data.status);
        } else {
            appendMessageBubble('assistant', `⚠️ Error: ${data.error || 'Something went wrong.'}`);
        }
    } catch (err) {
        const thinkingElem = document.getElementById('thinkingBubble');
        if (thinkingElem) thinkingElem.remove();
        appendMessageBubble('assistant', '⚠️ Connection error. Please try sending again.');
    }
    scrollToBottom();
}

function renderUIAction(uiAction) {
    if (!uiAction || !uiAction.options || uiAction.options.length === 0) return;

    const container = document.createElement('div');
    container.className = 'ui-action-container';
    container.style.display = 'flex';
    container.style.flexWrap = 'wrap';
    container.style.gap = '8px';
    container.style.marginTop = '8px';
    container.style.marginBottom = '12px';

    if (uiAction.type === 'doctor_selection') {
        uiAction.options.forEach(doc => {
            const btn = document.createElement('button');
            btn.className = 'btn btn-sm btn-outline ui-card-btn';
            btn.innerHTML = `<strong>${doc.name}</strong><br><small>${doc.specialization}</small>`;
            btn.onclick = () => sendQuickPrompt(`I select ${doc.name}`);
            container.appendChild(btn);
        });
    } else if (uiAction.type === 'service_selection') {
        uiAction.options.forEach(svc => {
            const btn = document.createElement('button');
            btn.className = 'btn btn-sm btn-outline ui-card-btn';
            btn.innerHTML = `<strong>${svc.name}</strong><br><small>Rs. ${svc.price} • ${svc.duration}m</small>`;
            btn.onclick = () => sendQuickPrompt(`I want ${svc.name}`);
            container.appendChild(btn);
        });
    } else if (uiAction.type === 'date_selection') {
        uiAction.options.forEach(opt => {
            const btn = document.createElement('button');
            btn.className = 'btn btn-sm btn-outline ui-chip-btn';
            btn.textContent = `📅 ${opt.label} (${opt.value})`;
            btn.onclick = () => sendQuickPrompt(opt.value);
            container.appendChild(btn);
        });
    }

    chatMessages.appendChild(container);
    scrollToBottom();
}

function sendQuickPrompt(promptText) {
    chatInput.value = promptText;
    btnSend.click();
}

async function resetChat() {
    if (!confirm('Start a new conversation session?')) return;
    try {
        const res = await fetch('/api/chat/reset', { method: 'POST' });
        const data = await res.json();
        if (data.success) {
            conversationId = data.conversation_id;
            localStorage.setItem('ai_business_conv_id', conversationId);
            renderMessages(data.messages);
            updateStatusUI('AI');
        }
    } catch (e) {
        console.error('Reset error:', e);
    }
}

function scrollToBottom() {
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// Voice Recording Handling
let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;

const btnVoiceRecord = document.getElementById('btnVoiceRecord');
const voiceMicIcon = document.getElementById('voiceMicIcon');

if (btnVoiceRecord) {
    btnVoiceRecord.addEventListener('click', toggleVoiceRecord);
}

async function toggleVoiceRecord() {
    if (!isRecording) {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            audioChunks = [];
            const options = MediaRecorder.isTypeSupported('audio/webm') ? { mimeType: 'audio/webm' } : {};
            mediaRecorder = new MediaRecorder(stream, options);
            
            mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) audioChunks.push(e.data);
            };

            mediaRecorder.onstop = async () => {
                const recordedMime = mediaRecorder.mimeType || 'audio/webm';
                const audioBlob = new Blob(audioChunks, { type: recordedMime });
                await sendVoiceBlob(audioBlob, recordedMime);
                stream.getTracks().forEach(track => track.stop());
            };

            mediaRecorder.start();
            isRecording = true;
            btnVoiceRecord.style.background = '#e74c3c';
            btnVoiceRecord.style.color = '#fff';
            voiceMicIcon.textContent = '⏹️';
        } catch (err) {
            console.error('Microphone access error:', err);
            alert('Microphone access is required for voice messages.');
        }
    } else {
        if (mediaRecorder && mediaRecorder.state !== 'inactive') {
            mediaRecorder.stop();
        }
        isRecording = false;
        btnVoiceRecord.style.background = '';
        btnVoiceRecord.style.color = '';
        voiceMicIcon.textContent = '🎤';
    }
}

async function sendVoiceBlob(blob, mimeType) {
    const formData = new FormData();
    const ext = mimeType.includes('wav') ? 'wav' : 'webm';
    formData.append('file', blob, `voice_input.${ext}`);
    if (conversationId) {
        formData.append('conversation_id', conversationId);
    }

    appendMessageBubble('user', '🎙️ (Voice Message)', new Date().toISOString());
    btnSend.disabled = true;

    try {
        const res = await fetch('/api/chat/send-voice', {
            method: 'POST',
            body: formData
        });
        const data = await res.json();
        btnSend.disabled = false;

        if (data.success) {
            if (data.session_reset || !conversationId) {
                conversationId = data.conversation_id;
                localStorage.setItem('ai_business_conv_id', conversationId);
            }
            appendMessageBubble('assistant', data.reply, new Date().toISOString());
            if (data.ui_action) renderUIAction(data.ui_action);
            updateStatusUI(data.status);
        } else {
            appendMessageBubble('assistant', `⚠️ ${data.error || 'Voice processing error.'}`, new Date().toISOString());
        }
    } catch (err) {
        btnSend.disabled = false;
        console.error('Voice send error:', err);
        appendMessageBubble('assistant', '⚠️ Failed to send voice message.', new Date().toISOString());
    }
}
