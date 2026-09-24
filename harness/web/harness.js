// The harness page: a conversation, a settings bar, and a small API that
// plugin UIs use to render their own blocks and, if they want, a panel.
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const renderers = new Map();     // "plugin:type" -> fn(block, api) -> Node | null
  const decorators = new Map();    // "plugin:type" -> [fn(block, node, api)]
  const listeners = new Map();     // "plugin:type" -> [fn(block)]
  const SESSION_KEY = 'citecounsel.session.v1';
  let session = null, busy = false, config = null, attachments = [];
  try { session = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null'); } catch { session = null; }

  // ── Helpers exposed to plugins ────────────────────────────────────
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function toast(message) {
    $('toast').textContent = message; $('toast').classList.add('show');
    setTimeout(() => $('toast').classList.remove('show'), 2600);
  }
  function saveSession(value) {
    session = value;
    try { value ? localStorage.setItem(SESSION_KEY, JSON.stringify(value)) : localStorage.removeItem(SESSION_KEY); } catch { /* private window */ }
  }
  function sessionRef() { return session ? {session_id: session.id, session_token: session.token} : {}; }
  async function post(url, body) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    if (data.session) saveSession(data.session);
    if (!response.ok || data.ok === false) throw new Error(data.message || 'The request did not complete.');
    return data;
  }

  // ── Rendering ─────────────────────────────────────────────────────
  // Inline text: **bold** and *italic*, as an array of nodes.
  function inline(text) {
    const out = [];
    for (const part of String(text).split(/(\*\*[^*]+\*\*)/g)) {
      if (/^\*\*[^*]+\*\*$/.test(part)) out.push(el('strong', '', part.slice(2, -2)));
      else out.push(...api.italic(part));
    }
    return out;
  }
  // The small source line under a reply: where each grounded fact came from.
  function sourceLine(sources) {
    const line = el('div', 'fact-sources');
    line.append(el('span', 'label', 'Sources'));
    const seen = new Set();
    let shown = 0;
    for (const f of sources) {
      const key = (f.ref || f.kind || '?') + ':' + (f.field || '');
      if (seen.has(key)) continue;
      seen.add(key);
      if (shown >= 6) { line.append(el('span', 'fact-source', `+${sources.length - shown} more`)); break; }
      const chip = el('span', 'fact-source', f.ref ? `${f.ref} · ${f.field}` : (f.kind === 'user' ? 'your message' : 'tool result'));
      if (f.excerpt) chip.title = f.excerpt;
      line.append(chip);
      shown += 1;
    }
    return line;
  }
  function generic(block) {
    switch (block.type) {
      case 'text': {
        const node = el('div', 'bubble assistant');
        const text = String(block.text ?? '');
        const facts = Array.isArray(block.facts) ? block.facts : [];
        const marks = [], sources = [];
        for (const f of facts) {
          if (!f || typeof f !== 'object') continue;
          if (f.verdict === 'unsourced') for (const span of f.spans || []) marks.push({start: span[0], end: span[1], fact: f.fact});
          else sources.push(f);
        }
        marks.sort((a, b) => a.start - b.start || b.end - a.end);
        const merged = [];
        for (const m of marks) {
          const last = merged[merged.length - 1];
          if (last && m.start < last.end) { last.end = Math.max(last.end, m.end); if (!last.facts.includes(m.fact)) last.facts.push(m.fact); }
          else merged.push({start: m.start, end: m.end, facts: [m.fact]});
        }
        let at = 0;
        for (const m of merged) {
          if (m.start > at) node.append(...inline(text.slice(at, m.start)));
          const mark = el('mark', 'unsourced', text.slice(m.start, m.end));
          mark.title = 'No source found for: ' + m.facts.join(', ');
          node.append(mark);
          at = m.end;
        }
        if (at < text.length) node.append(...inline(text.slice(at)));
        if (sources.length) node.append(sourceLine(sources));
        return node;
      }
      case 'notice': return el('div', 'bubble notice ' + (block.level || 'info'), block.text || block.message);
      case 'activity': {
        // The chip names the plugin and tool by id — short and stable.
        return el('div', 'activity' + (block.error ? ' failed' : ''),
                  (block.error ? '✕ ' : '⚙ ') + (block.plugin || '') + (block.tool ? ' · ' + block.tool : ''));
      }
      case 'card': {
        const card = el('section', 'bubble card');
        if (block.title) card.append(el('h3', '', block.title));
        if (Array.isArray(block.rows)) {
          const table = el('dl', 'rows');
          for (const [k, v] of block.rows) {
            const dd = el('dd', '');
            dd.append(...inline(v == null ? '' : String(v)));
            table.append(el('dt', '', k), dd);
          }
          card.append(table);
        }
        if (block.body) card.append(el('p', '', block.body));
        if (block.note) card.append(el('p', 'note', block.note));
        return card;
      }
      case 'record_card': {
        // A stored record: one row per field, plus the ref and its verdict.
        const card = el('section', 'bubble card record');
        const head = el('h3', 'record-head');
        head.append(el('span', 'record-type', block.source_type || 'record'), el('span', 'record-ref', block.ref || ''));
        if (typeof block.verified === 'boolean') head.append(el('span', 'badge ' + (block.verified ? 'ok' : 'warn'),
                                                                 block.verified ? 'verified' : 'unverified'));
        card.append(head);
        const table = el('dl', 'rows');
        for (const [k, v] of Object.entries(block.fields || {})) {
          const value = String(v ?? '');
          table.append(el('dt', '', k), el('dd', '', value.length > 280 ? value.slice(0, 280) + '…' : value));
        }
        card.append(table);
        return card;
      }
      default: return el('div', 'bubble notice warning', `This result from “${block.plugin || '?'}” could not be rendered.`);
    }
  }
  function renderBlock(block) {
    if (!block || typeof block !== 'object') return null;
    const key = (block.plugin || '') + ':' + block.type;
    (listeners.get(key) || []).forEach(fn => { try { fn(block); } catch (error) { console.error(error); } });
    const custom = renderers.get(key);
    let node = null;
    if (custom) {
      try { node = custom(block, api); } catch (error) { console.error(error); node = generic({type: 'notice', level: 'error', text: 'Plugin UI error.'}); }
    } else node = generic(block);
    if (!node) return null;   // a plugin may route a block to its panel only
    for (const fn of decorators.get(key) || []) { try { fn(block, node, api); } catch (error) { console.error(error); } }
    return node;
  }
  function show(blocks) {
    $('empty').hidden = true;
    const turn = el('article', 'turn assistant-turn');
    for (const block of blocks || []) { const node = renderBlock(block); if (node) turn.append(node); }
    if (turn.childElementCount) { $('transcript').append(turn); turn.scrollIntoView({block: 'end', behavior: 'smooth'}); }
  }
  function setBusy(value) {
    busy = value; $('send').disabled = value; $('input').disabled = value;
    document.body.classList.toggle('busy', value);
  }

  // ── The plugin API ────────────────────────────────────────────────
  const api = {
    el, toast, post,
    registerBlock(plugin, type, fn) { renderers.set(plugin + ':' + type, fn); },
    decorate(plugin, type, fn) { const key = plugin + ':' + type; decorators.set(key, [...(decorators.get(key) || []), fn]); },
    on(plugin, type, fn) { const key = plugin + ':' + type; listeners.set(key, [...(listeners.get(key) || []), fn]); },
    // A panel lives in the right-hand column, which exists only when some
    // plugin asks for one.
    registerPanel(plugin, title) {
      $('panels').hidden = false; document.body.classList.add('has-panels');
      const panel = el('section', 'panel'); panel.dataset.plugin = plugin;
      panel.append(el('h2', '', title));
      const body = el('div', 'panel-body'); panel.append(body);
      $('panels').append(panel);
      return body;
    },
    // A button in a plugin's UI: deterministic, no model. Returned blocks are
    // shown in the conversation unless {quiet: true}.
    async action(plugin, name, payload = {}, {quiet = false} = {}) {
      if (!session) throw new Error('No conversation yet.');
      const data = await post(`/api/actions/${plugin}/${name}`, {...sessionRef(), payload});
      if (quiet) (data.blocks || []).forEach(block => (listeners.get((block.plugin || '') + ':' + block.type) || []).forEach(fn => fn(block)));
      else show(data.blocks);
      return data.blocks || [];
    },
    hasSession() { return !!session; },
    italic(text) {
      return String(text).split(/(\*[^*]+\*)/g).filter(Boolean).map(part =>
        part.length > 2 && part.startsWith('*') && part.endsWith('*') ? el('em', '', part.slice(1, -1)) : document.createTextNode(part));
    },
    italicHtml(text) {
      const esc = s => s.replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
      return String(text).split(/(\*[^*]+\*)/g).map(part =>
        part.length > 2 && part.startsWith('*') && part.endsWith('*') ? '<i>' + esc(part.slice(1, -1)) + '</i>' : esc(part)).join('');
    },
    async copyRich(html, plain, done = 'Copied.') {
      try {
        if (window.ClipboardItem) await navigator.clipboard.write([new ClipboardItem({'text/html': new Blob([html], {type: 'text/html'}), 'text/plain': new Blob([plain], {type: 'text/plain'})})]);
        else await navigator.clipboard.writeText(plain);
        toast(done);
      } catch { toast('Copy failed — select the text and copy manually.'); }
    },
    busy: () => busy,
    render: show,   // render a list of blocks into the transcript (also for tests)
  };
  window.Harness = api;

  // ── Conversation ──────────────────────────────────────────────────
  function userBubble(text, names) {
    $('empty').hidden = true;
    const turn = el('article', 'turn user-turn');
    const bubble = el('div', 'bubble user', text);
    if (names.length) bubble.append(el('small', 'attached', 'Attached: ' + names.join(', ')));
    turn.append(bubble); $('transcript').append(turn);
  }
  async function send() {
    const text = $('input').value.trim();
    if (busy || (!text && !attachments.length)) return;
    const used = attachments; attachments = []; renderChips();
    $('input').value = ''; grow();
    userBubble(text, used.map(a => a.name));
    setBusy(true);
    const typing = el('div', 'typing', 'Working…'); $('transcript').append(typing);
    typing.scrollIntoView({block: 'end'});
    try {
      const data = await post('/api/turns', {...sessionRef(), input: text, attachments: used.map(a => a.id)});
      typing.remove(); show(data.blocks);
    } catch (error) {
      typing.remove(); show([{type: 'notice', level: 'error', text: error.message}]);
    } finally { setBusy(false); $('input').focus(); }
  }
  async function upload(file) {
    if (!file) return;
    const body = new FormData(); body.append('file', file);
    if (session) { body.append('session_id', session.id); body.append('session_token', session.token); }
    try {
      const response = await fetch('/api/files', {method: 'POST', body});
      const data = await response.json();
      if (data.session) saveSession(data.session);
      if (!response.ok || data.ok === false) throw new Error(data.message || 'Upload failed.');
      attachments.push(data.attachment); renderChips();
    } catch (error) { toast(error.message); }
  }
  function renderChips() {
    $('chips').replaceChildren(...attachments.map((a, i) => {
      const chip = el('span', 'chip', a.name);
      const x = el('button', '', '×'); x.type = 'button'; x.onclick = () => { attachments.splice(i, 1); renderChips(); };
      chip.append(x); return chip;
    }));
  }
  function grow() { const i = $('input'); i.style.height = 'auto'; i.style.height = Math.min(i.scrollHeight, 200) + 'px'; }

  // ── Settings bar ──────────────────────────────────────────────────
  function renderSettings() {
    $('llm-dot').className = 'dot ' + (config.llm_configured ? 'on' : 'off');
    $('llm-status').textContent = config.llm_configured ? 'API key configured' : 'No API key';
    $('model').value = config.model || '';
    $('plugins').replaceChildren(...config.plugins.map(plugin => {
      const card = el('div', 'plugin' + (plugin.enabled ? ' enabled' : ''));
      const head = el('label', 'plugin-head');
      const toggle = el('input'); toggle.type = 'checkbox'; toggle.checked = plugin.enabled;
      toggle.onchange = async () => {
        try { config = {...config, ...(await post('/api/settings/plugins/' + plugin.name, {enabled: toggle.checked}))}; location.reload(); }
        catch (error) { toggle.checked = !toggle.checked; toast(error.message); }
      };
      head.append(toggle, el('b', '', plugin.title));
      card.append(head, el('p', 'desc', plugin.description));
      if (plugin.requires.length) card.append(el('p', 'meta', 'Requires: ' + plugin.requires.join(', ')));
      const details = el('details', 'tools');
      details.append(el('summary', '', `${plugin.tools.length} tools`));
      for (const tool of plugin.tools) details.append(el('p', 'meta', tool.name + ' — ' + tool.description));
      card.append(details);
      for (const setting of plugin.settings) card.append(settingField(plugin, setting));
      return card;
    }));
  }
  function settingField(plugin, setting) {
    const row = el('label', 'setting'); row.append(el('span', '', setting.label));
    let input;
    if (setting.kind === 'choice') {
      input = el('select');
      for (const choice of setting.choices) { const o = el('option', '', (setting.labels || {})[choice] || choice || '—'); o.value = choice; input.append(o); }
      input.value = setting.value ?? '';
    } else if (setting.kind === 'bool') { input = el('input'); input.type = 'checkbox'; input.checked = !!setting.value; }
    else { input = el('input'); input.type = setting.kind === 'secret' ? 'password' : 'text';
      input.placeholder = setting.kind === 'secret' ? (setting.set ? 'Set (hidden)' : 'Not set') : ''; if (setting.kind !== 'secret') input.value = setting.value ?? ''; }
    input.onchange = async () => {
      const value = setting.kind === 'bool' ? input.checked : input.value;
      try { await post('/api/settings/plugins/' + plugin.name, {settings: {[setting.name]: value}}); toast('Saved.'); }
      catch (error) { toast(error.message); }
    };
    row.append(input);
    if (setting.help) row.append(el('small', '', setting.help));
    return row;
  }
  function loadPluginUi() {
    for (const plugin of config.plugins) {
      if (!plugin.enabled || !plugin.ui) continue;
      const style = el('link'); style.rel = 'stylesheet'; style.href = `/plugins/${plugin.name}/ui.css`; document.head.append(style);
      const script = el('script'); script.src = `/plugins/${plugin.name}/ui.js`; document.body.append(script);
    }
  }
  async function loadConfig() {
    try {
      const response = await fetch('/api/config');
      config = await response.json();
      renderSettings(); loadPluginUi();
    } catch { $('llm-status').textContent = 'Cannot reach the local server'; }
  }

  $('composer').onsubmit = event => { event.preventDefault(); send(); };
  $('input').oninput = grow;
  $('input').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } };
  $('attach').onclick = () => $('file').click();
  $('file').onchange = () => { upload($('file').files[0]); $('file').value = ''; };
  $('new-chat').onclick = () => { saveSession(null); $('transcript').replaceChildren(); $('empty').hidden = false; document.dispatchEvent(new Event('harness:new-session')); };
  $('model-form').onsubmit = async event => {
    event.preventDefault();
    try { config = {...config, ...(await post('/api/settings/model', {model: $('model').value.trim()}))}; renderSettings(); toast('Model saved.'); }
    catch (error) { toast(error.message); }
  };
  $('key-form').onsubmit = async event => {
    event.preventDefault();
    try { config = {...config, ...(await post('/api/settings/keys', {openrouter_api_key: $('api-key').value}))}; $('api-key').value = ''; renderSettings(); toast('API key saved to the local .env.'); }
    catch (error) { toast(error.message); }
  };
  document.addEventListener('dragover', event => event.preventDefault());
  document.addEventListener('drop', event => { event.preventDefault(); upload(event.dataTransfer.files[0]); });
  loadConfig();
})();
