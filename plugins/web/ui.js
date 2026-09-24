// Web plugin UI: search results and a read-page note.
(() => {
  const H = window.Harness;
  const {el} = H;
  function link(url, text) {
    const a = el('a', 'web-link', text); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a;
  }
  H.registerBlock('web', 'web_results', block => {
    const card = el('details', 'web-card');
    card.append(el('summary', '', `网络搜索 “${block.query}” · ${(block.results || []).length} 条结果`));
    for (const r of block.results || []) {
      const row = el('div', 'web-row');
      if (/^https?:\/\//.test(r.url)) row.append(link(r.url, r.title || r.url));
      row.append(el('small', '', r.url + (r.date ? ` · ${r.date}` : '')), el('p', '', r.snippet || ''));
      card.append(row);
    }
    if (block.note) card.append(el('p', 'note', block.note));
    return card;
  });
  H.registerBlock('web', 'web_page', block => {
    const row = el('div', 'activity');
    row.append('已读取网页：');
    if (/^https?:\/\//.test(block.url)) row.append(link(block.url, block.title || block.url));
    if (block.date) row.append(` · ${block.date}`);
    return row;
  });
})();
