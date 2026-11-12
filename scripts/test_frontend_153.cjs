// Isolated DOM/event contract tests. This is not a real-browser test.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const { test } = require('node:test');

function setup({readonly = false, fail = false, freeze = false} = {}) {
  const listeners = {}, calls = [], elements = [];
  function element(tags, value = '', dataset = {}) {
    const item = {tags, value, dataset, disabled: false, textContent: '', hidden: true,
      classList: {toggle() {}}, setAttribute() {}, appendChild() {}, remove() {},
      replaceChildren(child) {this.child = child;}, closest() {return null;},
      matches(selector) {return selector.split(',').some(s => tags.includes(s.trim()));}
    };
    elements.push(item);
    return item;
  }
  const body = {hasAttribute: () => readonly, setAttribute: () => {readonly = true;},
    appendChild() {}};
  const note1 = element(['[data-note-endpoint]'], 'old', {noteEndpoint: '/note/1'});
  const note2 = element(['[data-note-endpoint]'], 'old', {noteEndpoint: '/note/1'});
  const reviewer = element(['[data-final-reviewer-name]'], 'QA Operator');
  const reason = element(['[data-rejection-reason]'], 'Operational issue');
  const decision = element(["input[name='review-decision']", "input[name='review-decision']:checked"]);
  decision.value = 'REJECT';
  const button = element(["[data-action='finalize']"], '', {action: 'finalize', runId: 'fixture'});
  button.closest = () => button;
  element(['[data-toast-region]']);
  element(['[data-review-status]']);
  element(['[data-finalize-confirmation]']);
  const slot = element(['[data-final-pdf-slot]']);
  const token = element(["meta[name='weekend-report-csrf-token']"]);
  token.content = 'signed-fixture-token';
  const document = {body,
    querySelectorAll: s => elements.filter(e => e.matches(s)),
    querySelector: s => elements.find(e => e.matches(s)) || null,
    createElement: () => element([]),
    addEventListener: (name, handler) => {listeners[name] = handler;},
  };
  const context = {document, window: {setTimeout() {}}, fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: !(fail && url.endsWith('/finalize')), status: 400,
      headers: {get: name => name === 'X-Review-Snapshot-Frozen' ? (freeze ? '1' : null) : 'application/json'},
      json: async () => url.endsWith('/finalize')
        ? {decision: 'REJECT', final_pdf_url: '/fixture.pdf', detail: 'blocked'} : {} };
  }};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../app/web/static/app.js'), 'utf8'), context);
  return {listeners, calls, note1, note2, reviewer, reason, decision, button, slot};
}

test('duplicate editors synchronize and save once; signed two-step rejection locks controls', async () => {
  const x = setup();
  x.note1.value = 'Updated finding';
  x.listeners.input({target: x.note1});
  assert.equal(x.note2.value, 'Updated finding');
  await x.listeners.click({target: x.button});
  assert.equal(x.calls.length, 0);
  await x.listeners.click({target: x.button});
  assert.equal(x.calls.length, 2);
  assert.deepEqual(JSON.parse(x.calls[0].options.body), {note: 'Updated finding'});
  const request = x.calls[1];
  assert.equal(request.options.headers['X-CSRF-Token'], 'signed-fixture-token');
  assert.deepEqual(JSON.parse(request.options.body), {
    decision: 'REJECT', reviewer: 'QA Operator', rejection_reason: 'Operational issue'});
  for (const control of [x.note1, x.note2, x.reviewer, x.reason, x.decision, x.button]) {
    assert.equal(control.disabled, true);
  }
  assert.equal(x.slot.child.href, '/fixture.pdf');
});

test('changing reviewer or rejection reason resets confirmation', async () => {
  const x = setup();
  await x.listeners.click({target: x.button});
  x.reason.value = 'Changed reason';
  x.listeners.input({target: x.reason});
  await x.listeners.click({target: x.button});
  assert.equal(x.calls.length, 0);
  await x.listeners.click({target: x.button});
  assert.equal(JSON.parse(x.calls.at(-1).options.body).rejection_reason, 'Changed reason');
});

test('frozen snapshot retry skips note mutation', async () => {
  const x = setup({readonly: true});
  await x.listeners.click({target: x.button});
  await x.listeners.click({target: x.button});
  assert.equal(x.calls.length, 1);
  assert.equal(x.calls[0].url, '/api/runs/fixture/finalize');
});

test('failed policy validation leaves retry available and no PDF link', async () => {
  const x = setup({fail: true});
  await x.listeners.click({target: x.button});
  await x.listeners.click({target: x.button});
  assert.equal(x.button.disabled, false);
  assert.equal(x.slot.child, undefined);
});

test('rejection without reason makes no network request', async () => {
  const x = setup();
  x.reason.value = '';
  await x.listeners.click({target: x.button});
  assert.equal(x.calls.length, 0);
});

test('PDF failure freezes editors immediately so same-page retry reaches finalization', async () => {
  const x = setup({fail: true, freeze: true});
  await x.listeners.click({target: x.button});
  await x.listeners.click({target: x.button});
  assert.equal(x.note1.disabled, true);
  assert.equal(x.button.disabled, false);
  await x.listeners.click({target: x.button});
  assert.equal(x.calls.filter(c => c.url === '/note/1').length, 1);
  assert.equal(x.calls.filter(c => c.url.endsWith('/finalize')).length, 2);
});
