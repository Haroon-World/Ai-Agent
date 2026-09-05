let conversationId = localStorage.getItem('ai_business_conv_id');
const chatMessages = document.getElementById('chatMessages');
const chatForm = document.getElementById('chatForm');
const chatInput = document.getElementById('chatInput');
const btnSend = document.getElementById('btnSend');
const btnResetChat = document.getElementById('btnResetChat');
const handoffBanner = document.getElementById('handoffBanner');
const statusDot = document.getElementById('statusDot');
const agentModeLabel = document.getElementById('agentModeLabel');

const SCROLL_BOTTOM_THRESHOLD = 80;
let lastRenderedMessageCount = 0;
let lastRenderedStatus = '';

function isUserNearBottom(threshold = SCROLL_BOTTOM_THRESHOLD) {
    if (!chatMessages) return true;
    const distanceToBottom = chatMessages.scrollHeight - chatMessages.scrollTop - chatMessages.clientHeight;
    return distanceToBottom <= threshold;
}

function scrollToBottom(force = false) {
    if (!chatMessages) return;
    if (force || isUserNearBottom()) {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    }
}

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
            lastRenderedStatus = data.status;
            renderMessages(data.messages, true);
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
            lastRenderedStatus = data.status;
            renderMessages(data.messages, true);
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
            const msgs = data.messages || [];
            const hasNewMessages = msgs.length !== lastRenderedMessageCount;
            const hasStatusChange = data.status !== lastRenderedStatus;

            if (hasNewMessages || hasStatusChange) {
                lastRenderedStatus = data.status;
                renderMessages(msgs, false);
                updateStatusUI(data.status);
            }
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

function renderMessages(messages, forceScroll = false) {
    if (!chatMessages) return;

    const wasNearBottom = isUserNearBottom();
    const prevScrollTop = chatMessages.scrollTop;

    chatMessages.innerHTML = '';
    const lastAssistantIdx = messages.map(m => m.role).lastIndexOf('assistant');
    messages.forEach((m, idx) => {
        if (m.role === 'user' || m.role === 'assistant') {
            appendMessageBubble(m.role, m.content, m.created_at);
            // Render interactive UI controls ONLY for the current active assistant message
            if (m.role === 'assistant' && (m.interactive_data || m.ui_action) && idx === lastAssistantIdx) {
                renderUIAction(m.interactive_data || m.ui_action, true, false);
            }
        }
    });

    lastRenderedMessageCount = messages.length;

    if (forceScroll || wasNearBottom) {
        chatMessages.scrollTop = chatMessages.scrollHeight;
    } else {
        chatMessages.scrollTop = prevScrollTop;
    }
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
    meta.textContent = `${role === 'user' ? 'You' : 'ClinicConnect AI'} • ${timeStr}`;

    row.appendChild(bubble);
    row.appendChild(meta);
    chatMessages.appendChild(row);
    return bubble;
}

function updateMessageBubbleContent(bubbleEl, content) {
    if (!bubbleEl) return;
    bubbleEl.innerHTML = formatMarkdown(content);
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

    // Remove any interactive UI action wrappers from previous turns immediately
    document.querySelectorAll('.ui-action-wrapper').forEach(w => w.remove());

    // Append user message immediately to UI and scroll to bottom
    appendMessageBubble('user', text);
    chatInput.value = '';
    scrollToBottom(true);

    // Show typing / thinking state
    const thinkingRow = document.createElement('div');
    thinkingRow.className = 'message-row assistant';
    thinkingRow.id = 'thinkingBubble';
    thinkingRow.innerHTML = '<div class="message-bubble"><em>ClinicConnect AI is checking clinic records... ⏳</em></div>';
    chatMessages.appendChild(thinkingRow);
    scrollToBottom(true);

    try {
        const res = await fetch('/api/chat/send', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                conversation_id: conversationId,
                message: text
            })
        });

        let data;
        try {
            data = await res.json();
        } catch (parseErr) {
            data = { success: false, error: 'Server response could not be parsed.' };
        }

        const thinkingElem = document.getElementById('thinkingBubble');
        if (thinkingElem) thinkingElem.remove();

        if (data.success) {
            const wasNear = isUserNearBottom();
            appendMessageBubble('assistant', data.reply);
            // Ensure no stale UI action wrappers exist before rendering new action
            document.querySelectorAll('.ui-action-wrapper').forEach(w => w.remove());
            if (data.ui_action) {
                renderUIAction(data.ui_action, true, wasNear);
            }
            if (wasNear) {
                scrollToBottom(true);
            }
            updateStatusUI(data.status);
        } else {
            appendMessageBubble('assistant', `⚠️ ${data.error || 'Something went wrong. Please try again.'}`);
            scrollToBottom();
        }
    } catch (err) {
        const thinkingElem = document.getElementById('thinkingBubble');
        if (thinkingElem) thinkingElem.remove();
        appendMessageBubble('assistant', '⚠️ Connection error. Please check your connection and try again.');
        scrollToBottom();
    }
}

function renderUIAction(uiAction, isCurrent = true, shouldScroll = true) {
    if (!uiAction) return;

    // Always remove existing ui-action wrappers to ensure only one active UI control exists
    document.querySelectorAll('.ui-action-wrapper').forEach(w => w.remove());

    // 1. Final Booking Confirmation Card
    if (uiAction.type === 'booking_confirmation' && uiAction.details) {
        const d = uiAction.details;
        const wrapper = document.createElement('div');
        wrapper.className = 'ui-action-wrapper';
        if (!isCurrent) {
            wrapper.classList.add('ui-action-disabled');
        }

        const card = document.createElement('div');
        card.className = 'ui-confirm-card';
        card.innerHTML = `
            <div class="ui-confirm-header">
                <span>📋</span>
                <span>${uiAction.title || 'Review & Confirm Appointment'}</span>
            </div>
            <table class="ui-confirm-table">
                <tbody>
                    <tr><td>🩺 Service</td><td>${d.service_name} (${d.service_duration})</td></tr>
                    <tr><td>👨‍⚕️ Doctor</td><td>${d.doctor_name}</td></tr>
                    <tr><td>📅 Date</td><td>${d.formatted_date || d.date}</td></tr>
                    <tr><td>⏰ Time</td><td>${d.formatted_time || d.time}</td></tr>
                    <tr><td>👤 Patient</td><td>${d.customer_name}</td></tr>
                    <tr><td>📞 Phone</td><td>${d.customer_phone}</td></tr>
                    ${d.service_price ? `<tr><td>💰 Fee</td><td>${d.service_price}</td></tr>` : ''}
                </tbody>
            </table>
            <div class="ui-confirm-actions">
                <button class="ui-btn-confirm" id="btnConfirmAppointment">
                    <span>✅ Confirm Booking</span>
                </button>
                <button class="ui-btn-change" id="btnChangeAppointment">
                    <span>✏️ Change</span>
                </button>
                <button class="ui-btn-change" id="btnCancelAppointment" style="color: #ef4444; border-color: #fca5a5;">
                    <span>❌ Cancel</span>
                </button>
            </div>
        `;

        const confirmBtn = card.querySelector('#btnConfirmAppointment');
        const changeBtn = card.querySelector('#btnChangeAppointment');
        const cancelBtn = card.querySelector('#btnCancelAppointment');

        if (confirmBtn) {
            confirmBtn.onclick = () => {
                wrapper.classList.add('ui-action-disabled');
                sendQuickPrompt('Confirm Appointment');
            };
        }

        if (changeBtn) {
            changeBtn.onclick = () => {
                wrapper.classList.add('ui-action-disabled');
                sendQuickPrompt('I want to change my appointment details');
            };
        }

        if (cancelBtn) {
            cancelBtn.onclick = () => {
                wrapper.classList.add('ui-action-disabled');
                sendQuickPrompt('Cancel booking');
            };
        }

        wrapper.appendChild(card);
        chatMessages.appendChild(wrapper);
        if (shouldScroll) {
            scrollToBottom();
        }
        return;
    }

    if (!uiAction.options || uiAction.options.length === 0) return;

    const wrapper = document.createElement('div');
    wrapper.className = 'ui-action-wrapper';
    if (!isCurrent) {
        wrapper.classList.add('ui-action-disabled');
    }

    if (uiAction.title) {
        const titleEl = document.createElement('div');
        titleEl.className = 'ui-action-title';
        titleEl.textContent = uiAction.title;
        wrapper.appendChild(titleEl);
    }

    // 2. Service Selection Cards
    if (uiAction.type === 'service_selection') {
        const grid = document.createElement('div');
        grid.className = 'ui-service-grid';

        uiAction.options.forEach(svc => {
            const card = document.createElement('div');
            card.className = 'ui-service-card';
            card.innerHTML = `
                <div class="ui-service-name">🦷 ${svc.name}</div>
                <div class="ui-service-meta">
                    <span class="ui-service-price">${svc.price_formatted || ('Rs. ' + svc.price)}</span>
                    <span>⏱️ ${svc.duration} mins</span>
                </div>
                ${svc.description ? `<div class="ui-service-desc">${svc.description}</div>` : ''}
            `;
            card.onclick = () => {
                wrapper.classList.add('ui-action-disabled');
                sendQuickPrompt(svc.name);
            };
            grid.appendChild(card);
        });
        wrapper.appendChild(grid);
    }

    // 3. Doctor Selection Cards
    else if (uiAction.type === 'doctor_selection') {
        const grid = document.createElement('div');
        grid.className = 'ui-doctor-grid';

        uiAction.options.forEach(doc => {
            const card = document.createElement('div');
            card.className = 'ui-doctor-card';
            card.innerHTML = `
                <div class="ui-doctor-avatar">👨‍⚕️</div>
                <div class="ui-doctor-info">
                    <div class="ui-doctor-name">${doc.name}</div>
                    <div class="ui-doctor-spec">${doc.specialization || 'Dentist'}</div>
                </div>
            `;
            card.onclick = () => {
                wrapper.classList.add('ui-action-disabled');
                sendQuickPrompt(doc.name);
            };
            grid.appendChild(card);
        });
        wrapper.appendChild(grid);
    }

    // 4. Date Selection Chips + Date Picker
    else if (uiAction.type === 'date_selection') {
        const grid = document.createElement('div');
        grid.className = 'ui-date-grid';

        uiAction.options.forEach(opt => {
            const chip = document.createElement('button');
            chip.className = 'ui-date-chip';
            chip.innerHTML = `
                <span>📅 ${opt.label}</span>
                <span class="day-label">${opt.day || ''}</span>
            `;
            chip.onclick = () => {
                wrapper.classList.add('ui-action-disabled');
                sendQuickPrompt(opt.value);
            };
            grid.appendChild(chip);
        });

        // Custom date input picker
        if (uiAction.allow_custom_date) {
            const customContainer = document.createElement('div');
            customContainer.style.display = 'inline-flex';
            customContainer.style.alignItems = 'center';

            const datePicker = document.createElement('input');
            datePicker.type = 'date';
            datePicker.className = 'ui-custom-date-btn';
            const todayStr = new Date().toISOString().split('T')[0];
            datePicker.min = todayStr;
            datePicker.title = 'Choose custom date';

            datePicker.onchange = (e) => {
                if (e.target.value) {
                    wrapper.classList.add('ui-action-disabled');
                    sendQuickPrompt(e.target.value);
                }
            };
            customContainer.appendChild(datePicker);
            grid.appendChild(customContainer);
        }

        wrapper.appendChild(grid);
    }

    // 5. Time Slots Selection (Grouped Morning / Afternoon)
    else if (uiAction.type === 'time_slot_selection' || uiAction.type === 'time_selection' || uiAction.type === 'slot_selection') {
        const slotsContainer = document.createElement('div');
        slotsContainer.className = 'ui-slots-container';

        const morningSlots = uiAction.options.filter(o => o.period === 'Morning');
        const afternoonSlots = uiAction.options.filter(o => o.period === 'Afternoon');

        if (morningSlots.length > 0) {
            const sec = document.createElement('div');
            sec.className = 'ui-slot-section';
            sec.innerHTML = `<div class="ui-slot-section-title">🌅 Morning</div>`;
            const g = document.createElement('div');
            g.className = 'ui-slot-grid';
            morningSlots.forEach(opt => {
                const pill = document.createElement('button');
                pill.className = 'ui-slot-pill';
                pill.textContent = opt.label || opt.value || opt;
                pill.onclick = () => {
                    wrapper.classList.add('ui-action-disabled');
                    sendQuickPrompt(opt.value || opt.label || opt);
                };
                g.appendChild(pill);
            });
            sec.appendChild(g);
            slotsContainer.appendChild(sec);
        }

        if (afternoonSlots.length > 0) {
            const sec = document.createElement('div');
            sec.className = 'ui-slot-section';
            sec.innerHTML = `<div class="ui-slot-section-title">☀️ Afternoon</div>`;
            const g = document.createElement('div');
            g.className = 'ui-slot-grid';
            afternoonSlots.forEach(opt => {
                const pill = document.createElement('button');
                pill.className = 'ui-slot-pill';
                pill.textContent = opt.label || opt.value || opt;
                pill.onclick = () => {
                    wrapper.classList.add('ui-action-disabled');
                    sendQuickPrompt(opt.value || opt.label || opt);
                };
                g.appendChild(pill);
            });
            sec.appendChild(g);
            slotsContainer.appendChild(sec);
        }

        if (morningSlots.length === 0 && afternoonSlots.length === 0) {
            const g = document.createElement('div');
            g.className = 'ui-slot-grid';
            uiAction.options.forEach(opt => {
                const pill = document.createElement('button');
                pill.className = 'ui-slot-pill';
                pill.textContent = opt.label || opt.value || opt;
                pill.onclick = () => {
                    wrapper.classList.add('ui-action-disabled');
                    sendQuickPrompt(opt.value || opt.label || opt);
                };
                g.appendChild(pill);
            });
            slotsContainer.appendChild(g);
        }

        wrapper.appendChild(slotsContainer);
    }

    chatMessages.appendChild(wrapper);
    if (shouldScroll) {
        scrollToBottom();
    }
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
            renderMessages(data.messages, true);
            updateStatusUI('AI');
        }
    } catch (e) {
        console.error('Reset error:', e);
    }
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

    document.querySelectorAll('.ui-action-wrapper').forEach(w => w.remove());
    const pendingBubble = appendMessageBubble('user', '🎙️ Transcribing your voice message...', new Date().toISOString());
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

            // Show what the system actually heard, not a generic
            // placeholder — the customer needs to see this to notice if
            // transcription got it wrong.
            let transcriptLabel = `🎙️ "${data.transcript}"`;
            if (data.mock_transcription) {
                transcriptLabel += '\n(⚠️ Mock transcription — not your real audio content. A real speech provider is not configured for this demo.)';
            }
            if (pendingBubble) {
                updateMessageBubbleContent(pendingBubble, transcriptLabel);
            } else {
                appendMessageBubble('user', transcriptLabel, new Date().toISOString());
            }

            const wasNear = isUserNearBottom();
            const replyBubble = appendMessageBubble('assistant', data.reply, new Date().toISOString());
            if (data.ui_action) renderUIAction(data.ui_action, true, wasNear);
            if (wasNear) scrollToBottom(true);
            updateStatusUI(data.status);

            // Speak the reply back as a voice note, since the customer
            // messaged in by voice.
            playReplyAsVoice(data.reply, replyBubble);
        } else {
            if (pendingBubble) {
                updateMessageBubbleContent(pendingBubble, '🎙️ (voice message)');
            }
            appendMessageBubble('assistant', `⚠️ ${data.error || 'Voice processing error.'}`, new Date().toISOString());
        }
    } catch (err) {
        btnSend.disabled = false;
        console.error('Voice send error:', err);
        if (pendingBubble) {
            updateMessageBubbleContent(pendingBubble, '🎙️ (voice message)');
        }
        appendMessageBubble('assistant', '⚠️ Failed to send voice message.', new Date().toISOString());
    }
}

async function playReplyAsVoice(text, bubbleEl) {
    try {
        const res = await fetch('/api/chat/synthesize', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        if (!res.ok) return;

        const audioBlob = await res.blob();
        const audioUrl = URL.createObjectURL(audioBlob);
        const audio = new Audio(audioUrl);

        if (bubbleEl) {
            const playBtn = document.createElement('button');
            playBtn.className = 'voice-reply-play-btn';
            playBtn.type = 'button';
            playBtn.textContent = '🔊 Play voice reply';
            playBtn.onclick = () => audio.play();
            bubbleEl.appendChild(playBtn);
        }

        audio.play().catch(() => {
            // Autoplay can be blocked by the browser until the user
            // interacts with the page — the Play button above still
            // lets them hear it manually.
        });
    } catch (err) {
        console.error('Voice reply synthesis error:', err);
    }
}
