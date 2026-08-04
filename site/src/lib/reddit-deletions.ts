/**
 * For a reddit-json flattening, replace bare [deleted]/[removed] comment
 * bodies with the most recent body held in earlier captures, clearly marked
 * as held before deletion. Presentation only: snapshots are never rewritten,
 * and a comment deleted before any capture keeps its bare marker.
 */

const DELETED = new Set(['[deleted]', '[removed]']);

/** Comment and stub blocks start at a line beginning; the first chunk is
 *  the post header. Bodies may contain blank lines but never a line of
 *  their own starting with "comment: " or "more-stub: ". */
function splitBlocks(text: string): string[] {
  return text.split(/(?=^comment: |^more-stub: )/m);
}

function blockId(block: string): string | null {
  const m = /^comment: (\S+)/.exec(block);
  return m ? m[1] : null;
}

function blockBody(block: string): string {
  const i = block.indexOf('\nbody:\n');
  return i === -1 ? '' : block.slice(i + 7).replace(/\n$/, '');
}

export function annotateHeldDeletionsText(
  text: string,
  earlier: { ts: string; text: string }[],
): string {
  // Newest-held-wins: `earlier` is expected newest first.
  const heldById = new Map<string, { ts: string; body: string }>();
  for (const { ts, text: prev } of earlier) {
    for (const block of splitBlocks(prev)) {
      const id = blockId(block);
      if (!id || heldById.has(id)) continue;
      const body = blockBody(block);
      if (!DELETED.has(body.trim())) heldById.set(id, { ts, body });
    }
  }
  if (heldById.size === 0) return text;

  return splitBlocks(text)
    .map((block) => {
      const id = blockId(block);
      if (!id) return block;
      const body = blockBody(block);
      if (!DELETED.has(body.trim())) return block;
      const held = heldById.get(id);
      if (!held) return block;
      const head = block.slice(0, block.indexOf('\nbody:\n') + 7);
      return (
        `${head}${body.trim()}\n\n` +
        `held before deletion (captured ${held.ts}):\n${held.body}\n`
      );
    })
    .join('');
}
