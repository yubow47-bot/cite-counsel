// Run with node --test tests/inquiry_ui.cjs. No network or browser dependencies.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../plugins/inquiry/ui.js'), 'utf8');

class Element {
  constructor(tag, className = '', text = '') { this.tag = tag; this.className = className; this._text = text; this.children = []; this.value = ''; this.open = false; this.disabled = false; }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent || '').join(''); }
  setAttribute(key, value) { this[key] = value; }
  click() { return this.onclick?.(); }
}
const descendants = (node, predicate) => [node, ...node.children.flatMap(child => descendants(child, predicate))].filter(predicate);
const byClass = (node, name) => descendants(node, item => item.className?.split(' ').includes(name));
const button = (node, text) => descendants(node, item => item.tag === 'button' && item.textContent === text)[0];
const flush = async () => { for (let i = 0; i < 6; i++) await new Promise(resolve => setImmediate(resolve)); };
function setup() {
  const panel = new Element('section'), requests = [], prepared = [], toasts = [], listeners = new Map(), renderers = new Map(), events = new Map();
  let hasSession = false;
  const emit = block => listeners.get(block.type)?.(block);
  const api = {
    el: (tag, cls, text) => new Element(tag, cls, text), registerPanel: () => panel,
    hasSession: () => hasSession, busy: () => false, toast: text => toasts.push(text),
    on: (plugin, type, fn) => listeners.set(type, fn), registerBlock: (plugin, type, fn) => renderers.set(type, fn),
    prepareMessage: text => prepared.push(text), openMaterial: () => Promise.resolve(),
    action: (plugin, action, payload, options = {}) => new Promise((resolve, reject) => requests.push({plugin, action, payload, options, reject, resolve: blocks => {
      if (options.isCurrent && !options.isCurrent()) { resolve([]); return; }
      blocks.forEach(emit); resolve(blocks);
    }})),
  };
  const document = {addEventListener: (name, fn) => events.set(name, fn)};
  vm.runInNewContext(source, {window: {Harness: api}, document, setTimeout, clearTimeout, URL, Blob}, {filename: 'inquiry/ui.js'});
  return {panel, requests, prepared, toasts, emit, render: block => renderers.get(block.type)(block), start: () => { hasSession = true; events.get('harness:session-restored')(); }, event: name => events.get(name)()};
}
const report = (ref, fields = {}) => ({type: 'inquiry_report', ref, object: {question: `调查 ${ref}`}, readiness: 'open', ...fields});
const directory = (refs, activeRef = null) => ({type: 'inquiry_directory', query: '', offset: 0, total: refs.length, next_offset: null, active_ref: activeRef, items: refs.map(ref => ({ref, question: `问题 ${ref}`, readiness: 'open'}))});
const premise = (id, fields = {}) => ({id, statement: `${id} 是否成立`, if_true: '成立时适用规则 A', if_false: '不成立时改查规则 B', next_check: '检查对应记录', method: 'source', status: 'unknown', ...fields});

test('premise branches stay explicit and model-supported status is not presented as certainty', () => {
  const e = setup();
  const block = report('art_1', {premises: [premise('p1', {status: 'supported'}), premise('p2'), premise('p3', {status: 'supported', needs_recheck: true}), premise('p4', {status: 'refuted'})]});
  e.emit(block);
  const focus = byClass(e.panel, 'inquiry-focus')[0], cards = byClass(focus, 'inquiry-premise');
  assert.equal(cards.length, 4); assert.equal(cards.filter(card => card.open).length, 1);
  assert.match(cards[0].textContent, /p3.*待重查/); assert.equal(cards[0].open, true);
  assert.match(focus.textContent, /如果成立.*规则 A.*如果不成立.*规则 B/);
  assert.match(focus.textContent, /有依据 · 模型判断/); assert.match(focus.textContent, /有反证 · 模型判断/);
  const full = e.render(block);
  assert.equal(byClass(full, 'inquiry-premise').filter(card => card.open).length, 2);
});

test('next-step buttons prepare scoped editable prompts without executing tools', () => {
  const e = setup(); e.emit(report('art_2', {premises: [premise('p_source'), premise('p_user', {method: 'user'})]}));
  button(e.panel, '继续求证').click();
  assert.match(e.prepared[0], /art_2.*p_source/); assert.match(e.prepared[0], /不把可能解释写成确定事实/);
  button(e.panel, '补充这个事实').click();
  assert.match(e.prepared[1], /p_user/); assert.match(e.prepared[1], /请填写事实/);
  assert.equal(e.requests.length, 0);
});

test('new materials are visibly pending and only affected arguments are targeted for recheck', () => {
  const e = setup(); e.emit(report('art_3', {premises: [premise('p1')], affected_claim_refs: ['art_claim2'], pending_materials: [{ref: 'rec_new'}]}));
  assert.match(e.panel.textContent, /1 份新材料待关联/); assert.match(e.panel.textContent, /尚未判断这些材料是否影响/);
  button(e.panel, '检查新材料的影响').click();
  assert.match(e.prepared[0], /rec_new.*现有前提 p1/); assert.match(e.prepared[0], /不要仅因材料新增就认定前提成立/);
  button(e.panel, '重查受影响结论').click(); assert.match(e.prepared[1], /只重查受影响的论点 art_claim2/);
});

test('an old same-session inspect cannot replace a newer selected question', async () => {
  const e = setup(); e.emit(directory(['art_1', 'art_2']));
  const rows = byClass(e.panel, 'inquiry-item'); rows[0].click(); rows[1].click();
  assert.match(e.panel.textContent, /正在打开调查/);
  e.requests[1].resolve([report('art_2')]); await flush();
  e.requests[0].resolve([report('art_1')]); await flush();
  const focus = byClass(e.panel, 'inquiry-focus')[0];
  assert.match(focus.textContent, /当前问题 · art_2/); assert.doesNotMatch(focus.textContent, /art_1/);
  assert.equal(e.panel['aria-busy'], 'false');
});

test('a late list cannot reset a question selected while the list was loading', async () => {
  const e = setup(); e.emit(directory(['art_2'])); e.start();
  byClass(e.panel, 'inquiry-item')[0].click();
  e.requests[0].resolve([directory(['art_1', 'art_2'], 'art_1')]); await flush();
  assert.equal(e.requests.length, 2);
  e.requests[1].resolve([report('art_2')]); await flush();
  assert.match(byClass(e.panel, 'inquiry-focus')[0].textContent, /当前问题 · art_2/);
});

test('repeated lists with the same query keep the latest result and a session switch invalidates pending reports', async () => {
  const e = setup(); e.start(); e.event('harness:materials-changed');
  e.requests[1].resolve([directory(['art_new'])]); await flush();
  e.requests[0].resolve([directory(['art_old'])]); await flush();
  assert.match(byClass(e.panel, 'inquiry-directory')[0].textContent, /art_new/);
  assert.doesNotMatch(byClass(e.panel, 'inquiry-directory')[0].textContent, /art_old/);
  byClass(e.panel, 'inquiry-item')[0].click(); e.event('harness:new-session');
  e.requests[2].resolve([report('art_new')]); await flush();
  assert.equal(byClass(e.panel, 'inquiry-focus')[0].textContent, '');
});

test('current action failures are visible and release busy state; stale failures stay quiet', async () => {
  const e = setup(); e.emit(directory(['art_1', 'art_2']));
  const rows = byClass(e.panel, 'inquiry-item'); rows[0].click(); rows[1].click();
  e.requests[0].reject(new Error('old error')); await flush(); assert.equal(e.toasts.length, 0);
  e.requests[1].reject(new Error('来源暂不可用')); await flush();
  assert.match(byClass(e.panel, 'inquiry-feedback')[0].textContent, /来源暂不可用/);
  assert.equal(e.panel['aria-busy'], 'false'); assert.equal(e.toasts.length, 1);
});

test('affected assessment cannot keep showing the original supported verdict', () => {
  const e = setup(); const card = e.render(report('art_4', {assessments: [{claim_ref: 'art_claim', verdict: 'supported', effective_verdict: 'inconclusive', needs_recheck: true}]}));
  assert.match(card.textContent, /模型判断：待重查/); assert.doesNotMatch(card.textContent, /模型判断：有依据/);
});

test('a changed scope leaves the old saved answer visibly pending even without a targeted claim', () => {
  const e = setup(); const block = report('art_5', {answer_needs_refresh: true, object: {question: '范围已改变', answer: {text: '先前的结论'}}});
  e.emit(block);
  assert.match(e.panel.textContent, /已保存的答复需要更新/);
  const card = e.render(block);
  assert.match(card.textContent, /先前答复 · 待更新.*先前的结论/);
});

test('premise support distinguishes user testimony from externally bound material', () => {
  const e = setup();
  e.emit(report('art_6', {premises: [
    premise('p_user', {status: 'supported', method: 'user', basis: 'user_reported', user_quote: '我在本地零售店工作。'}),
    premise('p_source', {status: 'supported', basis: 'source_bound', evidence_refs: ['rec_1']}),
    premise('p_unknown', {basis: 'unestablished'})
  ]}));
  const cards = byClass(e.panel, 'inquiry-premise');
  const user = cards.find(card => card.textContent.includes('p_user'));
  const source = cards.find(card => card.textContent.includes('p_source'));
  const unknown = cards.find(card => card.textContent.includes('p_unknown'));
  assert.match(user.textContent, /用户提供 · 尚未外部核实/);
  assert.match(user.children[0].textContent, /用户提供 · 尚未外部核实/); // Visible even while the premise is collapsed.
  assert.match(user.textContent, /用户原话：“我在本地零售店工作。”/);
  assert.doesNotMatch(user.textContent, /已关联实际材料/);
  assert.match(source.textContent, /已关联实际材料 · 支持关系仍为模型判断/);
  assert.doesNotMatch(source.textContent, /用户原话/);
  assert.equal(byClass(unknown, 'inquiry-premise-basis').length, 0);
});
