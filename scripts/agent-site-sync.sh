#!/usr/bin/env bash
# Invoke the page-sync agent over the staleness packet.
#
# Site-prose maintenance is agent work since 8 Aug 2026, gated by the build
# gates rather than a human read. scripts/report_site_staleness.py routes
# what may have fallen behind (unreferenced registered sources, pages citing
# a source that moved, aging dated assertions); this script regenerates that
# packet, hydrates the pages and capture excerpts it names as the operator
# account, and hands the lot to the agent fenced. The agent edits
# site/src/pages/ only; it never fetches, and the guard's sync role makes
# the remit a permission fact rather than an instruction.
#
# The point of the lane is the post-run gate chain. When the run changed
# site/src/pages/, the driver runs `just check-claims` and a full gated
# build (`just build-site-core`: test, audit-core, check-claims, the Astro
# build and the output gates — the review gate is a publish concern, not a
# page-edit concern) under flock /tmp/cc-build.lock, queueing behind any
# build in progress the way an operator's build does. A gate failure rejects
# the run: the edits stay in place as evidence (the same posture as a guard
# rejection), an urgent gate-failure alert goes out, and the unit exits
# non-zero.
#
# Exit codes:
#   0  sync completed, the packet named nothing, or no agent is configured
#      (the packet still regenerates for a human to read)
#   1  agent run failed, the guard rejected the run, or a post-run gate
#      failed (marker NOT advanced; next tick retries)
#
# State lives in .work/site-sync/: evidence packs, rendered prompts and
# per-run reports. .work/ is ignored and never committed.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# shellcheck disable=SC1091
source "$ROOT/scripts/agent-run-common.sh"
agent_load_env

STATE_DIR="$ROOT/.work/site-sync"
MARKER="$STATE_DIR/last-run"
PROMPT_TEMPLATE="$ROOT/scripts/agent-site-sync-prompt.md"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
EVIDENCE_DIR="$STATE_DIR/$TS"
REPORT_PATH=".work/site-sync/${TS}-report.md"
PROMPT_RENDERED="$STATE_DIR/prompt-rendered.md"

mkdir -p "$EVIDENCE_DIR"
# The agent writes its report under here; the driver made the directories,
# so make them group-writable (the agent account shares the group).
chmod 2775 "$STATE_DIR" "$EVIDENCE_DIR"

# The packet is this lane's trigger and its main input; regenerate it so the
# agent works from current state, not from whatever the last packet said.
"$ROOT/.venv/bin/python" scripts/report_site_staleness.py --json >/dev/null
cp "$ROOT/.work/site-staleness.md" "$EVIDENCE_DIR/packet.md"

# Nothing routed, nothing to do. The unreferenced list alone counts: an
# unreviewed backlog of unlinked sources is exactly what the lane exists for.
ACTIONABLE="$("$ROOT/.venv/bin/python" - <<'PY'
import json
from pathlib import Path
packet = json.loads(Path(".work/site-staleness.json").read_text())
n = (sum(len(v) for v in packet["unreferenced"]["groups"].values())
     + len(packet["revision_routing"])
     + len(packet["dated_assertions"]))
print(n)
PY
)"
if [[ "$ACTIONABLE" == "0" ]]; then
  echo "agent-site-sync: the packet names nothing; nothing to do"
  exit 0
fi

# The sync agent is any non-interactive CLI that takes a rendered prompt as
# `<bin> -p "<prompt>"`. SITE_SYNC_AGENT_BIN overrides REVIEW_AGENT_BIN;
# either may be set in .env (see .env.example). Unset is supported: the
# packet above still regenerates each tick, and the work waits for a human.
AGENT_BIN="${SITE_SYNC_AGENT_BIN:-${REVIEW_AGENT_BIN:-}}"
if [[ -z "$AGENT_BIN" ]]; then
  echo "agent-site-sync: $ACTIONABLE packet entrie(s) outstanding, but neither SITE_SYNC_AGENT_BIN nor REVIEW_AGENT_BIN is set;"
  echo "  the packet is at .work/site-staleness.md for a human (see .env.example)"
  exit 0
fi

# Hydrate the evidence pack here, as the operator account, so the agent
# needs no network. The packet can name hundreds of entries (the
# unreferenced register alone is 600+), and run-agent.sh passes the prompt
# as ONE command-line argument, so the whole prompt must stay under the
# kernel's 128 KiB single-argument ceiling. This lane therefore batches
# like the intake lane: a bounded slice per run (timely signals first:
# dated assertions, revision routing, then unreferenced entries), with
# byte budgets on the hydrated page texts and capture excerpts. What does
# not fit waits for the next tick; unlinked sources repeat until linked,
# so nothing is lost by deferring.
#
#   packet.md    the selected batch (source titles and revision summaries
#                are stranger-written: rendered untrusted)
#   pages.md     the current text of pages the batch names, up to budget
#   captures.md  an excerpt of the newest held capture of each source the
#                batch's revision routing names (rendered untrusted)
echo "agent-site-sync: hydrating evidence for $ACTIONABLE packet entrie(s)"
"$ROOT/.venv/bin/python" - "$ROOT" "$EVIDENCE_DIR" <<'PY'
import json
import sys
from pathlib import Path

root, evidence_dir = Path(sys.argv[1]), Path(sys.argv[2])
packet = json.loads((root / ".work" / "site-staleness.json").read_text())

MAX_DATED, MAX_ROUTING, MAX_UNREF = 10, 25, 25
PAGES_BUDGET, CAPTURES_BUDGET = 55_000, 25_000

dated = packet["dated_assertions"][:MAX_DATED]
routing = packet["revision_routing"][:MAX_ROUTING]
unref = []
for group, entries in packet["unreferenced"]["groups"].items():
    for e in entries:
        unref.append((group, e))
unref = unref[:MAX_UNREF]
deferred = (len(packet["dated_assertions"]) - len(dated)
            + len(packet["revision_routing"]) - len(routing)
            + sum(len(v) for v in packet["unreferenced"]["groups"].values())
            - len(unref))

batch = ["# staleness batch (bounded slice of the full packet)", ""]
batch.append("## Dated assertions")
for a in dated:
    batch.append(f"- {a['file']}:{a['line']}: {a['text']}")
batch.append("\n## Revision routing")
for r in routing:
    batch.append(f"- {r['source']} ({r['timestamp']}): {r['summary']} "
                 f"-- pages: {', '.join(r['pages']) or 'none'}")
batch.append("\n## Unreferenced registered sources")
for group, e in unref:
    batch.append(f"- [{group}] {e['id']} -- weak mentions: "
                 f"{', '.join(e['files']) or 'none'}")
batch.append("")
(evidence_dir / "packet.md").write_text("\n".join(batch))

pages = set()
for a in dated:
    pages.add(a["file"])
for r in routing:
    pages.update(r["pages"])
for _, e in unref:
    pages.update(e["files"])

pages_out, pages_used = [], 0
pages_root = root / "site" / "src" / "pages"
for rel in sorted(pages):
    page = pages_root / rel
    if not page.is_file():
        continue
    text = page.read_text(errors="replace")
    if pages_used + len(text) > PAGES_BUDGET:
        pages_out.append(f"===== site/src/pages/{rel} =====\n"
                         "(not hydrated this run: over the byte budget; "
                         "a later tick batches it)")
        continue
    pages_used += len(text)
    pages_out.append(f"===== site/src/pages/{rel} =====\n" + text)
(evidence_dir / "pages.md").write_text(
    "\n\n".join(pages_out) + "\n" if pages_out else "(none)\n")

captures_out, captures_used = [], 0
snapshots = root / "archive" / "snapshots"
for r in routing:
    source_dir = snapshots / r["source"]
    if not source_dir.is_dir():
        continue
    txts = sorted(p for p in source_dir.iterdir() if p.suffix == ".txt")
    if not txts or captures_used >= CAPTURES_BUDGET:
        continue
    excerpt = txts[-1].read_text(errors="replace")[:8000]
    captures_used += len(excerpt)
    captures_out.append(
        f"===== archive/snapshots/{r['source']}/{txts[-1].name} (excerpt) =====\n"
        + excerpt)
(evidence_dir / "captures.md").write_text(
    "\n\n".join(captures_out) + "\n" if captures_out else "(none)\n")

print(f"agent-site-sync: batch of {len(dated) + len(routing) + len(unref)} "
      f"({deferred} deferred to later ticks); "
      f"{pages_used // 1024} KiB pages, {captures_used // 1024} KiB captures")
PY

# What site/src/pages/ looks like now, so the gate can tell whether the run
# changed anything. Hashes, not git state: the tree may already be dirty.
PAGES_BEFORE="$EVIDENCE_DIR/pages-before.sha256"
find site/src/pages -type f -print0 | sort -z | xargs -0 sha256sum > "$PAGES_BEFORE"

agent_begin sync

agent_render "$PROMPT_TEMPLATE" "$PROMPT_RENDERED" \
  --untrusted "PACKET=$EVIDENCE_DIR/packet.md" \
  --file "PAGES=$EVIDENCE_DIR/pages.md" \
  --untrusted "CAPTURES=$EVIDENCE_DIR/captures.md" \
  --value "REPORT_PATH=$REPORT_PATH" \
  --value "DATE=$(date -u +%Y-%m-%d)"

rc=0
agent_invoke "$AGENT_BIN" "$PROMPT_RENDERED" || rc=$?

grc=0
agent_finish sync || grc=$?

if [[ $grc -ne 0 ]]; then
  echo "agent-site-sync: the run was rejected by the guard; edits stay as the agent left them" >&2
  exit 1
fi
if [[ $rc -ne 0 ]]; then
  echo "agent-site-sync: agent run failed; marker NOT advanced" >&2
  exit 1
fi

# No agent_run_captures here: the sync role registers nothing, and the guard
# rejects any capture request it makes.

# ---- post-run gates -------------------------------------------------------
#
# The human read is replaced by this chain, so a run that changed the pages
# only counts when the pages still pass every gate a publish would face.
run_gates() {
  # Queue behind any build in progress, as an operator's build does. The
  # _astro recipe's probe sees the inherited fd 9 already holding the lock
  # and does not wait on it again.
  exec 9>/tmp/cc-build.lock
  flock -w 900 9 || { echo "agent-site-sync: could not take /tmp/cc-build.lock within 900s" >&2; return 1; }
  just check-claims && just build-site-core
}

PAGES_AFTER="$EVIDENCE_DIR/pages-after.sha256"
find site/src/pages -type f -print0 | sort -z | xargs -0 sha256sum > "$PAGES_AFTER"

if cmp -s "$PAGES_BEFORE" "$PAGES_AFTER"; then
  echo "agent-site-sync: the run changed no pages; gates not needed"
else
  echo "agent-site-sync: the run changed site/src/pages/; running the gate chain"
  gate_rc=0
  run_gates || gate_rc=$?
  if [[ $gate_rc -ne 0 ]]; then
    # The audit inside build-site exits 21 while the poll holds the archive
    # writer lock. Retry once rather than diagnosing it.
    echo "agent-site-sync: gate chain failed (exit $gate_rc); one retry after 90s" >&2
    sleep 90
    gate_rc=0
    run_gates || gate_rc=$?
  fi
  if [[ $gate_rc -ne 0 ]]; then
    # A rejected run keeps its edits: what failed the gate is the evidence a
    # human reads, and a dirty tree outside archive/ already stops the
    # scheduled publish. Alerting is allowed to fail; it never breaks this.
    echo "agent-site-sync: gate chain rejected the run; edits left in place as evidence" >&2
    "$ROOT/.venv/bin/python" scripts/alert.py emit \
      --kind gate-failure --severity urgent \
      --key "sync-gate-$(date -u +%Y-%m-%d)" \
      --summary "site-sync run $AGENT_RUN_ID edited site/src/pages/ but failed the post-run gates (exit $gate_rc); edits left in place as evidence, see $REPORT_PATH" || true
    exit 1
  fi
fi

if [[ ! -f "$ROOT/$REPORT_PATH" ]]; then
  echo "agent-site-sync: WARNING - agent succeeded but wrote no report to $REPORT_PATH" >&2
fi
touch "$MARKER"
chmod 600 "$MARKER"
echo "agent-site-sync: run complete; marker advanced to $(date -u +%Y-%m-%dT%H:%M:%SZ)"
exit 0
