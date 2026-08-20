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
