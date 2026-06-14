/**
 * Copy a McGill citation to the system clipboard with rich formatting.
 *
 * Writes both ``text/html`` (``*italic*`` → ``<em>italic</em>``) and
 * ``text/plain`` (asterisks stripped), so Word / Google Docs pastes
 * the italicised version and plain-text editors get clean text.
 *
 * Falls back to ``text/plain`` only if the ``ClipboardItem`` API
 * is unavailable (e.g. some mobile browsers).
 */
export async function copyCitation(raw: string): Promise<void> {
  const esc = (s: string) =>
    s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

  const html = esc(raw).replace(/\*(.+?)\*/g, "<em>$1</em>");
  const plain = raw.replace(/\*(.+?)\*/g, "$1");

  try {
    await navigator.clipboard.write([
      new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([plain], { type: "text/plain" }),
      }),
    ]);
  } catch {
    // ClipboardItem not supported — fall back to plain text only
    await navigator.clipboard.writeText(plain);
  }
}
