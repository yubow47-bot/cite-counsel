const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor(tag, className = '', text = '') { this.tag = tag; this.className = className; this.text = text || ''; this.children = []; }
  append(...children) { this.children.push(...children); }
  setAttribute() {}
  get textContent() { return this.text + this.children.map(child => child.textContent).join(''); }
}
const findAll = (node, predicate) => [node, ...node.children.flatMap(child => findAll(child, predicate))].filter(predicate);

test('comparison separates versions and prepares one-node revisions without executing changes', async () => {
  const renderers = new Map(), opened = [], prepared = [], actions = [];
  const api = {el: (tag, cls, text) => new Element(tag, cls, text), registerPanel: () => new Element('section'),
    registerBlock: (plugin, type, render) => renderers.set(type, render), on() {}, hasSession: () => false,
    openMaterial: (...args) => { opened.push(args); return Promise.resolve(); },
    prepareMessage: text => prepared.push(text), action: (...args) => actions.push(args)};
  const document = {addEventListener() {}, createTextNode: text => new Element('text', '', text)};
  vm.runInNewContext(fs.readFileSync(__dirname + '/../plugins/research/ui.js', 'utf8'), {window: {Harness: api}, document});
  const card = renderers.get('research_compare')({ref: 'rec_1', current_ref: 'rec_4', newer_version: true,
    fields: [{field: 'text', old_chars: 16, new_chars: 18,
      segments: [{op: 'equal', text: 'Common '}, {op: 'replace', old: 'old wording', new: 'new wording'}]}],
    affected: [{ref: 'art_3', type: 'claim', depth: 2, via: 'art_2', is_latest: true}], excerpts: []});
  const columns = name => findAll(card, node => node.className === name).map(node => node.textContent).join('');
  assert.match(columns('compare-before'), /Common old wording/);
  assert.doesNotMatch(columns('compare-before'), /new wording/);
  assert.match(columns('compare-after'), /Common new wording/);
  const buttons = findAll(card, node => node.tag === 'button');
  await buttons.find(node => node.textContent === 'Open earlier original').onclick();
  assert.deepEqual(opened[0], ['rec_1', 'text', 0, true]);
  buttons.find(node => node.textContent === 'Revise this item').onclick();
  assert.match(prepared[0], /art_3.*art_2.*rec_1.*rec_4/);
  assert.match(prepared[0], /prior_ref=art_3/);
  assert.deepEqual(actions, []);
});
