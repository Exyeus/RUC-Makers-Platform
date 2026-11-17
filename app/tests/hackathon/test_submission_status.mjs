import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

// Exercise the live updater against the same named receipt nodes used by the template.
const nodes = new Map([
  '[data-submission-state]', '[data-project-revision]', '[data-status-detail]',
  '[data-current-received]', '[data-received-label]', '[data-received-time]',
  '[data-receipt-detail]', '[data-receipt-id]', '[data-download-submission]',
  '[data-status-query-feedback]',
].map(selector => [selector, { textContent: '', hidden: false }]));
const root = {
  dataset: { eventTimezone: 'Asia/Shanghai' },
  querySelector: selector => nodes.get(selector) || null,
};
const save = { tagName: 'BUTTON', name: 'action', disabled: false };
const submit = { tagName: 'BUTTON', name: 'action', disabled: false };
const projectForm = { dataset: { submissionsOpen: 'true' }, elements: [save, submit], addEventListener() {} };
const guidance = { textContent: '' };
let savedMaterial = { textContent: '材料已保存，作品尚未正式提交。' };
const uploadStatus = { textContent: '文件校验失败，请更换文件。' };
const saveFeedback = { textContent: 'previous save' };
const submitFeedback = { textContent: 'previous submission' };
globalThis.document = {
  querySelector: selector => ({
    '[data-project-status-url]': root, '[data-project-form]': projectForm,
    '[data-submit-project]': submit, '[data-submit-guidance]': guidance,
    '[data-project-status]': saveFeedback, '[data-submit-status]': submitFeedback,
    '[data-material-save-advisory]': savedMaterial, '[data-upload-status]': uploadStatus,
  })[selector] || null,
  querySelectorAll: () => [],
};
const transfer = readFileSync(new URL('../../eventyay/static/hackathon/upload-transfer.js', import.meta.url));
const transferURL = `data:text/javascript;base64,${transfer.toString('base64')}`;
const source = readFileSync(new URL('../../eventyay/static/hackathon/upload.js', import.meta.url), 'utf8')
  .replace("'./upload-transfer.js?v=20261116.2'", JSON.stringify(transferURL));
const { renderProject, formatEventTime, projectFeedback } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
assert.equal(projectFeedback('submit'), submitFeedback);
assert.equal(saveFeedback.textContent, '');
assert.equal(submitFeedback.textContent, '');
submitFeedback.textContent = 'submit succeeded';
assert.equal(projectFeedback('save'), saveFeedback);
assert.equal(submitFeedback.textContent, '');
const data = {
  state: 'submitted', status_key: 'submitted', revision: 7, label: '当前版本已正式提交',
  status_detail: '等待检查', receipt_id: 'old-receipt', received_at: '2026-11-16T10:34:00Z',
  active_upload_count: 0, submissions_open: true,
};
renderProject(data);
renderProject({ ...data, revision: 8, receipt_id: 'new-receipt', received_at: '2026-11-16T10:47:46Z' });
assert.equal(nodes.get('[data-receipt-id]').textContent, 'new-receipt');
assert.equal(savedMaterial.textContent, '材料已保存，第 8 版已正式提交。');
assert.equal(nodes.get('[data-received-time]').textContent, '2026-11-16 18:47:46');
assert.equal(nodes.get('[data-project-revision]').textContent, 8);
assert.equal(nodes.get('[data-download-submission]').textContent, '下载本版提交凭证');
assert.equal([...nodes.values()].some(node => String(node.textContent).includes('old-receipt')), false);
assert.equal(formatEventTime('2026-11-16T20:00:00Z'), '2026-11-17 04:00:00');
renderProject({ ...data, state: 'changes', status_key: 'resubmit', revision: 9 });
assert.equal(nodes.get('[data-received-label]').textContent, '最近登记时间');
assert.equal(nodes.get('[data-download-submission]').textContent, '下载最近登记凭证');
renderProject({ ...data, state: 'queued', active_upload_count: 1 });
assert.equal(submit.textContent, '登记排队提交');
assert.equal(savedMaterial.textContent, '材料已保存，正在等待其余排队材料完成。');
assert.equal(submit.disabled, true);
assert.equal(save.disabled, true);
renderProject({ ...data, state: 'changes', submissions_open: false, active_upload_count: 0 });
assert.equal(submit.disabled, true); // Cancelling a queue never reopens a passed deadline.
renderProject({ ...data, state: 'changes', submissions_open: true });
assert.equal(submit.disabled, false);
renderProject({ ...data, receipt_id: null, received_at: null });
assert.equal(nodes.get('[data-receipt-id]').textContent, '');
assert.equal(nodes.get('[data-download-submission]').hidden, true);
savedMaterial = null; // A live transfer or validation error owns the status area; no terminal advisory exists.
renderProject(data);
assert.equal(uploadStatus.textContent, '文件校验失败，请更换文件。');
console.log('submission status regression passed');
