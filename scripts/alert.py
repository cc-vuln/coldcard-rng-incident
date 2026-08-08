#!/usr/bin/env python3
"""The single writer for operator-visible alerts.

An alert is one JSON line appended to
``$XDG_STATE_HOME/coldcard-archive/alerts.jsonl`` (default
``~/.local/state/coldcard-archive``). The read-only operator UI renders the
file; nothing here decides routes, sends messages, or holds a secret. The
8 Aug 2026 notification decision made the file the whole delivery surface,
so this script is deliberately small: append, dedupe, and a periodic sweep
that turns the repo's existing state files into alerts.

Line shape::

    {"ts": ..., "key": ..., "severity": ..., "kind": ..., "summary": ...,
     "detail": ...?}

Idempotency: the same ``key`` is never appended twice within a window
(default 24h). The check reads the tail of the file from EOF backwards and
stops at the window edge; the file only grows, so it is never loaded whole.
Callers pick keys that encode how often a reminder is acceptable: a failing
unit keys on its status value, a stale host proposal keys on the day.

Severities are info / warning / urgent. urgent is reserved for
guard-rejection, gate-failure and the x-session-health login wall;
everything the sweep raises is info or warning.

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".work"
INDEX = ROOT / "archive" / "index.jsonl"
CAPTURE_FAILURES = WORK / "capture-failures.txt"
CAPTURE_FAILURES_ALERTED = WORK / "capture-failures-alerted.txt"
HOST_PROPOSALS = WORK / "host-proposals.txt"
PUBLISH_STAMP = WORK / "publish-scheduled.stamp"

ALERTS_NAME = "alerts.jsonl"
TS_FORMAT = "%Y%m%dT%H%M%SZ"

KINDS = (
    "failure-streak",
    "guard-rejection",
    "gate-failure",
    "unit-failure",
    "publish-failure",
    "publish-skip-streak",
    "host-admission",
    "correction-applied",
    "capture-failure",
    "x-session-health",
    "x-availability",
    "gone-set",
)
SEVERITIES = ("info", "warning", "urgent")

# Failure-streak thresholds, per diagnosis family. Each threshold sits where
# the family's false-alarm rate stops being weather:
#   content-* at 2    a below-floor or missing-marker diagnosis almost always
#                     means this repo's own min_chars / required_text config
#                     went stale (the 6 Aug 2026 audit found five of six
#                     "challenged" sources were exactly that), so two in a
#                     row is already a config bug, not the publisher moving
#   dns-unresolved    at 4: on this host it usually means the local
#                     filtering resolver, and one filtered lookup proves
#                     nothing about the name; a short streak is expected
#                     noise behind IVPN egress
#   origin-* at 6     interstitials, refusals and 5xx are the publisher's
#                     side and flap by the hour; six consecutive failures is
#                     where "their problem" becomes "our record has a hole"
STREAK_THRESHOLDS = (
    ("content-", 2),
    ("dns-unresolved", 4),
    ("origin-", 6),
)

HOST_PROPOSAL_AGE = timedelta(hours=48)
PUBLISH_STAMP_AGE = timedelta(hours=8)

# Units the sweep watches, with the exit statuses that are ROUTINE for each.
# Alerting on a routine status is a false alarm every 24 hours:
# archive-poll exits 10 when a poll found changes (healthy), 20 on an
# incomplete poll (source-level failures have their own streak alerts) and
# 21 on writer-lock contention; record-commit exits 1 for an ordinary block
# (red gate, lock, unresolved guard run — all self-retrying); x-media exits
# 21 the same way. Anything outside the set is a genuine failure signal.
UNITS = {
    "archive-poll.service": {"0", "10", "20", "21"},
    "archive-review.service": {"0"},
    "discover-community.service": {"0"},
    "discover-x.service": {"0"},
    "claim-sweep.service": {"0"},
    "corrections-watch.service": {"0"},
    "site-sync.service": {"0"},
    "record-commit.service": {"0", "1"},
    "publish-scheduled.service": {"0"},
    "corroborate-gone.service": {"0"},
    "x-availability.service": {"0"},
    "x-media.service": {"0", "21"},
}


def state_dir() -> Path:
    # CC_ALERT_STATE_DIR exists for the tests, which must never touch the
    # real alerts.jsonl. Everyone else gets the XDG default.
    override = os.environ.get("CC_ALERT_STATE_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_STATE_HOME")
    if base:
        return Path(base) / "coldcard-archive"
    return Path.home() / ".local" / "state" / "coldcard-archive"


def parse_ts(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), TS_FORMAT).replace(
            tzinfo=timezone.utc)
    except ValueError:
        return None


def _recent_keys(fh, size: int, window_start: datetime) -> set[str]:
    """Keys of alerts newer than window_start, read tail-from-EOF.

    The file is append-only and chronological, so the scan stops at the
    first alert older than the window. Anything unparseable is skipped; a
    corrupt line must not suppress a real alert or crash the writer.
    """
    keys: set[str] = set()
    pos, buf = size, b""
    scanned = 0
    while pos > 0 and scanned < 16 * 1024 * 1024:
        step = min(65536, pos)
        pos -= step
        fh.seek(pos)
        buf = fh.read(step) + buf
        scanned += step
        lines = buf.split(b"\n")
        complete = lines[1:] if pos else lines
        buf = lines[0]  # partial leading line, completed by the next chunk
        stop = False
        for raw in reversed(complete):
            raw = raw.strip()
            if not raw:
                continue
            try:
                alert = json.loads(raw)
            except ValueError:
                continue
            ts = parse_ts(alert.get("ts"))
            if ts is not None and ts < window_start:
                stop = True
                break
            key = alert.get("key")
            if isinstance(key, str):
                keys.add(key)
        if stop:
            break
    return keys


def emit(kind: str, severity: str, key: str, summary: str,
         detail: str | None = None, window_hours: float = 24,
         now: datetime | None = None) -> bool:
    """Append one alert. Returns False when the key is already in the window."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if severity not in SEVERITIES:
        raise ValueError(f"unknown severity {severity!r}")
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=window_hours)
    state = state_dir()
    state.mkdir(parents=True, exist_ok=True)
    path = state / ALERTS_NAME
    record = {
        "ts": now.strftime(TS_FORMAT),
        "key": key,
        "severity": severity,
        "kind": kind,
        "summary": summary,
    }
    if detail:
        record["detail"] = detail
    line = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
    # The idempotency check and the append hold the same advisory lock, so
    # two writers racing the same key cannot both append. fcntl is present
    # on every host this project runs on; without it, append unlocked
    # rather than not at all.
    try:
        import fcntl
    except ImportError:
        fcntl = None
    with open(path, "a+b") as fh:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        fh.seek(0, os.SEEK_END)
        if key in _recent_keys(fh, fh.tell(), window_start):
            return False
        fh.write(line)
    return True


def streak_alerts(rows: list[dict], now: datetime | None = None) -> list[dict]:
    """Turn parsed `capture.py diagnose --json` rows into alert dicts.

    Importable on its own so the thresholds can be tested against stubbed
    JSON without a registry, an archive, or a capture run.
    """
    out = []
    for row in rows:
        diagnosis = row.get("diagnosis") or ""
        streak = row.get("streak") or 0
        threshold = None
        for prefix, value in STREAK_THRESHOLDS:
            if diagnosis.startswith(prefix):
                threshold = value
                break
        if threshold is None or streak < threshold:
            continue
        sid = row.get("id") or "(unknown)"
        out.append({
            "kind": "failure-streak",
            "severity": "warning",
            "key": f"failure-streak-{sid}-{diagnosis}",
            "summary": (f"{sid}: {diagnosis} x{streak} "
                        f"(failing since {row.get('failing_since')})"),
            "detail": row.get("detail") or None,
        })
    return out


def _diagnose_rows() -> list[dict]:
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "capture.py"),
         "diagnose", "--json"],
        cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        return []
    try:
        rows = json.loads(done.stdout)
    except ValueError:
        # diagnose prints prose, not JSON, when nothing is failing.
        return []
    return rows if isinstance(rows, list) else []


def _sweep_capture_failures(now: datetime) -> list[dict]:
    """One alert per recorded first-capture failure, then mark them alerted."""
    if not CAPTURE_FAILURES.exists():
        return []
    lines = [l for l in
             CAPTURE_FAILURES.read_text(encoding="utf-8").splitlines()
             if l.strip()]
    if not lines:
        return []
    alerts = []
    for line in lines:
        fields = line.split("\t")
        sid = fields[0].strip()
        run = fields[1].strip() if len(fields) > 1 else ""
        alerts.append({
            "kind": "capture-failure",
            "severity": "warning",
            "key": f"capture-failure-{sid}-{run or 'unrecorded'}",
            "summary": (f"first capture of {sid} failed "
                        f"(agent run {run or 'unrecorded'})"),
            "detail": line,
        })
    # Alerted, not deleted: the lines move to the -alerted file verbatim so
    # report_status.py's picture of what happened is not thinned out.
    with open(CAPTURE_FAILURES_ALERTED, "a", encoding="utf-8") as fh:
        fh.writelines(l + "\n" for l in lines)
    CAPTURE_FAILURES.unlink()
    return alerts


def _sweep_host_proposals(now: datetime) -> list[dict]:
    """Remind about host proposals waiting more than 48h, once per host per day."""
    if not HOST_PROPOSALS.exists():
        return []
    stale: dict[str, str] = {}
    for line in HOST_PROPOSALS.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 4:
            continue
        host = fields[1].strip()
        stamp = parse_ts(fields[3])
        if host and stamp is not None and now - stamp > HOST_PROPOSAL_AGE:
            stale.setdefault(host, fields[2].strip())
    return [{
        "kind": "host-admission",
        "severity": "info",
        "key": f"host-admission-{host}-{now:%Y-%m-%d}",
        "summary": f"host proposal for {host} has waited more than 48h",
        "detail": reason or None,
    } for host, reason in sorted(stale.items())]


def _index_changed_since(since: datetime) -> bool:
    """True when index.jsonl holds a 'changed' event newer than `since`.

    Tail-from-EOF, stopping at the first event older than the mark; the
    index is far too large to read for a yes/no question.
    """
    try:
        fh = open(INDEX, "rb")
    except OSError:
        return False
    with fh:
        fh.seek(0, os.SEEK_END)
        pos, buf = fh.tell(), b""
        scanned = 0
        while pos > 0 and scanned < 16 * 1024 * 1024:
            step = min(65536, pos)
            pos -= step
            fh.seek(pos)
            buf = fh.read(step) + buf
            scanned += step
            lines = buf.split(b"\n")
            complete = lines[1:] if pos else lines
            buf = lines[0]
            for raw in reversed(complete):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                ts = parse_ts(event.get("ts"))
                if ts is not None and ts < since:
                    return False
                if event.get("event") == "changed":
                    return True
    return False


def _sweep_publish_skips(now: datetime) -> list[dict]:
    """The archive moved but no scheduled publish landed for over 8h.

    The stamp file's mtime is the last successful publish; counting skip
    lines would need the journal, which rotates. The check that survives
    rotation is the archive's own record: a stamp older than 8h alongside
    changed events newer than the stamp means the record is ahead of the
    site and the scheduled publisher is skipping (or failing) every tick.
    """
    try:
        stamp_mtime = datetime.fromtimestamp(
            PUBLISH_STAMP.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return []
    if now - stamp_mtime <= PUBLISH_STAMP_AGE:
        return []
    if not _index_changed_since(stamp_mtime):
        return []
    return [{
        "kind": "publish-skip-streak",
        "severity": "warning",
        "key": f"publish-skip-streak-{now:%Y-%m-%d}",
        "summary": ("archive/index.jsonl has changes newer than the last "
                    f"scheduled publish ({stamp_mtime:%Y-%m-%d %H:%M}Z); "
                    "publish-scheduled has not landed in over 8h"),
        "detail": None,
    }]


def _sweep_unit_failures(now: datetime) -> list[dict]:
    """Alert when a scheduled unit's most recent run exited non-zero.

    Keyed per unit per status value: a unit stuck failing the same way
    reminds once a day, a new exit status is a new alert.
    """
    try:
        done = subprocess.run(
            ["systemctl", "show", *UNITS,
             "-p", "ExecMainStatus,ActiveState"],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    # `systemctl show` prints one property block per unit, blank-line
    # separated, in the order the units were given.
    blocks = [b for b in done.stdout.split("\n\n") if b.strip()]
    alerts = []
    for unit, block in zip(UNITS, blocks):
        props = dict(
            line.split("=", 1) for line in block.splitlines() if "=" in line)
        status = props.get("ExecMainStatus", "")
        if status and status not in UNITS[unit]:
            alerts.append({
                "kind": "unit-failure",
                "severity": "warning",
                "key": f"unit-failure-{unit}-{status}",
                "summary": (f"{unit} exited {status} on its most recent run "
                            f"(ActiveState={props.get('ActiveState', '?')})"),
                "detail": None,
            })
    return alerts


def sweep(now: datetime | None = None,
          window_hours: float = 24) -> tuple[int, int]:
    """The periodic pass. Returns (emitted, suppressed)."""
    now = now or datetime.now(timezone.utc)
    candidates = []
    candidates.extend(streak_alerts(_diagnose_rows(), now))
    candidates.extend(_sweep_capture_failures(now))
    candidates.extend(_sweep_host_proposals(now))
    candidates.extend(_sweep_publish_skips(now))
    candidates.extend(_sweep_unit_failures(now))
    emitted = suppressed = 0
    for alert in candidates:
        if emit(alert["kind"], alert["severity"], alert["key"],
                alert["summary"], detail=alert.get("detail"),
                window_hours=window_hours, now=now):
            emitted += 1
            print(f"emitted: {alert['key']}: {alert['summary']}")
        else:
            suppressed += 1
    return emitted, suppressed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="append operator alerts to alerts.jsonl")
    sub = parser.add_subparsers(dest="command", required=True)

    em = sub.add_parser("emit", help="append one alert, idempotently")
    em.add_argument("--kind", required=True, choices=KINDS)
    em.add_argument("--severity", required=True, choices=SEVERITIES)
    em.add_argument("--key", required=True)
    em.add_argument("--summary", required=True)
    em.add_argument("--detail")
    em.add_argument("--window-hours", type=float, default=24)

    sw = sub.add_parser("sweep", help="raise alerts from current repo state")
    sw.add_argument("--window-hours", type=float, default=24)

    sub.add_parser("test", help="emit one synthetic alert, to verify the "
                                "file-to-UI path end to end")

    args = parser.parse_args(argv)
    if args.command == "emit":
        appended = emit(args.kind, args.severity, args.key, args.summary,
                        detail=args.detail, window_hours=args.window_hours)
        print(("emitted" if appended
               else f"suppressed (key already alerted within "
                    f"{args.window_hours:g}h)") + f": {args.key}")
        return 0
    if args.command == "sweep":
        emitted, suppressed = sweep(window_hours=args.window_hours)
        print(f"sweep: {emitted} emitted, {suppressed} suppressed")
        return 0
    # test
    key = f"test-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    emit("capture-failure", "info", key,
         "synthetic alert from `alert.py test`; the file-to-UI path works")
    print(f"emitted: {key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
