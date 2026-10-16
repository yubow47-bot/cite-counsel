(() => {
  'use strict';
  const api = window.Harness;
  const panel = api.registerPanel('research', 'Research workspace');
  const refresh = api.el('button', 'research-refresh', 'Refresh saved work');
  const search = api.el('input', 'research-search');
  search.type = 'search'; search.placeholder = 'Find saved claims, excerpts or drafts'; search.maxLength = 200;
  search.setAttribute('aria-label', 'Search saved research objects');
  const searchControls = api.el('div', 'research-search-controls'); searchControls.append(search, refresh);
  const directory = api.el('div', 'research-directory');
  const pager = api.el('div', 'research-pagination');
  const previous = api.el('button', '', 'Previous'), next = api.el('button', '', 'Next');
  const count = api.el('span', 'note'); pager.append(previous, count, next); pager.hidden = true;
  let directoryOffset = 0, nextOffset = null, searchTimer;
  panel.append(api.el('p', 'note', 'Saved assertions and exact source passages. Legal relationships require review.'), searchControls, directory, pager);
  const run = async (name, payload = {}, quiet = false) => {
    try { return await api.action('research', name, payload, {quiet}); }
    catch (error) { api.toast(error.message || 'Could not load research objects.'); }
  };
  const reviewLabel = status => status === 'user_reviewed' ? 'User recorded reading this snapshot'
    : status === 'review_invalidated' ? 'Reading acknowledgement invalidated by changes' : 'Not yet acknowledged';
  const labels = {
    support_gap: 'No supporting excerpt is linked', unresolved_unknown: 'A premise or assertion is unknown',
    unverified_assumption: 'A working assumption has not been verified',
    inference_gap: 'An inference needs saved premises and an explanation',
    unhandled_counterevidence: 'A recorded objection has no response',
    scope_not_applicable: 'The assertion is declared outside the applicable scope',
    unresolved_disputed: 'A premise or assertion is disputed', opposing_evidence: 'Opposing evidence is linked',
    object_revised: 'A newer object version exists', dependency_revised: 'A dependency has a newer version',
    dependency_changed: 'A saved dependency hash changed', dependency_unavailable: 'A dependency is unavailable',
    excerpt_binding_mismatch: 'The original excerpt binding no longer matches', source_unavailable: 'The original source is unavailable',
    inspection_limit: 'Dependency inspection is incomplete', missing_research_object: 'A saved research object is unavailable'
  };
  const loadDirectory = (offset = 0) => {
    directoryOffset = offset;
    return run('list', {query: search.value.trim(), offset, limit: 30}, true);
  };
  refresh.onclick = () => loadDirectory(0);
  search.oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadDirectory(0), 250); };
  previous.onclick = () => loadDirectory(Math.max(0, directoryOffset - 30));
  next.onclick = () => { if (nextOffset != null) loadDirectory(nextOffset); };
  api.on('research', 'research_directory', block => {
    if ((block.query || '') !== search.value.trim() || (block.offset || 0) !== directoryOffset) return;
    directory.replaceChildren();
    for (const item of block.items || []) {
      const row = api.el('button', 'research-item');
      row.append(api.el('span', 'research-item-type', `${item.type} · ${item.ref}`),
                 api.el('span', '', item.text),
                 api.el('small', item.stale ? 'research-stale' : '',
                   `${reviewLabel(item.review_status)} · ${item.issue_count} open checks`));
      row.onclick = () => run('inspect', {ref: item.ref});
      directory.append(row);
    }
    const length = (block.items || []).length;
    if (!length) directory.append(api.el('p', 'note', search.value.trim() ? 'No saved research objects match this search.' : 'No saved research objects yet.'));
    nextOffset = block.next_offset;
    previous.disabled = directoryOffset === 0; next.disabled = nextOffset == null;
    count.textContent = `${length ? directoryOffset + 1 : 0}–${directoryOffset + length} of ${block.total || 0}`;
    pager.hidden = !(block.total > 30 || directoryOffset > 0);
  });
  api.registerBlock('research', 'research_directory', () => null);
  api.registerBlock('research', 'research_export', block => api.el('p', 'note',
    `Inspection export prepared for ${block.ref} at ${block.inspected_at}${block.stale ? ' · STALE: dependencies require revision' : ''}. Legal interpretation remains unverified.`));
  const clip = (value, limit = 280) => {
    const text = String(value || '').replace(/\s+/g, ' ').trim();
    return text.length > limit ? text.slice(0, limit).trimEnd() + '…' : text;
  };
  const draftPreview = markdown => {
    // This is a plain-text preview of our saved snapshot, never an HTML or
    // Markdown renderer. Preserve the original verbatim in the disclosure.
    const technical = /^(?:#{1,6}\s|>|Research (?:snapshot|draft)\.|Exact-version fingerprint:|Source(?:-version fingerprint)?:|Speaker:|State:|Evidence |Depends on |Assertion versions:|Open checks:|(?:supports|limits|opposes) \(asserted\)|Premise \[|DEPENDENCY SNAPSHOT)/i;
    return clip(String(markdown || '').split('\n').map(line => line.trim()).filter(line => line && !technical.test(line)).slice(0, 2).join(' '));
  };
  const EXCERPT_STATUS = {same_position: 'Quote unchanged, same position', moved: 'Quote unchanged, moved',
    ambiguous: 'Quote appears more than once in the new version', missing: 'Quote no longer appears in the new version'};
  api.registerBlock('research', 'research_compare', block => {
    const card = api.el('section', 'bubble card research-report research-compare');
    card.append(api.el('p', 'research-eyebrow', 'Source version comparison'),
                api.el('h3', '', `${block.ref} → ${block.current_ref}`));
    if (!block.newer_version) card.append(api.el('p', 'note', 'This is already the latest version.'));
    for (const field of block.fields || []) {
      const section = api.el('details', 'research-detail compare-field');
      section.open = (block.fields || []).length === 1;
      section.append(api.el('summary', '', `${field.field}: ${field.old_chars} → ${field.new_chars} characters`));
      const text = api.el('div', 'compare-text');
      for (const segment of field.segments || []) {
        if (segment.op === 'too_large') { text.append(api.el('p', 'note', 'Too large to compare here; open both versions as materials.')); continue; }
        if (segment.op === 'equal') {
          if (segment.text) text.append(document.createTextNode(segment.text));
          if (segment.elided) text.append(api.el('span', 'compare-elided', ` … ${segment.elided} unchanged characters … `));
          if (segment.tail) text.append(document.createTextNode(segment.tail));
          continue;
        }
        if (segment.old) text.append(api.el('del', 'compare-old', segment.old));
        if (segment.new) text.append(api.el('ins', 'compare-new', segment.new));
      }
      section.append(text);
      card.append(section);
    }
    if (!(block.fields || []).length && block.newer_version) card.append(api.el('p', 'note', 'No text field changed.'));
    if ((block.excerpts || []).length) {
      const list = api.el('ul', 'compare-excerpts');
      for (const row of block.excerpts) {
        list.append(api.el('li', row.new_status === 'missing' ? 'compare-missing' : '',
          `${row.ref}${row.is_latest ? '' : ' (older version)'}: ${EXCERPT_STATUS[row.new_status] || row.new_status} — “${clip(row.quote, 140)}”`));
      }
      card.append(api.el('h4', '', 'Saved excerpts of the old version'), list);
    }
    const latest = (block.affected || []).filter(row => row.is_latest);
    if (latest.length) {
      const list = api.el('ul', 'compare-affected');
      for (const row of latest) list.append(api.el('li', '', `${'  '.repeat(row.depth - 1)}${row.type} ${row.ref} (via ${row.via})`));
      card.append(api.el('h4', '', 'Saved work that depends on it'), list);
    }
    card.append(api.el('p', 'note', block.note));
    if (block.newer_version && (block.excerpts || []).length) {
      const excerpts = block.excerpts.filter(row => row.is_latest).map(row => row.ref);
      const claims = latest.filter(row => row.type !== 'excerpt').map(row => row.ref);
      const button = api.el('button', '', 'Prepare a revision request');
      button.title = 'Fills the message box only; nothing is sent or changed.';
      button.onclick = () => api.prepareMessage(`Source ${block.ref} has a newer version ${block.current_ref}. Compare them with research__compare, `
        + `then for each excerpt ${excerpts.join(', ')} save a new excerpt from ${block.current_ref} only where the passage still says the same thing, `
        + `and revise the affected work${claims.length ? ' ' + claims.join(', ') : ''} one object at a time with prior_ref, keeping the old versions. `
        + 'Do not rebind anything whose meaning changed; report it instead.');
      card.append(button);
    }
    return card;
  });
  api.registerBlock('research', 'research_report', block => {
    const node = block.object || {};
    const issues = block.issues || [], sources = block.sources || [];
    const card = api.el('section', 'bubble card research-report');
    const names = {draft: 'Research draft', excerpt: 'Source excerpt', claim: 'Claim', premise: 'Premise', conclusion: 'Conclusion'};
    card.append(api.el('p', 'research-eyebrow', `${names[node.type] || 'Saved research'} · ${block.ref}`),
                api.el('h3', '', node.title || names[node.type] || 'Saved research'));
    const summary = node.markdown ? draftPreview(node.markdown) : clip(node.quote || node.text);
    if (summary) card.append(api.el('p', 'research-preview', summary));
    const state = api.el('div', 'research-state');
    state.append(api.el('span', block.stale ? 'research-state-badge research-stale' : 'research-state-badge',
      block.stale ? 'Dependencies changed · revision needed' : issues.length ? `${issues.length} checks to review` : 'No recorded link gaps'));
    if (node.status) state.append(api.el('span', 'note', `State: ${node.status} (asserted)`));
    card.append(state, api.el('p', 'note', reviewLabel(block.review_status)));
    if (issues.length) {
      const checks = api.el('ul', 'research-key-checks');
      const unique = [...new Set(issues.map(issue => issue.code))];
      for (const code of unique.slice(0, 3)) checks.append(api.el('li', '', labels[code] || code));
      card.append(checks);
      const allChecks = api.el('details', 'research-detail');
      allChecks.append(api.el('summary', '', `All checks and dependency paths (${issues.length})`));
      const fullChecks = api.el('ul');
      for (const issue of issues) fullChecks.append(api.el('li', '',
        `${labels[issue.code] || issue.code}: ${(issue.path || [issue.ref]).join(' → ')}${issue.current_ref ? `; current ${issue.current_ref}` : ''}`));
      allChecks.append(fullChecks); card.append(allChecks);
      // A newer version of a cited source: show old and new text side by side.
      const revised = [...new Set(issues.filter(issue => issue.code === 'dependency_revised' && /^rec_/.test(issue.ref))
        .map(issue => issue.ref))];
      if (revised.length) {
        const compareControls = api.el('div', 'research-controls');
        for (const ref of revised) {
          const button = api.el('button', '', `Compare ${ref} with its newer version`);
          button.onclick = () => run('compare', {ref});
          compareControls.append(button);
        }
        card.append(compareControls);
      }
    }
    if (sources.length) {
      const sourceGroup = api.el('details', 'research-detail research-sources');
      sourceGroup.append(api.el('summary', '', `Source passages and original context (${sources.length})`));
      for (const source of sources) {
        const detail = api.el('details', 'research-source');
        detail.append(api.el('summary', '', `${source.excerpt_ref} · ${source.work.provider}/${source.work.record_id}`),
                      api.el('blockquote', '', source.quote),
                      api.el('p', 'note', `${source.ref}.${source.field}[${source.start}:${source.end}] · ${source.origin}`));
        const context = api.el('button', '', 'Open original context');
        context.onclick = () => api.openMaterial(source.ref, source.field, Math.max(0, source.start - 400), true)
          .catch(error => api.toast(error.message || 'Could not open original context.'));
        detail.append(context);
        sourceGroup.append(detail);
      }
      card.append(sourceGroup);
    }
    const snapshot = api.el('details', 'research-detail research-snapshot');
    snapshot.append(api.el('summary', '', 'Full saved snapshot and version details'),
      api.el(node.markdown ? 'pre' : 'p', 'research-content', node.markdown || node.quote || node.text || ''),
      api.el('p', 'note', `Inspection at: ${block.inspected_at || 'not recorded'} · Source binding: ${block.mechanical_binding}`),
      api.el('pre', 'research-version', JSON.stringify({ref: block.ref, current_ref: block.current_ref,
        speaker: node.speaker, speaker_verification: node.speaker_verification, assessment: node.assessment,
        basis_kind: node.basis_kind, scope: node.scope, reasoning: node.reasoning, limitations: node.limitations,
        counterargument: node.counterargument, counterargument_response: node.counterargument_response,
        source: node.source, dependencies: node.dependencies}, null, 2)),
      api.el('p', 'note', block.note));
    card.append(snapshot);
    const controls = api.el('div', 'research-controls');
    if (sources.length) {
      const original = api.el('button', '', 'Open source context');
      original.title = sources.length > 1 ? 'Opens the first source passage; expand source passages to choose another.' : 'Open the exact original source version near this passage.';
      original.onclick = () => api.openMaterial(sources[0].ref, sources[0].field, Math.max(0, sources[0].start - 400), true)
        .catch(error => api.toast(error.message || 'Could not open original context.'));
      controls.append(original);
    }
    const inspect = api.el('button', '', 'Check current dependencies');
    inspect.onclick = () => run('inspect', {ref: block.ref});
    const review = api.el('button', '', 'Record that I read this snapshot');
    review.disabled = block.stale;
    review.title = block.stale ? 'Revise changed dependencies first.' : 'Records that you read this snapshot. Open checks remain; this does not approve the legal analysis.';
    review.onclick = async () => { await run('review', {ref: block.ref}); await loadDirectory(directoryOffset); };
    controls.append(inspect, review);
    if (node.markdown) {
      const download = api.el('button', '', 'Inspect and download Markdown');
      download.title = 'Checks current dependencies and includes the complete saved evidence graph and current inspection in the export.';
      download.onclick = async () => {
        download.disabled = true;
        try {
          const blocks = await run('export', {ref: block.ref}, true);
          const exported = blocks?.find(item => item.type === 'research_export');
          if (!exported) return;
          const url = URL.createObjectURL(new Blob([exported.markdown], {type: 'text/markdown;charset=utf-8'}));
          const link = api.el('a'); link.href = url; link.download = exported.filename; link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
          if (exported.stale) api.toast('Export includes a STALE warning and current checks. Revise dependencies before relying on this draft.');
        } finally { download.disabled = false; }
      };
      controls.append(download);
    }
    card.append(controls, api.el('p', 'research-legal-note', 'Source links and reading acknowledgements do not verify the legal analysis.'));
    return card;
  });
  document.addEventListener('harness:materials-changed', () => { if (api.hasSession()) loadDirectory(directoryOffset); });
  document.addEventListener('harness:new-session', () => {
    clearTimeout(searchTimer); search.value = ''; directoryOffset = 0; nextOffset = null;
    directory.replaceChildren(); pager.hidden = true;
  });
  document.addEventListener('harness:session-restored', () => { if (api.hasSession()) loadDirectory(0); });
  if (api.hasSession()) loadDirectory(0);
})();
