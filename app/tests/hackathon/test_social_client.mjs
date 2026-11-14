import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../../eventyay/static/hackathon/social.js', import.meta.url), 'utf8');
const oldId = '11111111-1111-4111-8111-111111111111';
const serverIds = ['22222222-2222-4222-8222-222222222222', '33333333-3333-4333-8333-333333333333'];
const handlers = {};
const button = {disabled: false};
const form = {
    hidden: false,
    elements: {body: {value: '第一条'}, client_id: {value: oldId}},
    querySelector: () => button,
    addEventListener: (name, callback) => { handlers[name] = callback; },
};
const log = {dataset: {me: 'me', after: '0'}, rows: [], append(row) { this.rows.push(row); }, replaceChildren() { this.rows = []; }};
const contacts = {replaceChildren() {}, append() {}};
const status = {textContent: ''};
const posts = [];
let failNextPost = false;
const context = vm.createContext({
    console, URL, Promise, Number, String, Array, Object, Error,
    location: {href: 'http://insecure.example.test/chat/'},
    // No crypto object at all: successful responses must still advance the retry ID.
    document: {
        hidden: false,
        querySelector: selector => ({'#chat-log': log, '#chat-send': form, '#chat-status': status, '#chat-contacts': contacts})[selector],
        createElement: () => ({dataset: {}, textContent: ''}),
    },
    FormData: class { constructor(value) { this.body = value.elements.body.value; this.id = value.elements.client_id.value; } },
    fetch: async (url, options = {}) => {
        if (options.method === 'POST') {
            posts.push(options.body);
            if (failNextPost) { failNextPost = false; throw new Error('reply lost'); }
            return {ok: true, status: 200, redirected: false, json: async () => ({ok: true, next_client_id: serverIds.shift()})};
        }
        return {ok: true, status: 200, redirected: false, json: async () => ({messages: [], contacts: {}, has_more: false})};
    },
    setTimeout: () => 0,
});
vm.runInContext(source, context);
await vm.runInContext('poll()', context);
await handlers.submit({preventDefault() {}});
assert.equal(posts[0].id, oldId);
assert.equal(form.elements.client_id.value, '22222222-2222-4222-8222-222222222222');
assert.equal(form.elements.body.value, '');
form.elements.body.value = '第二条';
failNextPost = true;
await handlers.submit({preventDefault() {}});
assert.equal(form.elements.body.value, '第二条');
assert.equal(form.elements.client_id.value, '22222222-2222-4222-8222-222222222222');
assert.match(status.textContent, /可重试同一条消息/);
await handlers.submit({preventDefault() {}});
assert.equal(posts[1].id, posts[2].id);
assert.equal(posts[2].body, '第二条');
assert.equal(form.elements.client_id.value, '33333333-3333-4333-8333-333333333333');
assert.equal(form.elements.body.value, '');
assert.equal(button.disabled, false);
console.log('HTTP-compatible next retry IDs and ambiguous-response retries passed');
