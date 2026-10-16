// Run with node --test tests/ui_races.cjs. No browser, network or npm dependencies.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../harness/web/harness.js'), 'utf8');

class Element {
  constructor(tag = '') { this.tag = tag; this.children = []; this.value = ''; this.style = {}; this.dataset = {}; this.events = {}; this.hidden = false; this.open = false; this._text = ''; const classes = new Set(); this.classList = {add: (...v) => v.forEach(x => classes.add(x)), remove: (...v) => v.forEach(x => classes.delete(x)), contains: x => classes.has(x), toggle: (x, force) => { const on = force ?? !classes.has(x); on ? classes.add(x) : classes.delete(x); return on; }}; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = [...items]; this._text = ''; }
  get childElementCount() { return this.children.length; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent || '').join(''); }
  setAttribute(key, value) { this[key] = value; }
  addEventListener(name, fn) { this.events[name] = fn; }
  close() { if (this.open) { this.open = false; this.events.close?.(); } }
  showModal() { this.open = true; }
  click() { this.onclick?.(); }
  focus() {}
  remove() {}
  scrollIntoView() {}
}
const flush = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
function setup(initial = null, savedEntries = null) {
  const elements = new Map(), requests = [], storage = new Map();
  const a = {id: 'A', token: 'token-A', title: 'Conversation A'}, b = {id: 'B', token: 'token-B', title: 'Conversation B'};
  storage.set('citecounsel.saved.v1', JSON.stringify(savedEntries || [a, b]));
  if (initial) storage.set('citecounsel.session.v1', JSON.stringify(initial === 'A' ? a : b));
  const document = new Element('document'); document.body = new Element('body'); document.head = new Element('head');
  document.getElementById = id => { if (!elements.has(id)) elements.set(id, new Element(id)); return elements.get(id); };
  document.createElement = tag => new Element(tag); document.createTextNode = text => { const e = new Element(); e.textContent = text; return e; };
  document.querySelectorAll = () => []; document.dispatchEvent = event => document.events[event.type]?.(event);
  document.getElementById('material-kind').value = 'record';
  const window = {innerWidth: 1200, addEventListener() {}};
  const context = {document, window, console, Event: class {constructor(type) { this.type = type; }}, localStorage: {getItem: k => storage.get(k), setItem: (k, v) => storage.set(k, v), removeItem: k => storage.delete(k)}, setTimeout: () => 0, navigator: {}, URL, Blob, AbortController, TextDecoder,
    FormData: class {constructor() {this.values = new Map();} append(k, v) {this.values.set(k, v);} get(k) {return this.values.get(k);}},
    fetch: (url, opts) => { if (url === '/api/config') return Promise.resolve({json: async () => ({plugins: [], llm_configured: false})}); return new Promise(resolve => requests.push({url, body: opts?.body, resolve: data => resolve({ok: true, json: async () => data})})); }};
  vm.runInNewContext(source, context, {filename: 'harness.js'});
  return {elements, requests, storage, document, window};
}
const material = ref => ({ref, field: 'text', text: ref + ' source text', available_fields: ['text'], offset: 0, total_chars: 20, next_offset: null, origin: 'extracted'});

test('a delayed resume cannot refill a newly opened conversation', async () => {
  const e = setup('A'); await flush();
  assert.equal(e.requests[0].url, '/api/sessions/resume');
  e.elements.get('new-chat').click();
  e.requests[0].resolve({timeline: [{role: 'user', text: 'Old research'}]}); await flush();
  assert.equal(e.elements.get('transcript').childElementCount, 0);
  assert.equal(e.storage.has('citecounsel.session.v1'), false);
});

test('overlapping restores keep the latest selected conversation', async () => {
  const e = setup('A'); await flush();
  e.elements.get('saved-chats').value = 'B'; e.elements.get('saved-chats').onchange(); await flush();
  e.requests[1].resolve({timeline: [{role: 'user', text: 'Current B'}]}); await flush();
  const directory = e.requests.find(r => r.url === '/api/materials'); directory.resolve({items: [], total: 0, next_offset: null}); await flush();
  e.requests[0].resolve({timeline: [{role: 'user', text: 'Stale A'}]}); await flush();
  assert.match(e.elements.get('transcript').textContent, /Current B/);
  assert.doesNotMatch(e.elements.get('transcript').textContent, /Stale A/);
  assert.equal(JSON.parse(e.storage.get('citecounsel.session.v1')).id, 'B');
});

test('queued uploads create one session and prevent switching while pending', async () => {
  const e = setup(); await flush();
  for (const name of ['one.pdf', 'two.pdf']) e.document.events.drop({preventDefault() {}, dataTransfer: {files: [{name}]}});
  await flush(); assert.equal(e.requests.length, 1); assert.equal(e.elements.get('new-chat').disabled, true);
  e.elements.get('new-chat').click();
  e.requests[0].resolve({session: {id: 'A', token: 'token-A'}, attachment: {id: 'att_1', name: 'one.pdf'}}); await flush();
  assert.equal(e.requests.length, 2); assert.equal(e.requests[1].body.get('session_id'), 'A');
  e.requests[1].resolve({session: {id: 'A', token: 'token-A'}, attachment: {id: 'att_2', name: 'two.pdf'}}); await flush();
  assert.equal(e.elements.get('chips').childElementCount, 2); assert.equal(e.elements.get('new-chat').disabled, false);
});

test('material responses stay within their view and request generation', async () => {
  const e = setup('A'); await flush();
  const first = e.window.Harness.openMaterial('rec_1'), second = e.window.Harness.openMaterial('rec_2');
  e.requests[2].resolve(material('rec_2')); await second;
  e.requests[1].resolve(material('rec_1')); await first;
  assert.equal(e.elements.get('material-title').textContent, 'rec_2');
  const stale = e.window.Harness.openMaterial('rec_3'); e.elements.get('new-chat').click();
  e.requests[3].resolve(material('rec_3')); await stale;
  assert.equal(e.elements.get('material-view').open, false);
});

test('record badges describe field provenance without implying legal verification', async () => {
  const e = setup(); await flush();
  e.window.Harness.render([{type: 'record_card', ref: 'rec_1', source_type: 'jurisprudence', verified: true, fields: {}},
                          {type: 'record_card', ref: 'rec_2', source_type: 'document', verified: false, fields: {}}]);
  const text = e.elements.get('transcript').textContent;
  assert.match(text, /Database-sourced fields/);
  assert.match(text, /Non-database fields/);
  assert.doesNotMatch(text, /\bverified\b/);
});

test('mobile workspace and research drawers have explicit close controls', async () => {
  const e = setup(); await flush(); e.window.innerWidth = 390;
  e.elements.get('toggle-workspace').click();
  assert.equal(e.document.body.classList.contains('show-workspace'), true);
  e.elements.get('close-workspace').click();
  assert.equal(e.document.body.classList.contains('show-workspace'), false);
  e.elements.get('toggle-research').click();
  assert.equal(e.document.body.classList.contains('show-panels'), true);
  e.elements.get('close-research').click();
  assert.equal(e.document.body.classList.contains('show-panels'), false);
  assert.equal(e.elements.get('drawer-backdrop').hidden, true);
  e.elements.get('toggle-research').click();
  e.elements.get('panels').events.click({target: {closest: () => ({})}});
  assert.equal(e.document.body.classList.contains('show-panels'), false);
});

test('opening another conversation cannot discard access to older saved investigations', async () => {
  const existing = Array.from({length: 35}, (_, index) => ({id: `old-${index}`, token: `saved-token-${index}`, title: `Saved ${index}`}));
  const e = setup(null, existing); await flush();
  e.document.events.drop({preventDefault() {}, dataTransfer: {files: [{name: 'new.pdf'}]}});
  await flush();
  e.requests[0].resolve({session: {id: 'new', token: 'new-token'}, attachment: {id: 'att_1', name: 'new.pdf'}});
  await flush();
  const saved = JSON.parse(e.storage.get('citecounsel.saved.v1'));
  assert.equal(saved.length, 36);
  assert.equal(saved.find(item => item.id === 'old-34').token, 'saved-token-34');
});

test('plugin next steps prepare an editable message without sending or replacing a draft', async () => {
  const e = setup(); await flush();
  const input = e.elements.get('input'); input.maxLength = 4000;
  assert.equal(e.window.Harness.prepareMessage('  请核对前提 p1。  '), true);
  assert.equal(input.value, '请核对前提 p1。');
  assert.equal(e.requests.length, 0);
  assert.equal(e.window.Harness.prepareMessage('替换草稿'), false);
  assert.equal(input.value, '请核对前提 p1。');
  assert.match(e.elements.get('toast').textContent, /已有草稿/);
  input.value = '';
  assert.equal(e.window.Harness.prepareMessage('x'.repeat(4001)), false);
  assert.equal(input.value, '');
  e.document.events.drop({preventDefault() {}, dataTransfer: {files: [{name: 'material.pdf'}]}});
  assert.equal(e.window.Harness.prepareMessage('上传时不插入'), false);
  assert.equal(input.value, '');
});

test('a plugin can discard an older same-session action before listeners or transcript update', async () => {
  const e = setup('A'); await flush();
  let current = true, observed = 0;
  e.window.Harness.on('inquiry', 'inquiry_report', () => { observed += 1; });
  const pending = e.window.Harness.action('inquiry', 'inspect', {ref: 'art_old'}, {quiet: true, isCurrent: () => current});
  current = false;
  e.requests[1].resolve({blocks: [{plugin: 'inquiry', type: 'inquiry_report', ref: 'art_old'}]});
  const result = await pending;
  assert.equal(result.length, 0);
  assert.equal(observed, 0);
  assert.equal(e.elements.get('transcript').childElementCount, 0);
});
