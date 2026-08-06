/**
 * Access to staged X media.
 *
 * site/tools/stage-x-media.mjs copies held image artefacts into public/ and
 * writes the manifest this reads. When staging is disabled the manifest is
 * empty, so every consumer degrades to text without a second code path.
 */
import manifest from '../data/x-media.json';

export interface XMediaFile {
  src: string;
  name: string;
  /** When this project captured the screenshot, distinct from post time. */
  captured: string;
}

const byPost = manifest as Record<string, XMediaFile[]>;

export function xMedia(postId: string): XMediaFile[] {
  return byPost[postId] ?? [];
}
