// The harness page: a conversation, a settings bar, and a small API that
// plugin UIs use to render their own blocks and, if they want, a panel.
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const renderers = new Map();     // "plugin:type" -> fn(block, api) -> Node | null
  const decorators = new Map();    // "plugin:type" -> [fn(block, node, api)]
  const listeners = new Map();     // "plugin:type" -> [fn(block)]
  const SESSION_KEY = 'citecounsel.session.v1';
  const SAVED_KEY = 'citecounsel.saved.v1';
  const loadedUis = new Set();
  let session = null, busy = false, config = null, attachments = [];
  let saved = [], pendingTitle = '', materialCursor = null, materialPage = null;
  let streamController = null;
  let viewEpoch = 0, restoring = false, uploadsPending = 0;
  let materialGeneration = 0, directoryGeneration = 0, findGeneration = 0;
  let uploadQueue = Promise.resolve();
  try { session = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null'); } catch { session = null; }
  try { saved = JSON.parse(localStorage.getItem(SAVED_KEY) || '[]'); if (!Array.isArray(saved)) saved = []; } catch { saved = []; }

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
    if (value) {
      const old = saved.find(s => s.id === value.id);
      saved = [{...value, title: old?.title || pendingTitle.slice(0, 60) || 'Saved conversation'}, ...saved.filter(s => s.id !== value.id)];
      storeSaved();
    }
    renderSaved();
  }
  function storeSaved() { try { localStorage.setItem(SAVED_KEY, JSON.stringify(saved)); } catch { /* private window */ } }
  function renderSaved() {
    $('saved-chats').replaceChildren();
    const first = el('option', '', 'Select a conversation'); first.value = ''; $('saved-chats').append(first);
    for (const item of saved) { const option = el('option', '', item.title || 'Saved conversation'); option.value = item.id; $('saved-chats').append(option); }
    $('saved-chats').value = session?.id || '';
    $('conversation-title').textContent = saved.find(item => item.id === session?.id)?.title || 'New conversation';
    updateControls();
  }
  function sessionRef() { return session ? {session_id: session.id, session_token: session.token} : {}; }
  async function post(url, body) {
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const data = await response.json().catch(() => ({}));
    // Callers own session transitions. A delayed response must never switch
    // the global conversation after the user has already opened another one.
    if (!response.ok || data.ok === false) throw new Error(data.message || 'The request did not complete.');
    return data;
  }
  // Server-sent events: each frame is "data: {...json...}\n\n". onEvent gets
  // one parsed {type, ...} object per frame, as soon as it arrives -- not
  // buffered until the whole response is in, which is the point of this
  // over post() for a turn (thinking shown live, not just the final reply).
  async function postStream(url, body, onEvent) {
    streamController = new AbortController();
    const response = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal: streamController.signal});
    if (!response.ok || !response.body) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.message || 'The request did not complete.');
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let completed = false;
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      let split;
      while ((split = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, split); buffer = buffer.slice(split + 2);
        const line = frame.split('\n').find(l => l.startsWith('data:'));
        if (!line) continue;
        let event;
        try { event = JSON.parse(line.slice(5).trim()); } catch { throw new Error('The server sent a malformed stream frame.'); }
        if (event.type === 'done') completed = true;
        onEvent(event);
      }
    }
    if (!completed) throw new Error('The connection ended before this run completed. Saved results can be resumed.');
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
      const chip = el(f.snapshot ? 'button' : 'span', 'fact-source', f.ref ? `${f.ref} · ${f.field}` : (f.kind === 'user' ? 'your message' : 'tool result'));
      if (f.excerpt) chip.title = f.excerpt;
      if (f.snapshot) chip.addEventListener('click', () => api.openSource(f.ref).catch(error => toast(error.message)));
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
      case 'thinking': {
        // Open by default: how the model decided what to do is part of the answer.
        const details = el('details', 'thinking');
        details.open = true;
        details.append(el('summary', '', 'Thinking'));
        const body = el('div', 'thinking-text');
        body.append(...inline(String(block.text ?? '')));
        details.append(body);
        return details;
      }
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
        const sourceGrounded = block.source_grounded ?? block.verified;
        if (typeof sourceGrounded === 'boolean') {
          const badge = el('span', 'badge ' + (sourceGrounded ? 'ok' : 'warn'),
                           sourceGrounded ? 'Database-sourced fields' : 'Non-database fields');
          badge.title = 'Field provenance only. Legal accuracy, authority and applicability have not been assessed.';
          head.append(badge);
        }
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
    busy = value; updateControls();
  }
  function updateControls() {
    const loading = restoring || uploadsPending > 0;
    $('send').disabled = busy || loading; $('input').disabled = busy || loading;
    document.body.classList.toggle('busy', busy);
    $('stop').hidden = !busy;
    $('new-chat').disabled = busy || uploadsPending > 0;
    $('saved-chats').disabled = busy || uploadsPending > 0;
    $('delete-chat').disabled = busy || loading || !session;
    $('attach').disabled = busy || restoring;
    $('session-state').textContent = restoring ? 'Opening saved conversation…' : uploadsPending ? `Uploading ${uploadsPending} file${uploadsPending > 1 ? 's' : ''}…` : busy ? 'Research in progress. Saved results stay in this workspace.' : 'Materials and research stay with this conversation.';
  }
  function closeDrawers() {
    document.body.classList.remove('show-workspace', 'show-panels');
    $('workspace').inert = window.innerWidth <= 900;
    $('panels').inert = window.innerWidth <= 900 || document.body.classList.contains('research-collapsed');
    $('drawer-backdrop').hidden = true;
    $('toggle-workspace').setAttribute('aria-expanded', 'false');
    $('toggle-research').setAttribute('aria-expanded', String(!document.body.classList.contains('research-collapsed') && window.innerWidth > 900));
  }
  function beginSession(value) {
    viewEpoch += 1; materialGeneration += 1; directoryGeneration += 1; findGeneration += 1;
    restoring = false; saveSession(value); attachments = []; renderChips();
    $('transcript').replaceChildren(); $('empty').hidden = false;
    $('material-view').close(); materialPage = null; $('material-hits').replaceChildren();
    $('materials').replaceChildren(); $('more-materials').hidden = true; materialCursor = null;
    $('material-search').value = ''; closeDrawers();
    document.dispatchEvent(new Event('harness:new-session'));
    return viewEpoch;
  }

  // ── The plugin API ────────────────────────────────────────────────
  const api = {
    el, toast, post,
    // Prepare an editable next step. Plugins never send it or replace a draft.
    prepareMessage(text) {
      const input = $('input'), value = String(text || '').trim();
      if (busy || restoring || uploadsPending) { toast('当前操作尚未完成，请稍后再准备下一步。'); return false; }
      if (input.value.trim()) { toast('输入框已有草稿，已保留。请先处理草稿，再选择下一步。'); input.focus(); return false; }
      if (!value || (input.maxLength > 0 && value.length > input.maxLength)) { toast('这条操作说明过长，请在输入框中简要说明下一步。'); return false; }
      input.value = value; grow(); closeDrawers(); input.focus();
      input.scrollIntoView({block: 'nearest', behavior: 'smooth'});
      toast('已填入输入框，可编辑后发送。');
      return true;
    },
    openMaterial: (ref, field = '', offset = 0, exact = false) => openMaterial(ref, field, offset, exact),
    async openSource(ref) {
      if (!session) throw new Error('No conversation yet.');
      const tab = window.open('about:blank', '_blank');
      try {
        const response = await fetch(`/api/sources/${encodeURIComponent(ref)}`, {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(sessionRef())
        });
        if (!response.ok) {
          const data = await response.json().catch(() => ({}));
          throw new Error(data.message || 'The source snapshot is unavailable.');
        }
        const url = URL.createObjectURL(new Blob([await response.arrayBuffer()], {type: 'text/plain;charset=utf-8'}));
        if (tab) tab.location.href = url;
        else { const a = el('a'); a.href = url; a.download = `${ref}.txt`; a.click(); }
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      } catch (error) { if (tab) tab.close(); throw error; }
    },
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
    async action(plugin, name, payload = {}, {quiet = false, isCurrent = () => true} = {}) {
      if (!session) throw new Error('No conversation yet.');
      const owner = session.id, epoch = viewEpoch;
      const data = await post(`/api/actions/${plugin}/${name}`, {...sessionRef(), payload});
      if (session?.id !== owner || viewEpoch !== epoch || !isCurrent()) return [];
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
    if (busy || restoring || uploadsPending || (!text && !attachments.length)) return;
    const used = attachments; attachments = []; renderChips();
    pendingTitle = text || used[0]?.name || 'Saved conversation';
    $('input').value = ''; grow();
    userBubble(text, used.map(a => a.name));
    setBusy(true);
    const typing = el('div', 'typing', 'Working…'); $('transcript').append(typing);
    typing.scrollIntoView({block: 'end'});
    // Built live as events arrive, not assembled after the fact: one turn
    // container, placed on the first thing that actually has something to
    // show (a thinking token or a block) so an empty bubble never appears.
    const turn = el('article', 'turn assistant-turn');
    let placed = false, thinkingBody = null, thinkingBuffer = '';
    const place = () => {
      if (!placed) { typing.remove(); $('transcript').append(turn); placed = true; }
      turn.scrollIntoView({block: 'end', behavior: 'smooth'});
    };
    try {
      await postStream('/api/turns/stream', {...sessionRef(), input: text, attachments: used.map(a => a.id)}, event => {
        if (event.type === 'session' && event.session) saveSession(event.session);
        else if (event.type === 'thinking_delta') {
          if (!thinkingBody) {
            const details = el('details', 'thinking'); details.open = true;
            details.append(el('summary', '', 'Thinking'));
            thinkingBody = el('div', 'thinking-text');
            details.append(thinkingBody);
            turn.append(details); place();
          }
          thinkingBuffer += event.text;
          thinkingBody.textContent = thinkingBuffer;
          thinkingBody.scrollTop = thinkingBody.scrollHeight;
        } else if (event.type === 'block') {
          const node = renderBlock(event.block);
          if (node) { turn.append(node); place(); }
        } else if (event.type === 'error') {
          turn.append(generic({type: 'notice', level: 'error', text: event.message})); place();
        }
      });
    } catch (error) {
      typing.remove();
      turn.append(generic({type: 'notice', level: 'error', text: error.name === 'AbortError' ? 'Stopped. Saved work can be continued.' : error.message})); place();
    } finally {
      typing.remove();
      if (!placed) { turn.append(generic({type: 'notice', level: 'info', text: 'The run ended without a reply. Saved results can be reviewed and continued.'})); place(); }
      streamController = null; setBusy(false); refreshMaterials().catch(() => {});
      document.dispatchEvent(new Event('harness:materials-changed')); $('input').focus();
    }
  }
  function upload(file) {
    if (!file || busy || restoring) return;
    const epoch = viewEpoch;
    uploadsPending += 1; updateControls();
    // The first upload can create a session. Serializing subsequent uploads
    // ensures they use that session rather than creating independent ones.
    uploadQueue = uploadQueue.catch(() => {}).then(async () => {
      if (viewEpoch !== epoch) return;
      const body = new FormData(); body.append('file', file);
      if (session) { body.append('session_id', session.id); body.append('session_token', session.token); }
      try {
        const response = await fetch('/api/files', {method: 'POST', body});
        const data = await response.json();
        if (!response.ok || data.ok === false) throw new Error(data.message || 'Upload failed.');
        if (viewEpoch !== epoch) return;
        if (data.session) saveSession(data.session);
        attachments.push(data.attachment); renderChips();
        if (data.blocks?.length) show(data.blocks);
      } catch (error) { if (viewEpoch === epoch) toast(error.message); }
    }).finally(() => { uploadsPending -= 1; updateControls(); });
    return uploadQueue;
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
    $('review-model').value = config.review_model || '';
    $('plugins').replaceChildren(...config.plugins.map(plugin => {
      const card = el('div', 'plugin' + (plugin.enabled ? ' enabled' : ''));
      const head = el('label', 'plugin-head');
      const toggle = el('input'); toggle.type = 'checkbox'; toggle.checked = plugin.enabled;
      toggle.onchange = async () => {
        try { config = {...config, ...(await post('/api/settings/plugins/' + plugin.name, {enabled: toggle.checked}))}; renderSettings(); await loadPluginUi(); }
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
  async function loadPluginUi() {
    const pending = [];
    for (const plugin of config.plugins) {
      for (const panel of document.querySelectorAll('.panel[data-plugin]')) if (panel.dataset.plugin === plugin.name) panel.hidden = !plugin.enabled;
      if (!plugin.enabled || !plugin.ui || loadedUis.has(plugin.name)) continue;
      loadedUis.add(plugin.name);
      const style = el('link'); style.rel = 'stylesheet'; style.href = `/plugins/${plugin.name}/ui.css`; document.head.append(style);
      const script = el('script'); script.src = `/plugins/${plugin.name}/ui.js`;
      pending.push(new Promise(resolve => { script.onload = resolve; script.onerror = () => { loadedUis.delete(plugin.name); toast(`Could not load ${plugin.title}.`); resolve(); }; }));
      document.body.append(script);
    }
    await Promise.all(pending);
    const visible = [...document.querySelectorAll('.panel[data-plugin]')].some(p => !p.hidden);
    $('panels').hidden = !visible; document.body.classList.toggle('has-panels', visible);
  }
  async function loadConfig() {
    try {
      const response = await fetch('/api/config');
      config = await response.json();
      renderSettings(); await loadPluginUi();
    } catch { $('llm-status').textContent = 'Cannot reach the local server'; }
  }

  $('composer').onsubmit = event => { event.preventDefault(); send(); };
  $('input').oninput = grow;
  $('input').onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } };
  $('attach').onclick = () => $('file').click();
  $('file').onchange = () => { upload($('file').files[0]); $('file').value = ''; };
  $('new-chat').onclick = () => {
    if (busy || uploadsPending) return;
    pendingTitle = ''; beginSession(null); $('input').value = ''; grow(); $('input').focus();
  };
  $('open-settings').onclick = $('workspace-settings').onclick = () => { closeDrawers(); $('settings-view').showModal(); };
  const RUN_KINDS = {model_request: 'Model request', model_response: 'Model response', tool_call: 'Tool call',
    tool_result: 'Tool result', answer_review_request: 'Review request', answer_review_response: 'Review response',
    answer_review_result: 'Review result', model_interrupted: 'Model interrupted', answer_delivered: 'Answer delivered',
    answer_unavailable: 'No answer'};
  function runDetail(row) {
    const known = value => value == null ? 'unknown' : value;
    switch (row.kind) {
      case 'model_request': return `${known(row.model)} · ${row.messages} messages · ${row.tools} tools`;
      case 'model_response': return row.tool_calls ? 'calls ' + row.tool_calls.join(', ') : `reply, ${row.reply_chars} characters`;
      case 'tool_call': return known(row.tool);
      case 'tool_result': return `${known(row.tool)} · ${row.error ? 'error: ' + row.error : row.receipt_status ? 'receipt ' + row.receipt_status : 'completed'}`
        + (row.seconds == null ? '' : ` · ${row.seconds}s`);
      case 'answer_review_request': case 'answer_review_response': return [row.model, row.mode].filter(Boolean).join(' · ');
      case 'answer_review_result': return [row.status, row.reason, row.model].filter(Boolean).join(' · ');
      case 'model_interrupted': case 'answer_unavailable': return known(row.reason);
      case 'answer_delivered': return `${row.chars} characters`;
      default: return '';
    }
  }
  function renderRunLog(turns) {
    if (!turns.length) { $('run-log').replaceChildren(el('p', 'hint', 'Nothing has run in this conversation yet.')); return; }
    $('run-log').replaceChildren(...turns.map(turn => {
      const section = el('section', 'run-turn');
      const when = new Date(turn.started * 1000).toLocaleTimeString();
      section.append(el('h3', '', `${turn.marked ? 'Turn' : 'Earlier activity (turn start not recorded)'} · ${when} · ${turn.seconds}s`));
      if (turn.text) section.append(el('p', 'run-question', turn.text));
      const list = el('ol', 'run-rows');
      for (const row of turn.rows) {
        const item = el('li', 'run-row' + (row.error ? ' failed' : ''));
        const budget = row.phase == null ? 'budget not recorded'
          : `${row.phase} · ${row.remaining_s}s left · ${row.tool_calls}/${row.max_calls} calls`;
        item.append(el('span', 'run-at', `+${row.at}s`), el('span', 'run-kind', RUN_KINDS[row.kind] || row.kind),
                    el('span', 'run-detail', runDetail(row)), el('span', 'run-budget', budget));
        list.append(item);
      }
      section.append(list);
      if (turn.truncated) section.append(el('p', 'hint', 'Later rows in this turn are not shown.'));
      return section;
    }));
  }
  $('open-run-log').onclick = async () => {
    closeDrawers();
    if (!session) { toast('Nothing has run in this conversation yet.'); return; }
    try { renderRunLog((await post('/api/run-log', sessionRef())).turns); $('run-log-view').showModal(); }
    catch (error) { toast(error.message); }
  };
  $('toggle-workspace').onclick = () => {
    const open = !document.body.classList.contains('show-workspace'); closeDrawers();
    document.body.classList.toggle('show-workspace', open); $('drawer-backdrop').hidden = !open;
    $('workspace').inert = !open;
    $('toggle-workspace').setAttribute('aria-expanded', String(open));
  };
  $('toggle-research').onclick = () => {
    if (window.innerWidth > 900) {
      document.body.classList.toggle('research-collapsed'); closeDrawers();
    } else {
      const open = !document.body.classList.contains('show-panels'); closeDrawers();
      document.body.classList.toggle('show-panels', open); $('drawer-backdrop').hidden = !open;
      $('panels').inert = !open;
      $('toggle-research').setAttribute('aria-expanded', String(open));
    }
  };
  $('drawer-backdrop').onclick = closeDrawers;
  $('close-workspace').onclick = () => { closeDrawers(); $('toggle-workspace').focus(); };
  $('close-research').onclick = () => { closeDrawers(); $('toggle-research').focus(); };
  $('panels').addEventListener('click', event => {
    if (window.innerWidth <= 900 && event.target.closest('.research-item')) closeDrawers();
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeDrawers(); });
  window.addEventListener('resize', closeDrawers);
  $('model-form').onsubmit = async event => {
    event.preventDefault();
    try { config = {...config, ...(await post('/api/settings/model', {model: $('model').value.trim()}))}; renderSettings(); toast('Model saved.'); }
    catch (error) { toast(error.message); }
  };
  $('review-model-form').onsubmit = async event => {
    event.preventDefault();
    try { config = {...config, ...(await post('/api/settings/review-model', {model: $('review-model').value.trim()}))}; renderSettings(); toast($('review-model').value ? 'Review model saved.' : 'Reviews use the main model.'); }
    catch (error) { toast(error.message); }
  };
  $('key-form').onsubmit = async event => {
    event.preventDefault();
    try { config = {...config, ...(await post('/api/settings/keys', {openrouter_api_key: $('api-key').value}))}; $('api-key').value = ''; renderSettings(); toast('API key saved to the local .env.'); }
    catch (error) { toast(error.message); }
  };
  document.addEventListener('dragover', event => event.preventDefault());
  document.addEventListener('drop', event => { event.preventDefault(); upload(event.dataTransfer.files[0]); });
  $('stop').onclick = async () => {
    try { if (session) await post('/api/sessions/cancel', sessionRef()); streamController?.abort(); toast('Stopping; the current request may take time to return.'); }
    catch (error) { toast(error.message); }
  };
  $('delete-chat').onclick = async () => {
    if (!session || busy || restoring || uploadsPending) return;
    const id = session.id, epoch = viewEpoch;
    try {
      await post('/api/sessions/close', sessionRef()); saved = saved.filter(s => s.id !== id); storeSaved();
      if (viewEpoch === epoch) $('new-chat').click(); else renderSaved();
    } catch (error) { toast(error.message); }
  };
  async function restoreSession(value) {
    const epoch = beginSession(value); restoring = true; updateControls();
    try {
      const data = await post('/api/sessions/resume', {session_id: value.id, session_token: value.token});
      if (viewEpoch !== epoch) return;
      const label = (data.timeline || []).find(turn => turn.role === 'user')?.text?.slice(0, 60);
      const entry = saved.find(item => item.id === value.id);
      if (entry && label && entry.title === 'Saved conversation') { entry.title = label; storeSaved(); renderSaved(); }
      for (const turn of data.timeline || []) {
        if (turn.role === 'user') userBubble(turn.text || '', turn.attachments || []);
        else show(turn.blocks || []);
      }
      if (data.persistence_warning) show([{type: 'notice', level: 'warning', text: data.persistence_warning}]);
      if (data.running) show([{type: 'notice', level: 'info', text: 'A previous request is stopping or finishing. Refresh the saved conversation after it returns.'}]);
      await refreshMaterials();
      if (viewEpoch !== epoch) return;
      document.dispatchEvent(new Event('harness:session-restored'));
    } catch (error) {
      if (viewEpoch !== epoch) return;
      show([{type: 'notice', level: 'error', text: error.message}]);
      toast(error.message);
    } finally { if (viewEpoch === epoch) { restoring = false; updateControls(); } }
  }
  $('saved-chats').onchange = async () => {
    const value = saved.find(s => s.id === $('saved-chats').value);
    if (!value || busy || uploadsPending) return;
    try { await restoreSession(value); } catch (error) { toast(error.message); }
  };
  async function refreshMaterials(append = false) {
    if (!session) return;
    const owner = session.id, epoch = viewEpoch, generation = ++directoryGeneration;
    const query = $('material-search').value, kind = $('material-kind').value;
    $('more-materials').disabled = true;
    let data;
    try { data = await post('/api/materials', {...sessionRef(), query, kind, offset: append ? materialCursor || 0 : 0}); }
    finally { if (directoryGeneration === generation) $('more-materials').disabled = false; }
    if (session?.id !== owner || viewEpoch !== epoch || directoryGeneration !== generation || $('material-search').value !== query || $('material-kind').value !== kind) return;
    if (!append) $('materials').replaceChildren();
    for (const item of data.items) {
      const fields = item.fields || {};
      const title = item.meta?.research?.title || fields.style_of_cause || fields.title || item.artifact_kind || item.detail || item.record_type || item.kind;
      const button = el('button', 'material-item');
      button.append(el('span', '', String(title).slice(0, 100)), el('small', '', `${item.ref} · ${item.provider || item.artifact_kind || item.kind}`));
      button.onclick = () => openMaterial(item.ref).catch(error => toast(error.message)); $('materials').append(button);
    }
    materialCursor = data.next_offset; $('more-materials').hidden = materialCursor == null;
    if (!data.total) $('materials').append(el('small', 'hint', query ? 'No matches in this category.' : 'No saved items in this category yet.'));
  }
  async function openMaterial(ref, field = '', offset = 0, exact = false) {
    if (!session) throw new Error('No conversation yet.');
    const epoch = viewEpoch, owner = session.id, generation = ++materialGeneration;
    const data = await post(`/api/materials/${encodeURIComponent(ref)}`, {...sessionRef(), field, offset, exact});
    if (viewEpoch !== epoch || session?.id !== owner || materialGeneration !== generation) return;
    if (materialPage?.ref !== data.ref || materialPage?.field !== data.field) { $('material-hits').replaceChildren(); $('material-find').value = ''; }
    materialPage = {...data, exact};
    $('material-title').textContent = data.ref;
    $('material-field').replaceChildren(...data.available_fields.map(name => { const option = el('option', '', name); option.value = name; return option; }));
    $('material-field').value = data.field;
    $('material-text').textContent = data.text;
    let coverage = '';
    if (data.coverage) {
      try { const c = typeof data.coverage === 'string' ? JSON.parse(data.coverage) : data.coverage; coverage = ` · ${c.status} coverage` + (c.total_units ? ` · ${(c.processed_units || []).length}/${c.total_units} units read` : '') + (c.missing_units?.length ? ` · missing: ${c.missing_units.slice(0, 20).join(', ')}` : '') + (c.stale ? ' · cached source may be stale' : ''); }
      catch { coverage = ' · extraction coverage unavailable'; }
    }
    $('material-status').textContent = `${data.origin} · characters ${data.offset}–${data.offset + data.text.length} of ${data.total_chars}${coverage}`;
    $('material-prev').disabled = data.offset === 0; $('material-next').disabled = data.next_offset == null;
    if (!$('material-view').open) $('material-view').showModal();
  }
  $('material-find-go').onclick = async () => {
    if (!materialPage || $('material-find').value.trim().length < 2) return;
    const epoch = viewEpoch, owner = materialPage, query = $('material-find').value.trim(), generation = ++findGeneration;
    try {
      const data = await post(`/api/materials/${encodeURIComponent(owner.ref)}/search`, {...sessionRef(), field: owner.field, query});
      if (viewEpoch !== epoch || materialPage !== owner || findGeneration !== generation || $('material-find').value.trim() !== query) return;
      $('material-hits').replaceChildren();
      for (const hit of data.matches) {
        const button = el('button', 'material-hit', `${hit.start}: ${hit.text.slice(0, 180)}`);
        button.onclick = () => openMaterial(hit.ref, hit.field, Math.max(0, hit.start - 200), true).catch(error => toast(error.message));
        $('material-hits').append(button);
      }
      if (!data.matches.length) $('material-hits').append(el('p', 'hint', 'No matching words in this saved text.'));
    } catch (error) { toast(error.message); }
  };
  $('material-view').addEventListener('close', () => { materialGeneration += 1; findGeneration += 1; });
  $('material-field').onchange = () => openMaterial(materialPage.ref, $('material-field').value, 0, materialPage.exact).catch(error => toast(error.message));
  $('material-prev').onclick = () => openMaterial(materialPage.ref, materialPage.field, Math.max(0, materialPage.offset - 4000), materialPage.exact).catch(error => toast(error.message));
  $('material-next').onclick = () => openMaterial(materialPage.ref, materialPage.field, materialPage.next_offset, materialPage.exact).catch(error => toast(error.message));
  $('refresh-materials').onclick = () => refreshMaterials().catch(error => toast(error.message));
  $('more-materials').onclick = () => refreshMaterials(true).catch(error => toast(error.message));
  $('material-search').oninput = () => refreshMaterials().catch(error => toast(error.message));
  $('material-kind').onchange = () => refreshMaterials().catch(error => toast(error.message));
  closeDrawers(); renderSaved();
  loadConfig().then(async () => {
    if (session) {
      if (!saved.some(s => s.id === session.id)) saveSession(session);
      await restoreSession(session);
    }
  });
})();
