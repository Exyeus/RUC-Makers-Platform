const log = document.querySelector('#chat-log');
const form = document.querySelector('#chat-send');
const status = document.querySelector('#chat-status');
let stopped = false;
let busy = false;
let polling = null;
let cursor = Number(log.dataset.after || 0);

function stopChat() {
    stopped = true;
    form.hidden = true;
    log.replaceChildren();
    document.querySelector('#chat-contacts').replaceChildren();
    status.textContent = '会话权限已变更，请返回消息列表。';
}

function nextClientId(data) {
    // Prefer the server ID: HTTP browsers need not expose crypto.randomUUID.
    if (typeof data.next_client_id === 'string' && /^[a-f0-9-]{36}$/i.test(data.next_client_id)) {
        return data.next_client_id;
    }
    const crypto = globalThis.crypto;
    if (typeof crypto?.randomUUID === 'function') return crypto.randomUUID();
    if (typeof crypto?.getRandomValues === 'function') {
        const bytes = crypto.getRandomValues(new Uint8Array(16));
        bytes[6] = (bytes[6] & 15) | 64;
        bytes[8] = (bytes[8] & 63) | 128;
        const hex = Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('');
        return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
    }
    throw new Error('No next message ID');
}

async function readMessages() {
    if (stopped || document.hidden) return;
    try {
        let more;
        do {
            const url = new URL(location.href);
            url.searchParams.set('poll', '1');
            url.searchParams.set('after', String(cursor));
            const response = await fetch(url, {cache: 'no-store'});
            if (response.status === 403 || response.status === 404 || response.redirected) {
                stopChat();
                return;
            }
            if (!response.ok) throw new Error('Polling failed');
            const data = await response.json();
            for (const message of data.messages) {
                if (Number(message.id) <= cursor) continue;
                const paragraph = document.createElement('p');
                paragraph.dataset.messageId = message.id;
                paragraph.textContent = (message.author_id === log.dataset.me ? '我' : '对方') + '：' + message.body;
                log.append(paragraph);
                cursor = Number(message.id);
            }
            const contacts = document.querySelector('#chat-contacts');
            contacts.replaceChildren();
            for (const [key, value] of Object.entries(data.contacts)) {
                const paragraph = document.createElement('p');
                paragraph.textContent = key + '：' + value;
                contacts.append(paragraph);
            }
            more = data.has_more && data.messages.length > 0;
        } while (more && !stopped && !document.hidden);
        status.textContent = '消息已更新';
    } catch {
        status.textContent = '连接暂时中断，正在重试。';
    }
}

function poll() {
    // Sending and the timer can ask for an update together; share one cursor read.
    if (!polling) polling = readMessages().finally(() => { polling = null; });
    return polling;
}

form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || stopped) return;
    busy = true;
    form.querySelector('button').disabled = true;
    try {
        const response = await fetch(location.href, {
            method: 'POST', body: new FormData(form), headers: {'X-Requested-With': 'fetch'},
        });
        if (response.status === 403 || response.status === 404 || response.redirected) {
            stopChat();
            return;
        }
        const data = await response.json();
        if (!response.ok) {
            status.textContent = data.error || '发送失败';
            return;
        }
        const nextId = nextClientId(data);
        // Keep both body and old retry ID if the acknowledgement was incomplete.
        form.elements.client_id.value = nextId;
        form.elements.body.value = '';
        await poll();
    } catch {
        status.textContent = '发送状态未知，可重试同一条消息，不会重复发送。';
    } finally {
        busy = false;
        form.querySelector('button').disabled = false;
    }
});

async function loop() {
    await poll();
    if (!stopped) setTimeout(loop, 3000);
}
loop();
