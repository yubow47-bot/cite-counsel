(() => {
  'use strict';
  const api = window.Harness;
  const panel = api.registerPanel('inquiry', '问题与求证');
  const intro = api.el('p', 'note', '先找出会改变结论的未知前提，再决定查什么、向你确认什么。');
  const controls = api.el('div', 'inquiry-toolbar');
  const refresh = api.el('button', '', '刷新');
  const search = api.el('input', 'inquiry-search');
  search.type = 'search'; search.placeholder = '查找已保存的问题'; search.maxLength = 200;
  search.setAttribute('aria-label', '查找已保存的问题');
  controls.append(search, refresh);
  const feedback = api.el('p', 'note inquiry-feedback'); feedback.setAttribute('role', 'status'); feedback.setAttribute('aria-live', 'polite');
  const focus = api.el('div', 'inquiry-focus');
  const directory = api.el('div', 'inquiry-directory');
  const pager = api.el('div', 'inquiry-pagination');
  const previous = api.el('button', '', '上一页'), next = api.el('button', '', '下一页');
  const count = api.el('span', 'note'); pager.append(previous, count, next); pager.hidden = true;
  panel.append(intro, controls, feedback, focus, directory, pager);
  let offset = 0, nextOffset = null, searchTimer, selectedRef = null;
  let listGeneration = 0, inspectGeneration = 0, listing = false, inspecting = false;
  const clip = (value, max = 240) => {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    return text.length > max ? text.slice(0, max).trimEnd() + '…' : text;
  };
  const message = value => {
    const reasonLabels = {premise_source_changed: '前提使用的来源内容已改变。', premise_source_revised: '前提使用的来源已有新版本。', premise_source_unavailable: '前提使用的来源当前不可用。', premise_receipt_unavailable_or_changed: '前提的执行记录需要重新核对。', premise_source_not_executed: '没有实际执行此前记录的来源检查。', premise_source_not_checked: '尚未核对支撑这个前提的来源。', premise_user_binding_invalid: '无法核对这个前提所引用的用户原话。'};
    if (typeof value === 'string') return reasonLabels[value] || value;
    if (!value) return '';
    if (value.next_step && /bind.*(?:receipt|execution)/i.test(value.next_step)) return value.question || '查找相关证据或确认缺失的信息。';
    const labels = {
      candidate_explanations_missing: '还没有把解释与保存的证据关联。', key_claim_unassessed: '关键论点尚未检查。',
      countercheck_not_executed: '还没有为反证检查关联实际求证记录。', countercheck_not_recorded: '尚未记录反证检查的结果。',
      key_claim_disputed: '关键论点仍有争议。', key_claim_inconclusive: '关键论点仍无定论。',
      answer_claims_missing: '答复没有关联关键论点。', answer_needs_refresh: '调查已有变化，需要更新答复。',
      support_gap: '论点缺少关联依据。', inference_gap: '推理过程尚未写明。', unresolved_unknown: '仍有未知前提。',
      unresolved_disputed: '仍有争议前提。', claim_stale: '支撑材料已有变化，需要重查。', dependency_revised: '来源或支撑对象已有新版本。',
      inquiry_revised: '这是调查的较早版本。', receipt_unavailable_or_changed: '此前的执行记录已无法核对。',
      investigation_not_executed: '任务标记为完成，但没有成功取得的求证材料。', source_execution_failed: '来源获取失败；这不等于没有证据。',
      assessment_source_failed: '论证检查使用的来源获取失败。', dependency_unavailable: '已保存的来源或支撑对象不可用。',
      counterevidence_unavailable: '反证材料不可用，仍需检查其影响。', unhandled_counterevidence: '已关联反证，但尚未解释它对结论的影响。',
      scope_not_applicable: '引用依据被记录为不适用于当前范围。', premise_unknown: '会改变结论的前提仍然未知。',
      premise_needs_recheck: '前提的依据已有变化，需要重新检查。'
    };
    return value.message || value.detail || value.next_step || value.question || labels[value.code] || value.code || JSON.stringify(value);
  };
  const readiness = state => ({ready_for_review: '已记录关键检查 · 可审阅', conditional: '结论仍有条件或限制', open: '仍有关键问题待查'}[state] || state || '调查进行中');
  const setBusy = () => {
    refresh.disabled = listing; panel.setAttribute('aria-busy', String(listing || inspecting));
    feedback.textContent = inspecting ? '正在打开调查…' : listing ? '正在刷新问题…' : '';
  };
  const run = async (action, payload = {}, quiet = false, isCurrent = () => true) => {
    try { return await api.action('inquiry', action, payload, {quiet, isCurrent}); }
    catch (error) {
      if (isCurrent()) { const text = error.message || '暂时无法打开调查，请重试。'; feedback.textContent = text; api.toast(text); }
      return null;
    }
  };
  const addList = (parent, values, className = '', limit = Infinity) => {
    const items = (values || []).filter(item => message(item));
    if (!items.length) return;
    const list = api.el('ul', className);
    for (const value of items.slice(0, limit)) list.append(api.el('li', '', message(value)));
    parent.append(list);
  };
  const badge = state => api.el('span', `inquiry-badge inquiry-${state || 'open'}`, readiness(state));
  const openClaim = ref => api.action('research', 'inspect', {ref}).catch(error => api.toast(error.message));
  const openEvidence = ref => ref.startsWith('rec_') ? api.openMaterial(ref, '', 0, true).catch(error => api.toast(error.message)) : openClaim(ref);
  const prepare = (label, text) => {
    const button = api.el('button', 'inquiry-next-action', label); button.type = 'button';
    button.onclick = () => api.prepareMessage(text);
    return button;
  };
  const premiseState = premise => premise.needs_recheck ? 'needs_recheck' : (premise.effective_status || premise.status || 'unknown');
  const premiseLabel = state => ({unknown: '未知', supported: '有依据 · 模型判断', refuted: '有反证 · 模型判断', needs_recheck: '待重查'}[state] || state);
  const premisePrompt = (block, premise) => {
    const head = `继续调查 ${block.ref}，聚焦前提 ${premise.id}：「${clip(premise.statement, 600)}」。`;
    if (premise.method === 'user') return `${head}\n我补充的事实是：【请填写事实、时间及来源；不确定的地方也请说明】\n请据此检查这个前提，只重查受影响的论点；区分我提供的事实、证据支持的判断和仍可能的解释。`;
    return `${head}\n请执行下一项求证：${clip(premise.next_check || '查找能够区分该前提成立与不成立的证据', 700)}。记录找到的决定性证据和仍未知的部分，只重查受影响的论点。没有证据时保留条件结论，不把可能解释写成确定事实。`;
  };
  const premiseCard = (block, premise, expanded) => {
    const state = premiseState(premise), card = api.el('details', `inquiry-premise inquiry-premise-${state}`); card.open = expanded;
    const summary = api.el('summary', 'inquiry-premise-summary');
    summary.append(api.el('span', 'inquiry-premise-statement', premise.statement || premise.id), api.el('span', 'inquiry-premise-status', premiseLabel(state)));
    card.append(summary);
    if (premise.basis === 'user_reported') {
      summary.append(api.el('span', 'note inquiry-premise-basis', '用户提供 · 尚未外部核实'));
      if (premise.user_quote) card.append(api.el('p', 'inquiry-user-quote', `用户原话：“${premise.user_quote}”`));
    } else if (premise.basis === 'source_bound') {
      summary.append(api.el('span', 'note inquiry-premise-basis', '已关联实际材料 · 支持关系仍为模型判断'));
    }
    const branches = api.el('div', 'inquiry-branches');
    for (const [label, value] of [['如果成立', premise.if_true], ['如果不成立', premise.if_false]]) {
      const branch = api.el('div', 'inquiry-branch'); branch.append(api.el('strong', '', label), api.el('p', '', value || '尚未说明对结论的影响')); branches.append(branch);
    }
    card.append(branches);
    if (premise.next_check) card.append(api.el('p', 'inquiry-premise-next', `下一步：${premise.next_check}`));
    if (premise.needs_recheck) addList(card, premise.recheck_reasons, 'inquiry-recheck-reasons', 3);
    if (premise.affected_claim_refs?.length) card.append(api.el('p', 'note', `关联 ${premise.affected_claim_refs.length} 个论点 · ${premise.affected_claim_refs.join('、')}`));
    if (premise.evidence_refs?.length) {
      const sources = api.el('div', 'inquiry-evidence'); sources.append(api.el('span', 'note', '已关联依据：'));
      for (const ref of premise.evidence_refs) { const link = api.el('button', '', ref); link.onclick = () => openEvidence(ref); sources.append(link); }
      card.append(sources);
    }
    const label = premise.method === 'user' ? '补充这个事实' : state === 'needs_recheck' ? '重查这个前提' : '继续求证';
    card.append(prepare(label, premisePrompt(block, premise)));
    return card;
  };
  const addPremises = (parent, block, limit = 2) => {
    const premises = block.premises || [];
    if (!premises.length) return;
    const section = api.el('section', 'inquiry-premises');
    section.append(api.el('h4', '', '哪个前提会改变结论？'), api.el('p', 'note', '前提未确定时，保留两种分支；可能解释需要证据才能成为判断。'));
    const ordered = [...premises].sort((a, b) => ({needs_recheck: 0, unknown: 1}[premiseState(a)] ?? 2) - ({needs_recheck: 0, unknown: 1}[premiseState(b)] ?? 2));
    ordered.forEach((premise, index) => section.append(premiseCard(block, premise, index < limit)));
    section.append(api.el('p', 'note inquiry-action-hint', '选择下一步会填入输入框，编辑后再发送。'));
    parent.append(section);
  };
  const addUpdates = (parent, block) => {
    const affected = block.affected_claim_refs || [], pending = block.pending_materials || [];
    const answerStale = block.answer_needs_refresh || block.object?.answer?.needs_refresh;
    if (!affected.length && !pending.length && !answerStale) return;
    const updates = api.el('section', 'inquiry-updates');
    if (answerStale) updates.append(api.el('strong', '', '已保存的答复需要更新'), api.el('p', 'note', '相关前提、范围或依据已有变化，请先核对再使用先前答复。'));
    if (affected.length) {
      updates.append(api.el('strong', '', `${affected.length} 个论点需要重查`), api.el('p', 'note', '前提或其依据已有变化，先前结论需要重新核对。'));
      updates.append(prepare('重查受影响结论', `继续调查 ${block.ref}。请只重查受影响的论点 ${affected.join('、')} 及其前提，核对新旧依据，说明哪些结论改变、哪些仍然成立。前提尚未知时给出条件结论，不把可能解释写成确定事实。`));
    }
    if (pending.length) {
      const refs = pending.map(item => typeof item === 'string' ? item : item.ref).filter(Boolean);
      updates.append(api.el('strong', '', `${pending.length} 份新材料待关联`), api.el('p', 'note', '尚未判断这些材料是否影响当前前提。'));
      for (const ref of refs) { const button = api.el('button', 'inquiry-material-link', ref); button.onclick = () => openEvidence(ref); updates.append(button); }
      const ids = (block.premises || []).map(p => p.id).join('、');
      updates.append(prepare('检查新材料的影响', `继续调查 ${block.ref}。请阅读新材料 ${refs.join('、')}，先判断它们是否影响现有前提${ids ? ' ' + ids : ''}，记录相关依据或不相关的原因，再只重查受影响的论点。不要仅因材料新增就认定前提成立。`));
    }
    parent.append(updates);
  };
  const inspect = async (ref, quiet = true) => {
    const generation = ++inspectGeneration; selectedRef = ref; inspecting = true; setBusy();
    const isCurrent = () => generation === inspectGeneration && selectedRef === ref;
    const blocks = await run('inspect', {ref}, quiet, isCurrent);
    // The report listener clears the pending state when a response is rendered.
    if (isCurrent()) { inspecting = false; if (blocks) setBusy(); else panel.setAttribute('aria-busy', String(listing)); }
    return blocks;
  };
  const download = async (ref, button) => {
    button.disabled = true;
    try {
      const blocks = await run('export', {ref}, true);
      const exported = blocks?.find(block => block.type === 'inquiry_export');
      if (!exported) return;
      const url = URL.createObjectURL(new Blob([exported.markdown], {type: 'text/markdown;charset=utf-8'}));
      const link = api.el('a'); link.href = url; link.download = exported.filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } finally { button.disabled = false; }
  };
  const renderFocus = block => {
    inspectGeneration += 1; inspecting = false; selectedRef = block.ref; setBusy();
    const node = block.object || {};
    focus.replaceChildren();
    focus.append(api.el('p', 'inquiry-eyebrow', `当前问题 · ${block.ref}`), api.el('h3', '', node.question || '已保存的调查'), badge(block.readiness));
    addUpdates(focus, block); addPremises(focus, block, 1);
    if (!(block.premises || []).length) {
      if (block.blockers?.length) { focus.append(api.el('h4', '', '仍需查清')); addList(focus, block.blockers, 'inquiry-open-items', 3); }
      if (block.next_steps?.length) { focus.append(api.el('h4', '', '下一步')); addList(focus, block.next_steps, '', 2); }
    }
    const detail = api.el('button', 'inquiry-open-detail', '查看完整调查'); detail.onclick = () => inspect(block.ref, false); focus.append(detail);
  };
  const loadDirectory = async (position = 0) => {
    offset = position;
    const generation = ++listGeneration, selection = inspectGeneration;
    const query = search.value.trim(); listing = true; setBusy();
    const isCurrent = () => generation === listGeneration && query === search.value.trim() && position === offset;
    const blocks = await run('list', {query, offset, limit: 20}, true, isCurrent);
    if (!isCurrent()) return;
    listing = false;
    if (!blocks) { refresh.disabled = false; panel.setAttribute('aria-busy', String(inspecting)); return; }
    setBusy();
    const block = blocks.find(item => item.type === 'inquiry_directory');
    if (block && selection === inspectGeneration && !query && !position) {
      const ref = selectedRef || block.active_ref; if (ref) await inspect(ref);
    }
  };
  refresh.onclick = () => loadDirectory(0);
  search.oninput = () => { listGeneration += 1; listing = false; setBusy(); clearTimeout(searchTimer); searchTimer = setTimeout(() => loadDirectory(0), 250); };
  previous.onclick = () => loadDirectory(Math.max(0, offset - 20));
  next.onclick = () => { if (nextOffset != null) loadDirectory(nextOffset); };
  api.on('inquiry', 'inquiry_directory', block => {
    if ((block.query || '') !== search.value.trim() || (block.offset || 0) !== offset) return;
    directory.replaceChildren();
    for (const item of block.items || []) {
      const row = api.el('button', 'inquiry-item');
      row.append(api.el('strong', '', clip(item.question, 180)), api.el('small', '', readiness(item.readiness)));
      if (item.next_steps?.length) row.append(api.el('span', 'note', clip(message(item.next_steps[0]), 140)));
      row.onclick = () => inspect(item.ref); directory.append(row);
    }
    const length = block.items?.length || 0;
    if (!length) directory.append(api.el('p', 'note', search.value.trim() ? '没有找到匹配的问题。' : '还没有保存的调查。从你想解决的问题开始。'));
    nextOffset = block.next_offset; previous.disabled = offset === 0; next.disabled = nextOffset == null;
    count.textContent = `${length ? offset + 1 : 0}–${offset + length} / ${block.total || 0}`;
    pager.hidden = !(block.total > 20 || offset > 0);
  });
  api.on('inquiry', 'inquiry_report', renderFocus);
  api.registerBlock('inquiry', 'inquiry_directory', () => null);
  api.registerBlock('inquiry', 'inquiry_export', () => null);
  api.registerBlock('inquiry', 'inquiry_final_check', block => {
    const card = api.el('section', 'bubble card inquiry-check');
    card.append(api.el('h3', '', readiness(block.readiness)), api.el('p', 'note', block.note)); addList(card, block.items, 'inquiry-open-items');
    const button = api.el('button', '', '查看当前调查'); button.onclick = () => inspect(block.ref, false); card.append(button); return card;
  });
  api.registerBlock('inquiry', 'inquiry_report', block => {
    const node = block.object || {}, savedAnswer = node.answer || {};
    const card = api.el('section', 'bubble card inquiry-report');
    card.append(api.el('p', 'inquiry-eyebrow', `调查 · ${block.ref}`), api.el('h3', '', node.question || '已保存的问题'), badge(block.readiness));
    addUpdates(card, block); addPremises(card, block);
    if (node.goal) card.append(api.el('p', 'inquiry-goal', node.goal));
    if (savedAnswer.text) {
      card.append(api.el('h4', '', block.answer_needs_refresh || savedAnswer.needs_refresh ? '先前答复 · 待更新' : '已保存的答复'), api.el('p', 'inquiry-answer', savedAnswer.text));
      if (savedAnswer.conditions?.length) { card.append(api.el('h4', '', '适用条件')); addList(card, savedAnswer.conditions); }
      if (savedAnswer.gaps?.length) { card.append(api.el('h4', '', '尚缺信息')); addList(card, savedAnswer.gaps); }
      if (savedAnswer.review) {
        const review = api.el('details', 'inquiry-detail');
        review.append(api.el('summary', '', `模型复核 · ${savedAnswer.review.status}`), api.el('p', 'note', '复核提供的问题与原文范围，仍是模型判断。')); addList(review, savedAnswer.review.corrections); card.append(review);
      }
      const evidence = savedAnswer.delivery_evidence;
      if (evidence) {
        const detail = api.el('details', 'inquiry-detail');
        detail.append(api.el('summary', '', `交付依据 · ${evidence.observations?.length || 0} 段原文 · ${evidence.calculations?.length || 0} 项核算`));
        detail.append(api.el('p', 'note', '原文记录出处；核算验证输入和算术。它们不能单独证明推论或法律适用。'));
        for (const source of evidence.source_checks || []) {
          const status = {retrieved: '已取得', failed: '获取失败', unvisited: '尚未取得'}[source.status] || source.status;
          detail.append(api.el('p', 'note', `${status} · ${source.url}`));
        }
        card.append(detail);
      }
    }
    if (block.blockers?.length || block.next_steps?.length) {
      const remaining = api.el('details', 'inquiry-detail'); remaining.append(api.el('summary', '', '待查事项与下一步'));
      addList(remaining, block.blockers, 'inquiry-open-items'); addList(remaining, block.next_steps); card.append(remaining);
    }
    const scope = api.el('details', 'inquiry-detail'); scope.append(api.el('summary', '', '范围、事实与假设'));
    for (const [key, value] of Object.entries(node.scope || {})) {
      const names = {jurisdiction: '地点 / 管辖', as_of: '相关日期', procedure: '程序 / 决策', factual_context: '事实与假设'};
      scope.append(api.el('p', '', `${names[key] || key}：${value || '未知'}`));
    }
    card.append(scope);
    const taskReports = block.tasks || node.tasks || [];
    if (taskReports.length) {
      const tasks = api.el('details', 'inquiry-detail'); tasks.append(api.el('summary', '', `求证记录（${taskReports.length} 项）`));
      for (const task of taskReports) {
        const row = api.el('section', 'inquiry-task');
        row.append(api.el('h4', '', task.question), api.el('p', 'note', `${task.id} · ${task.status}${task.critical ? ' · 关键' : ''}`));
        for (const [key, label] of [['why', '目的'], ['plan', '计划'], ['findings', '发现（模型解释）'], ['next_step', '下一步']]) if (task[key]) row.append(api.el('p', '', `${label}：${task[key]}`));
        if (task.execution) row.append(api.el('p', 'note', `执行状态：${task.execution.replace(/_/g, ' ')}；是否回答了问题，仍由模型判断。`));
        row.append(api.el('p', 'note', task.receipt_ids?.length ? '执行记录：' + task.receipt_ids.join('、') : '尚未关联实际求证记录。')); tasks.append(row);
      }
      card.append(tasks);
    }
    const assessmentReports = block.assessments || node.assessments || [];
    if (assessmentReports.length) {
      const checks = api.el('details', 'inquiry-detail'); checks.append(api.el('summary', '', `关键论证检查（${assessmentReports.length} 项）`));
      for (const assessment of assessmentReports) {
        const row = api.el('section', 'inquiry-task');
        const original = api.el('button', '', '查看论点与原文 · ' + assessment.claim_ref); original.onclick = () => openClaim(assessment.claim_ref);
        const verdict = assessment.needs_recheck ? '待重查' : ({supported: '有依据', limited: '有限支持', disputed: '有争议', inconclusive: '无定论'}[assessment.effective_verdict || assessment.verdict] || assessment.effective_verdict || assessment.verdict || '无定论');
        row.append(original, api.el('p', 'note', `模型判断：${verdict}`), api.el('p', '', assessment.rationale || ''));
        if (assessment.direct_basis) row.append(api.el('p', 'note', assessment.direct_basis === 'source_quote' ? '与原文一致；内容真实性仍需判断。' : '包含模型解释，未视为直接原文事实。'));
        row.append(api.el('p', 'note', `关联 ${assessment.trusted_receipt_count || 0} 条可核对的执行记录；执行记录本身不代表论证成立。`));
        if (assessment.conditions?.length) { row.append(api.el('strong', '', '适用条件：')); addList(row, assessment.conditions); }
        if (assessment.counterevidence_refs?.length) {
          row.append(api.el('p', '', '反证或限制材料：'));
          for (const ref of assessment.counterevidence_refs) { const link = api.el('button', '', ref); link.onclick = () => openEvidence(ref); row.append(link); }
        }
        if (assessment.response) row.append(api.el('p', '', '对结论的影响：' + assessment.response)); checks.append(row);
      }
      card.append(checks);
    }
    const buttons = api.el('div', 'inquiry-controls');
    const inspectButton = api.el('button', '', '刷新当前状态'); inspectButton.onclick = () => inspect(block.ref, false);
    const exportButton = api.el('button', '', '下载调查报告'); exportButton.onclick = () => download(block.ref, exportButton);
    buttons.append(inspectButton, exportButton); card.append(buttons);
    card.append(api.el('p', 'note inquiry-boundary', '这里记录已执行的检查、依据与未知条件。来源是否相关、推理是否充分仍是模型判断。'));
    return card;
  });
  document.addEventListener('harness:materials-changed', () => { if (api.hasSession() && !api.busy()) loadDirectory(offset); });
  document.addEventListener('harness:new-session', () => {
    listGeneration += 1; inspectGeneration += 1; listing = false; inspecting = false; selectedRef = null;
    clearTimeout(searchTimer); search.value = ''; offset = 0; nextOffset = null; setBusy();
    focus.replaceChildren(); directory.replaceChildren(); pager.hidden = true;
  });
  document.addEventListener('harness:session-restored', () => { if (api.hasSession()) loadDirectory(0); });
  if (api.hasSession()) loadDirectory(0);
})();
