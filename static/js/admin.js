let currentConvId = null;

async function selectConversation(convId) {
    const isSwitchingConversation = currentConvId !== convId;
    currentConvId = convId;

    // Highlight selected in sidebar
    document.querySelectorAll('.conv-list-item').forEach(el => el.classList.remove('active'));
    const selectedTab = document.getElementById(`conv-tab-${convId}`);
    if (selectedTab) selectedTab.classList.add('active');

    const emptyNotice = document.getElementById('convEmptyNotice');
    const activeView = document.getElementById('convActiveView');
    if (emptyNotice) emptyNotice.style.display = 'none';
    if (activeView) activeView.style.display = 'flex';

    // Always scroll to bottom when opening a different conversation.
    // When refreshing the SAME conversation (e.g. after a reply/takeover),
    // only re-snap to bottom if the admin was already reading near the
    // bottom — otherwise preserve their scroll position so reading older
    // messages isn't interrupted.
    await loadConversationDetails(convId, { forceScrollToBottom: isSwitchingConversation });
}

async function loadConversationDetails(convId, options = {}) {
    const { forceScrollToBottom = false } = options;
    const streamEl = document.getElementById('convMessagesStream');

    // Detect whether the admin is currently scrolled near the bottom
    // BEFORE we touch the DOM, so a mid-refresh doesn't yank them down
    // while they're reading earlier messages.
    let wasNearBottom = true;
    if (streamEl) {
        const distanceFromBottom = streamEl.scrollHeight - streamEl.scrollTop - streamEl.clientHeight;
        wasNearBottom = distanceFromBottom < 80;
    }

    try {
        const res = await fetch(`/api/chat/history/${convId}`);
        const data = await res.json();
        if (!data.success) return;

        document.getElementById('activeConvTitle').textContent = `Conversation #${data.conversation_id}`;
        
        // Status Badge
        const statusBadge = document.getElementById('activeConvStatusBadge');
        if (data.status === 'HUMAN') {
            statusBadge.className = 'badge badge-warning';
            statusBadge.textContent = '👨‍💼 HUMAN STAFF';
            document.getElementById('btnTakeover').style.display = 'none';
            document.getElementById('btnRelease').style.display = 'inline-flex';
        } else {
            statusBadge.className = 'badge badge-success';
            statusBadge.textContent = '🤖 AI RECEPTIONIST';
            document.getElementById('btnTakeover').style.display = 'inline-flex';
            document.getElementById('btnRelease').style.display = 'none';
        }

        // State Badge
        const stateBadge = document.getElementById('activeConvStateBadge');
        stateBadge.textContent = `State: ${data.workflow_state || 'START'}`;

        // Stream messages
        const stream = document.getElementById('convMessagesStream');
        stream.innerHTML = '';

        data.messages.forEach(m => {
            const row = document.createElement('div');
            row.className = `message-row ${m.role}`;
            row.innerHTML = `
                <div class="message-bubble">
                    ${formatMarkdownAdmin(m.content)}
                </div>
                <div class="message-meta">${m.role.toUpperCase()} • ${m.created_at ? new Date(m.created_at).toLocaleTimeString() : ''}</div>
            `;
            stream.appendChild(row);
        });

        // Only snap to the latest message if the admin was already near
        // the bottom, or this is a fresh conversation switch — never
        // interrupt someone scrolled up reading history.
        if (forceScrollToBottom || wasNearBottom) {
            stream.scrollTop = stream.scrollHeight;
        }
    } catch (e) {
        console.error('Error loading conversation details:', e);
    }
}

function formatMarkdownAdmin(text) {
    if (!text) return '';
    return text
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
        .replace(/•/g, '&bull;')
        .replace(/\n/g, '<br>');
}

async function takeoverActiveConv() {
    if (!currentConvId) return;
    try {
        const res = await fetch('/api/admin/takeover', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ conversation_id: currentConvId })
        });
        const data = await res.json();
        if (data.success) {
            alert('You have taken over the conversation. AI auto-reply is paused.');
            await loadConversationDetails(currentConvId);
            window.location.reload();
        }
    } catch (e) {
        alert('Failed to take over: ' + e.message);
    }
}

async function releaseActiveConv() {
    if (!currentConvId) return;
    try {
        const res = await fetch('/api/admin/release', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ conversation_id: currentConvId })
        });
        const data = await res.json();
        if (data.success) {
            alert('Conversation released back to AI receptionist.');
            await loadConversationDetails(currentConvId);
            window.location.reload();
        }
    } catch (e) {
        alert('Failed to release: ' + e.message);
    }
}

async function sendStaffReply(e) {
    e.preventDefault();
    if (!currentConvId) return;
    const input = document.getElementById('staffReplyInput');
    const text = input.value.trim();
    if (!text) return;

    try {
        const res = await fetch('/api/admin/reply', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                conversation_id: currentConvId,
                message: text
            })
        });
        const data = await res.json();
        if (data.success) {
            input.value = '';
            await loadConversationDetails(currentConvId);
        }
    } catch (e) {
        alert('Failed to send staff reply: ' + e.message);
    }
}
