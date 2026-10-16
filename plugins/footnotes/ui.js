(() => {
  'use strict';
  const api = window.Harness;
  const KIND = {full: 'Full citation', ibid: 'Ibid', supra: 'Supra'};
  // *text* marks italics in rendered citations; build nodes, never HTML.
  function citation(text) {
    const line = api.el('span', 'footnote-text');
    text.split(/(\*[^*]+\*)/).forEach(part => {
      if (part.startsWith('*') && part.endsWith('*') && part.length > 2) line.append(api.el('em', '', part.slice(1, -1)));
      else if (part) line.append(document.createTextNode(part));
    });
    return line;
  }
  api.registerBlock('footnotes', 'footnotes', block => {
    const card = api.el('section', 'bubble card footnotes-card');
    card.append(api.el('h3', '', `${block.title || 'Footnotes'} · ${block.ref}`));
    if (block.current_ref && block.current_ref !== block.ref) {
      card.append(api.el('p', 'note', `A newer revision exists: ${block.current_ref}.`));
    }
    const list = api.el('ol', 'footnote-list');
    for (const note of block.notes || []) {
      const item = api.el('li', 'footnote');
      item.value = note.number;
      item.append(citation(note.text || ''));
      const facts = api.el('div', 'footnote-facts');
      for (const ref of note.citations || []) {
        const label = (KIND[ref.kind] || ref.kind) + (ref.target_note ? ` → note ${ref.target_note}` : '');
        facts.append(api.el('span', 'badge', label));
        facts.append(api.el('span', 'badge ' + (ref.source_verified ? 'ok' : 'warn'),
          ref.source_verified ? 'Database-sourced case' : 'Case fields not from a database'));
        if (ref.pinpoint && ref.pinpoint_origin !== 'user') facts.append(api.el('span', 'badge warn', 'Pinpoint not typed by you'));
      }
      item.append(facts);
      list.append(item);
    }
    if (!(block.notes || []).length) card.append(api.el('p', 'note', 'No footnotes yet.'));
    card.append(list);
    for (const issue of block.issues || []) {
      card.append(api.el('p', 'note warn', issue.detail || `${issue.code}: ${issue.source}`));
    }
    card.append(api.el('p', 'note', `Forms follow ${block.rule_version}; these Ibid/supra templates have not had a McGill rules review.`));
    return card;
  });
})();
