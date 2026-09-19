(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const transcript = $('transcript');
  const input = $('input');
  const candidateSets = new Map();
  const extensions = new Set(['pdf', 'docx', 'pptx', 'xlsx', 'jpg', 'jpeg', 'png', 'webp']);
  let config = null, selectedFile = null, controller = null, generation = 0, busy = false;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function toast(message) {
    $('toast').textContent = message;
    $('toast').classList.add('show');
    setTimeout(() => $('toast').classList.remove('show'), 2600);
  }
  function autoGrow() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 180) + 'px';
  }
  function setBusy(value) {
    busy = value;
    $('send').disabled = value;
    input.disabled = value;
    document.querySelectorAll('#transcript button, #attach').forEach(button => {
      button.disabled = value || button.dataset.selected === 'true';
    });
  }
  function block(node, classes = '') {
    node.className = 'assistant-block ' + classes;
    transcript.append(node);
    return node;
  }
  function userTurn(text, filename) {
    const turn = el('article', 'turn');
    turn.append(el('div', 'message-label', 'YOU'));
    const message = el('div', 'user-message', text || '读取附件中的引用信息');
    if (filename) message.append(el('small', 'attachment-name', '附件：' + filename));
    turn.append(message);
    transcript.append(turn);
  }
  function showError(message, retry) {
    const node = block(el('div', '', message), 'notice error');
    if (retry) {
      const button = el('button', 'retry', '重试这次请求');
      button.type = 'button';
      button.addEventListener('click', () => { if (!busy) retry(); });
      node.append(button);
    }
  }
  async function copyCitation(text) {
    try { await navigator.clipboard.writeText(text); toast('引文已复制。'); }
    catch { toast('复制未完成，请选中文字手动复制。'); }
  }
  function render(blockData) {
    if (!blockData || typeof blockData !== 'object') return;
    switch (blockData.type) {
      case 'notice': {
        const level = ['error', 'warning', 'info'].includes(blockData.level) ? blockData.level : 'info';
        block(el('div', '', blockData.message), 'notice ' + level);
        break;
      }
      case 'citation_result': {
        const card = block(el('section'), 'citation');
        const header = el('header');
        const verified = blockData.verified === true;
        header.append(el('span', verified ? 'verified' : 'unverified', verified ? '已核验' : '未核验 · 请核对原文'));
        const copy = el('button', 'copy', '复制引文');
        copy.type = 'button';
        copy.onclick = () => copyCitation(blockData.citation || '');
        header.append(copy);
        const content = el('div', 'citation-text');
        // Only paired italic markers are recognized; data is never HTML.
        const parts = (blockData.citation || '').split(/(\*[^*]+\*)/g);
        for (const part of parts) content.append(part.startsWith('*') && part.endsWith('*') && part.length > 2
          ? el('em', '', part.slice(1, -1)) : document.createTextNode(part));
        card.append(header, content);
        if (Array.isArray(blockData.warnings)) blockData.warnings.forEach(warning => card.append(el('p', 'result-note', warning)));
        break;
      }
      case 'candidate_list': {
        candidateSets.set(blockData.candidate_set_id, blockData.access_token);
        const card = block(el('section'), 'candidate-list');
        card.append(el('p', '', blockData.message));
        for (const [index, item] of (blockData.items || []).entries()) {
          const button = el('button', 'candidate', (index + 1) + '. ' + (item.display || item.id));
          button.type = 'button';
          button.onclick = async () => {
            if (busy) return;
            const token = candidateSets.get(blockData.candidate_set_id);
            if (!token) return showError('候选列表已失效，请重新查询。');
            const ok = await run('/api/chatbox/select', json({candidate_set_id: blockData.candidate_set_id, access_token: token, candidate_id: item.id}));
            if (ok) { button.dataset.selected = 'true'; button.disabled = true; }
          };
          card.append(button);
        }
        break;
      }
      case 'decision_info': {
        const card = block(el('details'), 'decision');
        card.append(el('summary', '', blockData.shadow ? 'JEV 文档分类 · 仅供对比' : 'JEV 文档分类 · 已采用'));
        const confidence = blockData.confidence == null ? '无' : Math.round(blockData.confidence * 100) + '%';
        card.append(el('p', '', `${blockData.selected_id} · confidence ${confidence} · ${blockData.latency_ms} ms\n${blockData.model}`));
        break;
      }
      default: block(el('p', '', '此版本暂不支持这类结果，请刷新后重试。'), 'notice warning');
    }
  }
  function json(body) { return {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)}; }
  async function run(url, options, retry) {
    if (busy) return false;
    const ownGeneration = generation;
    controller = new AbortController();
    const localController = controller;
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; localController.abort(); }, 180000);
    $('empty-state').hidden = true;
    const typing = block(el('div', '', '正在读取与检索…'), 'typing');
    typing.append(el('i'), el('i'), el('i')); setBusy(true);
    typing.scrollIntoView({block: 'end', behavior: 'smooth'});
    try {
      const response = await fetch(url, {...options, signal: localController.signal});
      let data;
      try { data = await response.json(); } catch { throw new Error('服务暂时没有返回有效结果，请检查是否仍在运行。'); }
      if (ownGeneration !== generation) return false;
      if (!response.ok || data.ok === false) throw new Error(data.message || data.blocks?.[0]?.message || '请求未完成，请检查输入后重试。');
      typing.remove(); (data.blocks || []).forEach(render);
      transcript.lastElementChild?.scrollIntoView({block: 'end', behavior: 'smooth'});
      return true;
    } catch (error) {
      if (ownGeneration !== generation) return false;
      typing.remove();
      showError(timedOut ? '等待超过 3 分钟。原请求可能仍在服务端处理，请稍后重试。' : error.message, retry);
      return false;
    } finally {
      clearTimeout(timer);
      if (ownGeneration === generation) { controller = null; setBusy(false); input.focus(); }
    }
  }
  function removeFile() { selectedFile = null; $('file-input').value = ''; $('file-chip').hidden = true; }
  function chooseFile(file) {
    if (!file) return;
    if (!extensions.has(file.name.split('.').pop().toLowerCase())) return toast('请选择 PDF、DOCX、PPTX、XLSX 或 JPG/PNG/WebP 图片。');
    const maxMb = config?.max_upload_mb || 10;
    if (file.size > maxMb * 1024 * 1024) return toast(`文件上限为 ${maxMb} MB。`);
    selectedFile = file; $('file-name').textContent = file.name; $('file-chip').hidden = false;
  }
  function submit(text, file) {
    if (busy) return;
    userTurn(text, file?.name);
    if (file) {
      const body = new FormData(); body.append('file', file); body.append('input', text);
      run('/api/chatbox/files', {method: 'POST', body}, () => submit(text, file));
    } else run('/api/chatbox/turns', json({input: text}), () => submit(text));
  }
  function send() {
    const text = input.value.trim();
    if (busy || (!text && !selectedFile)) return;
    const file = selectedFile;
    input.value = ''; autoGrow(); removeFile(); submit(text, file);
  }
  function clearChat() {
    generation++; controller?.abort(); controller = null;
    candidateSets.clear(); transcript.replaceChildren(); $('empty-state').hidden = false;
    removeFile(); input.value = ''; autoGrow(); setBusy(false); input.focus();
  }
  async function loadConfig() {
    try {
      const response = await fetch('/api/chatbox/config');
      if (!response.ok) throw new Error();
      config = await response.json();
      if (config.max_upload_mb) $('upload-limit').textContent = config.max_upload_mb;
      const configured = config.configured === true;
      const llmReady = config.llm_configured === true;
      const jevReady = config.jev_configured === true;
      $('provider-dot').className = 'status-dot ' + (configured ? 'live' : 'offline');
      $('provider-name').textContent = configured ? '双线路已配置' : 'API 配置未完成';
      $('provider-detail').textContent = `OpenRouter ${llmReady ? '已配置' : '待配置'} · TypeSafe ${jevReady ? '已配置' : '待配置'}`;
      $('startup').hidden = configured; $('settings-content').replaceChildren();
      for (const [name, value] of [['文字/图片线路', 'OpenRouter'], ['JEV 线路', 'TypeSafe 官方 API'], ['文字模型', config.llm_model], ['图片模型', config.vision_model], ['JEV 模型', config.jev_model], ['JEV 对比', config.jev_shadow ? '开启' : '关闭']]) {
        const row = el('div'); row.append(el('span', '', name), el('b', '', value)); $('settings-content').append(row);
      }
      $('settings-content').append(el('p', '', '修改 config/chatbox.local.json 后重启服务。'));
    } catch {
      $('provider-name').textContent = '无法连接本地服务'; $('provider-detail').textContent = '请确认启动窗口仍在运行';
      $('settings-content').replaceChildren(); const retry = el('button', 'retry', '重试连接');
      retry.onclick = loadConfig; $('settings-content').append(retry); $('settings').open = true;
    }
  }
  $('key-form').onsubmit = async event => {
    event.preventDefault();
    if (busy) return;
    const openrouter = $('openrouter-key').value.trim();
    const typesafe = $('typesafe-key').value.trim();
    if (!openrouter && !typesafe) return toast('请至少填写一个 API Key。');
    $('save-keys').disabled = true;
    try {
      const response = await fetch('/api/chatbox/settings/keys', json({
        openrouter_api_key: openrouter || null,
        typesafe_api_key: typesafe || null
      }));
      const data = await response.json();
      if (!response.ok || data.ok === false) throw new Error(data.message || '保存失败。');
      $('openrouter-key').value = ''; $('typesafe-key').value = '';
      toast(data.message); await loadConfig();
    } catch (error) { toast(error.message || '保存失败。'); }
    finally { $('save-keys').disabled = false; }
  };
  $('composer').onsubmit = event => { event.preventDefault(); send(); };
  input.oninput = autoGrow;
  input.onkeydown = event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); send(); } };
  $('attach').onclick = () => $('file-input').click();
  $('file-input').onchange = () => chooseFile($('file-input').files[0]);
  $('remove-file').onclick = removeFile; $('new-chat').onclick = clearChat;
  document.querySelectorAll('[data-prompt]').forEach(button => { button.onclick = () => { if (!busy) { input.value = button.dataset.prompt; autoGrow(); input.focus(); } }; });
  ['dragenter', 'dragover'].forEach(name => document.addEventListener(name, event => { event.preventDefault(); document.body.classList.add('dragging'); }));
  ['dragleave', 'drop'].forEach(name => document.addEventListener(name, event => { event.preventDefault(); document.body.classList.remove('dragging'); }));
  document.addEventListener('drop', event => { if (event.dataTransfer.files.length > 1) return toast('每次请只上传一个文件。'); chooseFile(event.dataTransfer.files[0]); });
  input.addEventListener('paste', event => { const file = event.clipboardData?.files[0]; if (file) { event.preventDefault(); chooseFile(file); } });
  loadConfig();
})();
