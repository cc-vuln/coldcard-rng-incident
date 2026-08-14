"""Tests for immutable discovery transactions and the legacy cutover."""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import migrate_discovery as migration  # noqa: E402
from discovery_store import (  # noqa: E402
    DiscoveryStore,
    build_parser,
    digest,
    load_intake_verdict_lines,
    pretty_json,
    url_identity,
    validate_migration,
    validate_store,
    verdict_facts,
)


LEGACY = """# Discovery intake

## Pending

- 2026-08-01 [@alice post](https://x.com/alice/status/123) (X @alice)
- 2026-08-01 [@bob repost](https://x.com/alice/status/123) (X @bob)

## Assessed

- 2026-08-03 [retry](https://x.com/carol/status/456) (X @carol) -> Pending: body fetch failed (20260804T010203Z)
- 2026-08-03 [accepted](https://stacker.news/items/99) by z -> registered as stackernews-accepted (body fetched via reader) (20260804T020304Z)
- 2026-08-03 [later accepted](https://www.reddit.com/r/Bitcoin/comments/abc123/thread/) by a -> Pending: body fetch failed (20260804T020000Z) -> registered as reddit-accepted (20260805T020304Z)
- 2026-08-03 [corrected](https://njump.me/note1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqsdz0t9) by n -> dismissed: first reading (20260804T030000Z); corrected on body-read re-check: useful evidence, registered as nostr-corrected (20260804T040000Z)
- historical broken row without URL -> dismissed: malformed historical row (20260804T050000Z)

## Deferred

- 2026-08-03 [quiet](https://bitcointalk.org/index.php?topic=777.0) by z [topical]
"""

ROTATED = """# Discovery intake verdicts - 2026-07

Assessed entries rotated out of DISCOVERY.md, verbatim.

- 2026-07-01 [old](https://www.reddit.com/r/Bitcoin/comments/old123/old/) by a -> dismissed: duplicate (20260702T010203Z)
"""


class StoreFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = DiscoveryStore(self.root, bootstrap=True)

    def observations(self, *native_ids: str) -> list[dict]:
        return [self.store._observation_event(
            {"url": f"https://x.com/a/status/{native_id}"},
            "pending", "20260801T000000Z", strict_identity=True,
        ) for native_id in native_ids]


class IdentityTests(unittest.TestCase):
    def test_cli_parser_constructs_and_accepts_one_limit(self) -> None:
        args = build_parser().parse_args(["list", "--limit", "0"])
        self.assertEqual(args.command, "list")
        self.assertEqual(args.limit, 0)

    def test_platform_native_identities_and_aliases(self) -> None:
        self.assertEqual(url_identity("https://x.com/A/status/123"), ("x", "123"))
        self.assertEqual(
            url_identity("https://old.reddit.com/r/x/comments/Ab12/title/"),
            ("reddit", "ab12"))
        self.assertEqual(url_identity("https://redd.it/Ab12"),
                         ("reddit", "ab12"))
        self.assertEqual(url_identity("https://stacker.news/items/99"),
                         ("stackernews", "99"))
        self.assertEqual(
            url_identity("https://bitcointalk.org/index.php?topic=88.0"),
            ("bitcointalk", "88"))

    def test_strict_identity_rejects_unknown_hosts(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a recognised"):
            url_identity("https://example.com/a", strict=True)

    def test_strict_identity_rejects_malformed_permalink_boundaries(self) -> None:
        malformed = (
            "ftp://reddit.com/r/x/comments/abc/title",
            "https://stacker.news/not/items/123evil",
            "https://bitcointalk.org/index.php?topic=88.0evil",
            "https://x.com/alice/status/123/evil",
            "https://reddit.com/comments/abc/>)[spoof](https://evil.invalid)",
            "https://redd.it/abc/>)[spoof](https://evil.invalid)",
            "https://stacker.news/items/123?x=>)[evil](https://evil.invalid)",
            "https://bitcointalk.org/index.php?topic=123&x=>)[evil](https://evil.invalid)",
            "https://reddit.com/comments/abc/title\n# fake",
            "https://reddit.com/comments/abc/title?q=\u008dhidden",
            "https://reddit.com/comments/abc/title?q=\u200bhidden",
            "https://reddit.com/comments/abc/title?q=\u202eevil",
        )
        for url in malformed:
            with self.subTest(url=url), self.assertRaisesRegex(
                    ValueError, "not a (safe|recognised)"):
                url_identity(url, strict=True)
        with self.assertRaisesRegex(ValueError, "import-only"):
            url_identity(
                "urn:coldcard-discovery:legacy:0123456789abcdef0123",
                strict=True)


class TransactionTests(StoreFixture):
    def test_non_http_observation_cannot_enter_or_render_from_replay(self) -> None:
        event = self.store._observation_event(
            {"url": "javascript:alert(1)"}, "pending",
            "20260801T000000Z", strict_identity=False)
        with self.assertRaisesRegex(ValueError, "URL disagrees"):
            self.store.commit_events(
                [event], kind="forged", at="20260801T000000Z")
        self.assertEqual(self.store.load_transactions(), [])

    def test_legacy_display_preserves_suffix_and_neutralizes_markup(self) -> None:
        candidate = {
            "identity": "stackernews:999",
            "platform": "stackernews",
            "native_id": "999",
            "url": "https://stacker.news/items/999",
            "first_recorded": "20260805T000000Z",
            "observations": [{
                "legacy_line": (
                    "- 2026-08-05 [community](https://stacker.news/items/999) "
                    "by author, 1 comments (Stacker News) "
                    "[click](https://evil.invalid) <b> ~~strike~~"),
                "legacy_candidate_line": (
                    "- 2026-08-05 [community](https://stacker.news/items/999) "
                    "by author, 1 comments (Stacker News) "
                    "[click](https://evil.invalid) <b> ~~strike~~"),
            }],
        }
        rendered = self.store.display_line(candidate)
        self.assertIn(
            "[community](<https://stacker.news/items/999>) "
            "by author, 1 comments (Stacker News)", rendered)
        self.assertNotIn("](https://evil.invalid)", rendered)
        self.assertNotIn("<b>", rendered)
        self.assertNotIn("~~strike~~", rendered)

    def test_legacy_display_keeps_a_no_url_row_discoverable(self) -> None:
        candidate = {
            "identity": "legacy:0123456789abcdef0123",
            "platform": "legacy",
            "native_id": "0123456789abcdef0123",
            "url": "urn:coldcard-discovery:legacy:0123456789abcdef0123",
            "first_recorded": "20260804T000000Z",
            "observations": [{
                "legacy_line": "- historical broken row without URL",
                "legacy_candidate_line": "- historical broken row without URL",
            }],
        }
        rendered = self.store.display_line(candidate)
        self.assertIn("historical broken row without URL", rendered)
        self.assertNotIn("legacy:012345", rendered)

    def test_lock_rejects_symlinked_work_or_lock_directories(self) -> None:
        for component in ("work", "locks"):
            with self.subTest(component=component):
                unsafe = self.root / f"unsafe-{component}"
                unsafe.mkdir()
                target = self.root / f"target-{component}"
                target.mkdir()
                if component == "work":
                    (unsafe / ".work").symlink_to(target, target_is_directory=True)
                else:
                    (unsafe / ".work").mkdir()
                    (unsafe / ".work/locks").symlink_to(
                        target, target_is_directory=True)
                with self.assertRaises(OSError):
                    with DiscoveryStore(unsafe, bootstrap=True).locked():
                        pass
                self.assertEqual(list(target.iterdir()), [])

    def test_transaction_staging_rejects_a_symlink(self) -> None:
        target = self.root / "staging-target"
        target.mkdir()
        with self.store.locked():
            pass
        staging = self.root / ".work/locks/.discovery-transactions"
        staging.symlink_to(target, target_is_directory=True)
        with self.assertRaises(OSError):
            self.store.record_observation(
                {"url": "https://x.com/a/status/123"},
                event_at="20260801T000000Z")
        self.assertEqual(self.store.load_transactions(), [])
        self.assertEqual(list(target.iterdir()), [])

    def test_projection_render_rejects_symlinked_ancestor_before_writing(self) -> None:
        self.store.record_observation(
            {"url": "https://x.com/a/status/123"},
            event_at="20260801T000000Z")
        platform = self.root / "discovery/candidates/x"
        held = self.root / "held-x-projections"
        platform.rename(held)
        external = self.root / "external-projection-target"
        external.mkdir()
        platform.symlink_to(external, target_is_directory=True)

        with self.assertRaisesRegex(ValueError, "symlink"):
            self.store.render_all()
        self.assertEqual(list(external.iterdir()), [])

    def test_projection_inventory_rejects_unusual_entries(self) -> None:
        self.store.record_observation(
            {"url": "https://x.com/a/status/123"},
            event_at="20260801T000000Z")
        fifo = self.root / "discovery/views/unexpected-fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError, "unusual entry"):
            self.store.render_all()

    def test_default_store_refuses_to_replace_a_legacy_queue(self) -> None:
        legacy_root = self.root / "legacy-only"
        legacy_root.mkdir()
        queue = legacy_root / "DISCOVERY.md"
        queue.write_text("# legacy queue\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "(migration is not active|namespace)"):
            DiscoveryStore(legacy_root).record_observation(
                {"url": "https://x.com/a/status/123"},
                event_at="20260801T000000Z")
        self.assertEqual(queue.read_text(encoding="utf-8"), "# legacy queue\n")
        self.assertFalse((legacy_root / "discovery/transactions").exists())

    def test_observations_merge_and_raw_driver_fields_do_not_escape(self) -> None:
        first = {
            "url": "https://x.com/a/status/123",
            "title": "first",
            "snippet": "private hydrated body",
            "event": {"raw": True},
            "relays": ["wss://private.invalid"],
        }
        second = {"url": "https://twitter.com/b/status/123", "title": "second"}
        self.store.record_observation(first, event_at="20260801T000000Z")
        self.store.record_observation(second, event_at="20260802T000000Z")
        candidate = self.store.load_candidate("x:123")
        self.assertEqual(len(candidate["observations"]), 2)
        self.assertNotIn("snippet", candidate["observations"][0])
        self.assertNotIn("event", candidate["observations"][0])
        self.assertNotIn("relays", candidate["observations"][0])
        self.assertEqual(self.store.count(), 1)

    def test_generated_view_rejects_multiline_display_spoof(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be one line"):
            self.store.record_observation({
                "url": "https://x.com/a/status/123",
                "display_line": (
                    "- 2026-08-01 [legitimate](https://x.com/a/status/123)\n"
                    "# Fake authoritative heading\n"
                    "- fake -> registered as invented-source"),
            }, event_at="20260801T000000Z")
        self.assertEqual(self.store.load_transactions(), [])

    def test_malformed_event_payloads_are_rejected_before_canonical_write(self) -> None:
        observation = self.observations("123")[0]
        observation["payload"]["url"] = "https://x.com/a/status/456"
        bare = {"url": "https://x.com/a/status/456"}
        observation["payload"]["observation"] = {
            "observation_id": digest(bare), **bare}
        observation["event_id"] = digest({
            key: observation[key] for key in
            ("candidate", "type", "at", "payload")})
        with self.assertRaisesRegex(ValueError, "URL disagrees"):
            self.store.commit_events(
                [observation], kind="bad", at="20260801T000000Z")
        self.assertEqual(self.store.load_transactions(), [])

        self.store.record_observation(
            {"url": "https://x.com/a/status/123"},
            event_at="20260801T000000Z")
        before = len(self.store.load_transactions())
        retry = self.store._retry_event(
            "x:123", "retry", "20260802T000000Z",
            expected_head="0" * 64)
        with self.assertRaisesRegex(ValueError, "expected head"):
            self.store.commit_events(
                [retry], kind="bad", at="20260802T000000Z")
        verdict = self.store._verdict_event(
            "x:123", "registered", "useful", "20260802T000000Z",
            source_id=["not", "text"])  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "source id"):
            self.store.commit_events(
                [verdict], kind="bad", at="20260802T000000Z")
        missing = self.store._verdict_event(
            "x:123", "registered", "useful", "20260802T000000Z")
        with self.assertRaisesRegex(ValueError, "no source id"):
            self.store.commit_events(
                [missing], kind="bad", at="20260802T000000Z")
        dismissed = self.store._verdict_event(
            "x:123", "dismissed", "irrelevant", "20260802T000000Z",
            source_id="wrong-source")
        with self.assertRaisesRegex(ValueError, "unexpectedly"):
            self.store.commit_events(
                [dismissed], kind="bad", at="20260802T000000Z")
        self.assertEqual(len(self.store.load_transactions()), before)

    def test_observation_cannot_claim_assessed_without_a_verdict(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown candidate state"):
            self.store.record_observation(
                {"url": "https://x.com/a/status/123"}, state="assessed",
                event_at="20260801T000000Z")
        self.assertEqual(self.store.load_transactions(), [])

    def test_current_projection_fields_clear_when_state_changes(self) -> None:
        self.store.record_observation(
            {"url": "https://x.com/a/status/123"},
            event_at="20260801T000000Z")
        self.store.set_state(
            "x:123", "deferred", reason="quiet",
            at="20260802T000000Z")
        deferred = self.store.load_candidate("x:123")
        self.assertEqual(deferred["state_reason"]["reason"], "quiet")

        self.store.record_observation(
            {"url": "https://x.com/a/status/123", "title": "active now"},
            state="pending", event_at="20260803T000000Z")
        promoted = self.store.load_candidate("x:123")
        self.assertEqual(promoted["state"], "pending")
        self.assertNotIn("state_reason", promoted)

        self.store.record_retry(
            "x:123", "temporary fetch failure", at="20260804T000000Z")
        self.store.set_state(
            "x:123", "human-review", reason="ambiguous link",
            at="20260805T000000Z")
        held = self.store.load_candidate("x:123")
        self.assertEqual(held["state"], "human-review")
        self.assertNotIn("retry", held)
        self.assertEqual(
            held["retry_history"][-1]["reason"], "temporary fetch failure")

    def test_assessed_candidate_can_be_explicitly_reopened(self) -> None:
        self.store.record_observation(
            {"url": "https://x.com/a/status/123"},
            event_at="20260801T000000Z")
        assessed = self.store.record_verdict(
            "x:123", "dismissed", reason="first reading",
            at="20260802T000000Z")
        with self.assertRaisesRegex(ValueError, "without superseding"):
            self.store.set_state(
                "x:123", "pending", reason="recheck",
                at="20260803T000000Z")
        reopened = self.store.set_state(
            "x:123", "pending", reason="recheck",
            supersedes=assessed["verdict"]["event_id"],
            at="20260803T000000Z")
        self.assertEqual(reopened["state"], "pending")
        self.assertNotIn("verdict", reopened)
        self.assertEqual(
            reopened["state_reason"]["supersedes"],
            assessed["verdict"]["event_id"])

    def test_native_identity_is_bounded_before_an_immutable_write(self) -> None:
        url = "https://x.com/a/status/" + "1" * 300
        with self.assertRaisesRegex(ValueError, "unsafe discovery path part"):
            self.store.record_observation(
                {"url": url}, event_at="20260801T000000Z")
        self.assertEqual(self.store.load_transactions(), [])

    def test_oversize_transaction_is_rejected_without_poisoning_chain(self) -> None:
        before = len(self.store.load_transactions())
        events = [self.store._observation_event(
            {"url": f"https://x.com/a/status/{100000 + number}",
             "display_line": "x" * 16000},
            "pending", "20260802T000000Z", strict_identity=True)
            for number in range(600)]
        with self.assertRaisesRegex(ValueError, "transaction exceeds"):
            self.store.commit_events(
                events, kind="oversize", at="20260802T000000Z")
        self.assertEqual(len(self.store.load_transactions()), before)
        self.assertEqual(self.store.list_candidates(), [])

    def test_transaction_time_is_the_latest_event_time(self) -> None:
        event = self.observations("123")[0]
        with self.assertRaisesRegex(ValueError, "latest event"):
            self.store.commit_events(
                [event], kind="wrong-month", at="20260901T000000Z")
        self.assertEqual(self.store.load_transactions(), [])

    def test_hard_stop_before_transaction_link_cannot_poison_history_dir(self) -> None:
        code = """
import os, sys
from pathlib import Path
from unittest import mock
sys.path.insert(0, sys.argv[2])
from discovery_store import DiscoveryStore
store = DiscoveryStore(Path(sys.argv[1]), bootstrap=True)
with mock.patch('discovery_store.os.link', side_effect=lambda *_a, **_k: os._exit(91)):
    store.record_observation({'url': 'https://x.com/a/status/123'},
                             event_at='20260801T000000Z')
"""
        result = subprocess.run(
            [sys.executable, "-c", code, str(self.root), str(ROOT / "scripts")],
            check=False)
        self.assertEqual(result.returncode, 91)
        self.assertEqual(self.store.load_transactions(), [])
        unexpected = [path for path in self.store.transactions.rglob("*")
                      if path.is_file()]
        self.assertEqual(unexpected, [])

    def test_operation_replay_is_idempotent_and_content_bound(self) -> None:
        observation = {"url": "https://x.com/a/status/123"}
        for _attempt in range(2):
            self.store.record_observation(
                observation, event_at="20260801T000000Z",
                operation_id="same-run")
        self.assertEqual(len(self.store.load_transactions()), 1)
        with self.assertRaisesRegex(ValueError, "different content"):
            self.store.record_observation(
                {"url": "https://x.com/a/status/456"},
                event_at="20260801T000000Z", operation_id="same-run")

    def test_durable_transaction_survives_projection_failure_and_replay_repairs(self) -> None:
        observation = {"url": "https://x.com/a/status/123"}
        with mock.patch.object(
                self.store, "_write_projections_unlocked",
                side_effect=OSError("simulated projection failure")):
            with self.assertRaisesRegex(OSError, "projection failure"):
                self.store.record_observation(
                    observation, event_at="20260801T000000Z",
                    operation_id="recoverable-run")
        self.assertEqual(len(self.store.load_transactions()), 1)
        self.assertEqual(self.store.load_candidate("x:123")["state"], "pending")
        self.store.record_observation(
            observation, event_at="20260801T000000Z",
            operation_id="recoverable-run")
        self.assertEqual(len(self.store.load_transactions()), 1)
        self.assertEqual(self.store.projection_errors(), [])

    def test_action_batch_checks_every_head_before_one_atomic_commit(self) -> None:
        self.store.commit_events(
            self.observations("123", "456"), kind="test",
            at="20260801T000000Z", operation_id="observations")
        candidates = {row["identity"]: row for row in self.store.list_candidates()}
        before = len(self.store.load_transactions())
        with self.assertRaisesRegex(ValueError, "candidate head changed"):
            self.store.apply_actions([
                {"candidate_id": "x:123", "action": "dismissed",
                 "reason": "duplicate", "expected_head": candidates["x:123"]["head"]},
                {"candidate_id": "x:456", "action": "retry",
                 "reason": "fetch failed", "expected_head": "stale"},
            ], operation_id="intake-run")
        self.assertEqual(len(self.store.load_transactions()), before)
        self.assertEqual(self.store.count(state="pending"), 2)

    def test_action_batch_replay_and_retry_head_binding(self) -> None:
        self.store.commit_events(
            self.observations("123", "456"), kind="test",
            at="20260801T000000Z", operation_id="observations")
        candidates = {row["identity"]: row for row in self.store.list_candidates()}
        actions = [
            {"candidate_id": "x:123", "action": "registered",
             "reason": "incident evidence", "source_id": "x-useful",
             "expected_head": candidates["x:123"]["head"],
             "at": "20260802T000000Z"},
            {"candidate_id": "x:456", "action": "retry",
             "reason": "fetch failed",
             "expected_head": candidates["x:456"]["head"],
             "at": "20260802T000001Z"},
        ]
        first = self.store.apply_actions(actions, operation_id="intake-run")
        count = len(self.store.load_transactions())
        second = self.store.apply_actions(actions, operation_id="intake-run")
        self.assertEqual(first, second)
        self.assertEqual(len(self.store.load_transactions()), count)
        tx = self.store.load_transactions()[-1]
        self.assertEqual(tx["events"][1]["payload"]["expected_head"],
                         candidates["x:456"]["head"])

    def test_duplicate_event_cannot_restore_an_old_head_aba(self) -> None:
        first = self.observations("123")[0]
        self.store.commit_events(
            [first], kind="test", at=first["at"], operation_id="first-a")
        stale_head = self.store.load_candidate("x:123")["head"]
        second = self.store._observation_event(
            {"url": "https://x.com/a/status/123", "title": "newer"},
            "pending", "20260802T000000Z", strict_identity=True)
        self.store.commit_events(
            [second], kind="test", at=second["at"], operation_id="second-b")

        with self.assertRaisesRegex(ValueError, "globally unique"):
            self.store.commit_events(
                [first], kind="test", at=first["at"],
                operation_id="replayed-a")
        with self.assertRaisesRegex(ValueError, "candidate head changed"):
            self.store.apply_actions([{
                "candidate_id": "x:123",
                "action": "dismissed",
                "reason": "stale decision",
                "expected_head": stale_head,
                "at": "20260803T000000Z",
            }], operation_id="stale-intake")

    def test_transaction_tampering_breaks_hash_validation(self) -> None:
        self.store.commit_events(
            self.observations("123"), kind="test",
            at="20260801T000000Z", operation_id="one")
        path = next(self.store.transactions.rglob("*.json"))
        value = json.loads(path.read_text())
        value["kind"] = "tampered"
        path.write_text(pretty_json(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.store.load_transactions()

    def test_transaction_bytes_are_canonical_and_have_unique_keys(self) -> None:
        for name, mutate, message in (
                ("whitespace", lambda raw: b"\n" + raw, "not canonical"),
                ("duplicate-key",
                 lambda raw: raw.replace(
                     b"{\n", b'{\n  "kind": "misleading",\n', 1),
                 "duplicate JSON key")):
            with self.subTest(case=name):
                root = self.root / name
                root.mkdir()
                store = DiscoveryStore(root, bootstrap=True)
                store.record_observation(
                    {"url": "https://x.com/a/status/123"},
                    event_at="20260801T000000Z")
                path = next(store.transactions.rglob("*.json"))
                path.write_bytes(mutate(path.read_bytes()))
                with self.assertRaisesRegex(ValueError, message):
                    store.load_transactions()

    def test_canonical_transaction_types_and_timestamps_are_enforced(self) -> None:
        cases = (("iso-event", "event timestamp"),
                 ("boolean-header", "transaction schema"))
        for name, message in cases:
            with self.subTest(case=name):
                root = self.root / name
                store = DiscoveryStore(root, bootstrap=True)
                event = store._observation_event(
                    {"url": "https://x.com/a/status/123"}, "pending",
                    "20260801T000000Z", strict_identity=True)
                if name == "iso-event":
                    event["at"] = "2026-08-01T00:00:00+00:00"
                    event["event_id"] = digest({
                        key: event[key]
                        for key in ("candidate", "type", "at", "payload")
                    })
                core = {
                    "schema": True if name == "boolean-header" else 1,
                    "sequence": True if name == "boolean-header" else 1,
                    "previous": None,
                    "at": "20260801T000000Z",
                    "kind": "forged",
                    "operation_id": name,
                    "events": [event],
                }
                transaction = {**core, "transaction_id": digest(core)}
                path = (store.transactions / "2026-08" /
                        f"00000001-{transaction['transaction_id']}.json")
                path.parent.mkdir(parents=True)
                path.write_text(pretty_json(transaction), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    store.load_transactions()

    def test_transaction_and_projection_modes_are_readable_not_writable(self) -> None:
        self.store.commit_events(
            self.observations("123"), kind="test",
            at="20260801T000000Z", operation_id="one")
        paths = [
            next(self.store.transactions.rglob("*.json")),
            self.store.candidate_path("x:123"),
            self.root / "discovery/state.json",
        ]
        self.assertEqual({stat.S_IMODE(path.stat().st_mode) for path in paths},
                         {0o644})
        self.assertEqual(stat.S_IMODE((self.root / "DISCOVERY.md").stat().st_mode),
                         0o640)


class MigrationFixture(StoreFixture):
    def setUp(self) -> None:
        super().setUp()
        (self.root / "DISCOVERY.md").write_text(LEGACY, encoding="utf-8")
        (self.root / "discovery").mkdir()
        (self.root / "discovery/assessed-2026-07.md").write_text(
            ROTATED, encoding="utf-8")

    def build(self, name: str = "built") -> tuple[Path, dict]:
        dest = self.root / name
        dest.mkdir()
        return dest, migration.build(
            self.root, dest, source_commit="fixture",
            created_at="20260814T000000Z")


class MigrationTests(MigrationFixture):
    def test_explicit_rehearsal_output_cannot_write_inside_repository(self) -> None:
        output = self.root / "discovery" / "unsafe-stage"
        with self.assertRaisesRegex(ValueError, "outside the repository"):
            migration._new_stage(self.root, output)
        self.assertFalse(output.exists())

    def test_normal_writer_rejects_symlinked_discovery_namespace(self) -> None:
        external, _manifest = self.build("external-store")
        victim = self.root / "victim"
        victim.mkdir()
        (victim / "discovery").symlink_to(
            external / "discovery", target_is_directory=True)
        (victim / "DISCOVERY.md").write_text(
            (external / "DISCOVERY.md").read_text(encoding="utf-8"),
            encoding="utf-8")
        external_store = DiscoveryStore(external)
        before = len(external_store.load_transactions())

        with self.assertRaisesRegex(ValueError, "namespace"):
            DiscoveryStore(victim).record_observation(
                {"url": "https://x.com/a/status/999"},
                event_at="20260815T000000Z")
        self.assertEqual(len(external_store.load_transactions()), before)

    def test_cutover_refuses_an_active_legacy_writer(self) -> None:
        import fcntl

        state = self.root / ".work/agent-discovery-intake"
        state.mkdir(parents=True)
        lock = state / "intake.lock"
        with lock.open("a+") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "pre-cutover"):
                with migration.cutover_locks(self.root):
                    self.fail("cutover acquired a held legacy lock")

    def test_rotated_symlink_is_rejected_before_its_target_is_copied(self) -> None:
        rotated = self.root / "discovery/assessed-2026-07.md"
        rotated.unlink()
        secret = self.root / "operator-secret"
        secret.write_text("TOKEN=should-not-copy\n", encoding="utf-8")
        rotated.symlink_to(secret)
        dest = self.root / "symlink-build"
        dest.mkdir()
        with self.assertRaisesRegex(ValueError, "not a regular file"):
            migration.build(self.root, dest, source_commit="fixture",
                            created_at="20260814T000000Z")
        self.assertFalse((dest / "discovery/migration-v1/legacy/discovery/"
                          "assessed-2026-07.md").exists())

    def test_cross_file_verdicts_replay_by_time_not_file_iteration(self) -> None:
        url = "https://x.com/alice/status/123"
        (self.root / "DISCOVERY.md").write_text(
            "# Discovery intake\n\n## Assessed\n\n"
            f"- 2026-08-11 [later]({url}) -> registered as later-source "
            "(20260811T000000Z)\n", encoding="utf-8")
        (self.root / "discovery/assessed-2026-07.md").write_text(
            "# Discovery intake verdicts — 2026-07\n\n"
            f"- 2026-07-01 [older]({url}) -> dismissed: old reading "
            "(20260702T000000Z)\n", encoding="utf-8")
        dest, _manifest = self.build("chronological")
        candidate = DiscoveryStore(dest).load_candidate("x:123")
        self.assertEqual("registered", candidate["verdict"]["kind"])
        self.assertEqual("later-source", candidate["verdict"]["source_id"])
        self.assertEqual(
            ["dismissed", "registered"],
            [row["kind"] for row in candidate["verdict_history"]])
        self.assertEqual(1, candidate["queue_rank"])

    def test_later_legacy_retry_explicitly_reopens_an_old_verdict(self) -> None:
        url = "https://x.com/alice/status/123"
        (self.root / "DISCOVERY.md").write_text(
            "# Discovery intake\n\n## Assessed\n\n"
            f"- 2026-08-11 [later retry]({url}) -> Pending: body fetch failed "
            "(20260811T000000Z)\n", encoding="utf-8")
        (self.root / "discovery/assessed-2026-07.md").write_text(
            "# Discovery intake verdicts — 2026-07\n\n"
            f"- 2026-07-01 [older]({url}) -> dismissed: old reading "
            "(20260702T000000Z)\n", encoding="utf-8")
        dest, manifest = self.build("reopened")
        candidate = DiscoveryStore(dest).load_candidate("x:123")
        self.assertEqual("pending", candidate["state"])
        self.assertNotIn("verdict", candidate)
        self.assertEqual("dismissed", candidate["verdict_history"][0]["kind"])
        self.assertEqual(
            candidate["verdict_history"][0]["event_id"],
            candidate["state_reason"]["supersedes"])
        self.assertEqual(
            1, len(manifest["repairs"]["verdict_reopened_by_later_retry"]))

    def test_final_verdict_facts_exclude_a_reopened_candidate(self) -> None:
        dest, _manifest = self.build("final-facts")
        store = DiscoveryStore(dest)
        assessed = store.load_candidate("stackernews:99")
        store.set_state(
            "stackernews:99", "pending", reason="operator recheck",
            supersedes=assessed["verdict"]["event_id"],
            at="20260815T000000Z")
        final_ids = {row["candidate_id"] for row in verdict_facts(dest)}
        historical_ids = {
            row["candidate_id"]
            for row in verdict_facts(dest, final_only=False)
        }
        self.assertNotIn("stackernews:99", final_ids)
        self.assertIn("stackernews:99", historical_ids)

    def test_migration_manifest_types_and_timestamp_are_canonical(self) -> None:
        for name, mutate, message in (
                ("boolean-schema",
                 lambda value: value.__setitem__("schema", True),
                 "unsupported discovery migration manifest"),
                ("iso-created",
                 lambda value: value.__setitem__(
                     "created_at", "2026-08-14T00:00:00Z"),
                 "timestamp is not canonical")):
            with self.subTest(case=name):
                dest, _manifest = self.build(name)
                marker = DiscoveryStore(dest).marker
                value = json.loads(marker.read_text(encoding="utf-8"))
                mutate(value)
                marker.write_text(pretty_json(value), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    validate_store(dest)

    def test_validator_derives_repairs_instead_of_trusting_builder(self) -> None:
        original = migration._migration_events

        def omit_repairs(store, rows):
            events, repairs, semantics = original(store, rows)
            return events, {key: [] for key in repairs}, semantics

        dest = self.root / "missing-repairs"
        dest.mkdir()
        with mock.patch.object(
                migration, "_migration_events", side_effect=omit_repairs):
            with self.assertRaisesRegex(ValueError, "repair inventory"):
                migration.build(
                    self.root, dest, source_commit="fixture",
                    created_at="20260814T000000Z")

    def test_validator_rejects_noncanonical_legacy_source_order(self) -> None:
        original = migration.read_all

        def reverse_sources(root):
            rows, sources = original(root)
            return rows, list(reversed(sources))

        dest = self.root / "reversed-sources"
        dest.mkdir()
        with mock.patch.object(migration, "read_all", side_effect=reverse_sources):
            with self.assertRaisesRegex(ValueError, "source order"):
                migration.build(
                    self.root, dest, source_commit="fixture",
                    created_at="20260814T000000Z")

    def test_migration_bundle_is_bound_into_canonical_transactions(self) -> None:
        dest, _manifest = self.build()
        marker = DiscoveryStore(dest).marker
        value = json.loads(marker.read_text(encoding="utf-8"))
        held = dest / "discovery/migration-v1/legacy/DISCOVERY.md"
        raw = held.read_bytes().replace(
            b"# Discovery intake\n", b"# Discovery archive\n", 1)
        held.write_bytes(raw)
        value["source_files"][0]["bytes"] = len(raw)
        value["source_files"][0]["sha256"] = __import__("hashlib").sha256(
            raw).hexdigest()
        marker.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n",
                          encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bundle root changed"):
            validate_store(dest)

    def test_migration_transaction_operation_is_bundle_bound(self) -> None:
        dest, _manifest = self.build()
        store = DiscoveryStore(dest)
        path = next(store.transactions.rglob("*.json"))
        transaction = json.loads(path.read_text(encoding="utf-8"))
        transaction["operation_id"] = "migration-v1:" + "0" * 64 + ":0001"
        core = {key: value for key, value in transaction.items()
                if key != "transaction_id"}
        transaction["transaction_id"] = digest(core)
        replacement = path.with_name(
            f"00000001-{transaction['transaction_id']}.json")
        replacement.write_text(json.dumps(
            transaction, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        path.unlink()
        marker = json.loads(store.marker.read_text(encoding="utf-8"))
        marker["migration_transactions"] = [transaction["transaction_id"]]
        store.marker.write_text(
            json.dumps(marker, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not bundle-bound"):
            validate_store(dest)

    def test_interrupted_directory_activation_restores_rotated_inputs(self) -> None:
        built, _manifest = self.build()
        live = self.root / "discovery"
        original_replace = os.replace

        class PowerLoss(BaseException):
            pass

        def stop_after_legacy_move(source, destination):
            original_replace(source, destination)
            if Path(source) == live:
                raise PowerLoss()

        with mock.patch.object(migration.os, "replace",
                               side_effect=stop_after_legacy_move):
            with self.assertRaises(PowerLoss):
                migration.replace_generated(self.root, built)
        self.assertFalse(live.exists())
        self.assertTrue((self.root / ".work/discovery-migration-install.json").is_file())

        self.assertTrue(migration.recover_install(self.root))
        restored = self.root / "discovery/assessed-2026-07.md"
        self.assertEqual(restored.read_text(encoding="utf-8"), ROTATED)
        rebuilt = self.root / "rebuilt-after-recovery"
        rebuilt.mkdir()
        manifest = migration.build(
            self.root, rebuilt, source_commit="fixture",
            created_at="20260814T000000Z")
        self.assertEqual(manifest["legacy_entries"], 9)

    def test_post_activation_fsync_failure_keeps_journal_for_recovery(self) -> None:
        built, manifest = self.build()
        original_fsync_directory = migration._fsync_directory

        def fail_after_live_activation(path: Path) -> None:
            original_fsync_directory(path)
            if migration.migration_is_installed(self.root):
                raise OSError("simulated post-activation fsync failure")

        with mock.patch.object(
                migration, "_fsync_directory",
                side_effect=fail_after_live_activation):
            with self.assertRaisesRegex(OSError, "post-activation"):
                migration.replace_generated(self.root, built)

        journal = self.root / ".work/discovery-migration-install.json"
        self.assertTrue(journal.is_file())
        self.assertTrue(migration.migration_is_installed(self.root))
        self.assertIn("## Pending", (self.root / "DISCOVERY.md").read_text())

        self.assertTrue(migration.recover_install(self.root))
        self.assertFalse(journal.exists())
        self.assertNotIn("## Pending", (self.root / "DISCOVERY.md").read_text())
        self.assertEqual(validate_store(self.root)["migration"], manifest)

    def test_exact_bytes_occurrences_and_duplicate_identity_are_preserved(self) -> None:
        dest, manifest = self.build()
        self.assertEqual(manifest["legacy_entries"], 9)
        self.assertEqual(validate_migration(dest), manifest)
        held = dest / "discovery/migration-v1/legacy/DISCOVERY.md"
        self.assertEqual(held.read_bytes(), LEGACY.encode("utf-8"))
        candidate = DiscoveryStore(dest).load_candidate("x:123")
        self.assertEqual(len(candidate["observations"]), 2)
        self.assertEqual(
            [row["legacy_line_number"] for row in candidate["observations"]],
            [5, 6])

    def test_every_transition_is_replayed_including_correction_and_retry(self) -> None:
        dest, manifest = self.build()
        store = DiscoveryStore(dest)
        retry = store.load_candidate("x:456")
        self.assertEqual(retry["state"], "pending")
        self.assertEqual(retry["retry_history"][0]["reason"], "body fetch failed")
        accepted = store.load_candidate("reddit:abc123")
        self.assertEqual([row["reason"] for row in accepted["retry_history"]],
                         ["body fetch failed"])
        self.assertEqual(accepted["verdict"]["source_id"], "reddit-accepted")
        corrected = store.list_candidates(platform="nostr")[0]
        self.assertEqual([row["kind"] for row in corrected["verdict_history"]],
                         ["dismissed", "registered"])
        self.assertEqual(corrected["verdict"]["source_id"], "nostr-corrected")
        self.assertEqual(
            len(manifest["repairs"]["multi_transition_lines_preserved"]), 2)
        self.assertEqual(
            [row["type"] for row in corrected["event_history"]],
            ["observation", "verdict", "verdict"])
        self.assertEqual(
            corrected["event_history"][-1]["supersedes"],
            corrected["verdict_history"][0]["event_id"])

    def test_source_reference_inventory_is_derived_from_baseline_events(self) -> None:
        incomplete = {
            "referenced": ["nostr-corrected", "reddit-accepted"],
            "live": [],
            "quarantined": [],
            "unresolved": ["nostr-corrected", "reddit-accepted"],
        }
        dest = self.root / "bad-source-inventory"
        dest.mkdir()
        with mock.patch.object(
                migration, "_source_reference_resolution",
                return_value=incomplete):
            with self.assertRaisesRegex(ValueError, "source references disagree"):
                migration.build(
                    self.root, dest, source_commit="fixture",
                    created_at="20260814T000000Z")

    def test_validator_independently_reparses_held_verdicts(self) -> None:
        dest = self.root / "broken-parser-build"
        dest.mkdir()
        with mock.patch.object(migration, "line_actions", return_value=[]):
            with self.assertRaisesRegex(
                    ValueError, "occurrence (actions|semantics) disagree"):
                migration.build(
                    self.root, dest, source_commit="fixture",
                    created_at="20260814T000000Z")

    def test_validator_binds_the_exact_legacy_url_to_observation(self) -> None:
        dest = self.root / "altered-observation-url"
        dest.mkdir()
        original = DiscoveryStore._observation_event

        def alter_url(raw, state, at, *, strict_identity):
            changed = dict(raw)
            if changed.get("url") == "https://x.com/alice/status/123":
                changed["url"] = "https://twitter.com/mallory/status/123"
            return original(
                changed, state, at, strict_identity=strict_identity)

        with mock.patch.object(
                DiscoveryStore, "_observation_event",
                side_effect=alter_url):
            with self.assertRaisesRegex(ValueError, "URL|url|provenance"):
                migration.build(
                    self.root, dest, source_commit="fixture",
                    created_at="20260814T000000Z")

    def test_registered_annotation_and_rotated_writer_shape_are_parsed(self) -> None:
        dest, _manifest = self.build()
        store = DiscoveryStore(dest)
        self.assertEqual(store.load_candidate("stackernews:99")["verdict"]["source_id"],
                         "stackernews-accepted")
        self.assertEqual(store.load_candidate("reddit:old123")["state"], "assessed")

    def test_missing_url_gets_stable_record_without_losing_line(self) -> None:
        first, manifest = self.build()
        legacy = DiscoveryStore(first).list_candidates(platform="legacy")
        self.assertEqual(len(legacy), 1)
        self.assertIn("historical broken row", legacy[0]["observations"][0]["legacy_line"])
        second, manifest_two = self.build("built-again")
        self.assertEqual(legacy[0]["identity"],
                         DiscoveryStore(second).list_candidates(platform="legacy")[0]["identity"])
        self.assertEqual(manifest["migration_semantic_root"],
                         manifest_two["migration_semantic_root"])

    def test_validator_rejects_legacy_bundle_or_projection_tampering(self) -> None:
        dest, _manifest = self.build()
        held = dest / "discovery/migration-v1/legacy/DISCOVERY.md"
        held.write_text(held.read_text() + "changed\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "copy changed"):
            validate_store(dest)

        other, _manifest = self.build("other")
        projection = DiscoveryStore(other).candidate_path("x:123")
        projection.write_text("{}\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "generated discovery file differs"):
            validate_store(other)

    def test_validator_rejects_extra_authoritative_or_schema_files(self) -> None:
        for relative, message in (
                ("discovery/fake-authoritative.json", "namespace differs"),
                ("discovery/schema/extra.schema.json", "schema namespace")):
            with self.subTest(relative=relative):
                dest, _manifest = self.build(
                    "extra-" + Path(relative).name.replace(".", "-"))
                path = dest / relative
                path.write_text("{}\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    validate_store(dest)

    def test_install_activates_new_marker_and_refuses_reingestion(self) -> None:
        built, manifest = self.build()
        migration.replace_generated(self.root, built)
        self.assertEqual(validate_store(self.root)["migration"], manifest)
        self.assertTrue(migration.migration_is_installed(self.root))
        self.assertNotIn("## Pending", (self.root / "DISCOVERY.md").read_text())
        self.assertEqual(
            migration.main(["--root", str(self.root), "--write"]), 2)

    def test_activation_refuses_a_bundle_that_omitted_a_live_source(self) -> None:
        original_read_all = migration.read_all

        def omit_rotated(root: Path):
            rows, sources = original_read_all(root)
            kept_sources = [row for row in sources
                            if row["path"] == "DISCOVERY.md"]
            kept_rows = [row for row in rows
                         if row["path"] == "DISCOVERY.md"]
            return kept_rows, kept_sources

        dest = self.root / "incomplete-build"
        dest.mkdir()
        with mock.patch.object(migration, "read_all", side_effect=omit_rotated):
            migration.build(
                self.root, dest, source_commit="fixture",
                created_at="20260814T000000Z")
        rotated = self.root / "discovery/assessed-2026-07.md"
        before = rotated.read_bytes()
        with self.assertRaisesRegex(ValueError, "does not cover every"):
            migration.replace_generated(self.root, dest)
        self.assertEqual(rotated.read_bytes(), before)
        self.assertFalse(migration.migration_is_installed(self.root))

    def test_generated_views_are_small_shards_and_legacy_export_is_available(self) -> None:
        dest, _manifest = self.build()
        store = DiscoveryStore(dest)
        pending = dest / "discovery/views/pending/x-001.md"
        self.assertTrue(pending.is_file())
        self.assertIn("@bob repost", pending.read_text())
        exported = store.export_legacy()
        self.assertIn("## Pending", exported)
        self.assertIn("registered as nostr-corrected", exported)
        lines = load_intake_verdict_lines(dest)
        self.assertTrue(any("stackernews-accepted" in line for line in lines))

    def test_every_generated_markdown_page_and_candidate_record_is_linked(self) -> None:
        lines = [
            f"- 2026-08-01 [item {number}]"
            f"(https://x.com/a/status/{1000 + number}) (X @a)"
            for number in range(105)
        ]
        (self.root / "DISCOVERY.md").write_text(
            "# Discovery intake\n\n## Pending\n\n" + "\n".join(lines) +
            "\n\n## Assessed\n\n## Deferred\n", encoding="utf-8")
        (self.root / "discovery/assessed-2026-07.md").unlink()
        dest, _manifest = self.build("reachable")
        pending = dest / "discovery/views/pending"
        self.assertTrue((pending / "x-002.md").is_file())

        markdown_files = {dest / "DISCOVERY.md", dest / "discovery/README.md"}
        markdown_files.update((dest / "discovery/views").rglob("*.md"))
        reached: set[Path] = set()
        todo = [dest / "DISCOVERY.md"]
        while todo:
            current = todo.pop()
            current = current.resolve()
            if current in reached:
                continue
            reached.add(current)
            for target in re.findall(
                    r"\]\(([^)#]+)(?:#[^)]*)?\)",
                    current.read_text(encoding="utf-8")):
                if "://" in target:
                    continue
                resolved = (current.parent / target).resolve()
                if target == "docs/DISCOVERY.md":
                    # This generated tree is a standalone rehearsal. The link
                    # resolves after atomic activation at repository root.
                    continue
                self.assertTrue(resolved.is_file(),
                                f"broken discovery link: {current} -> {target}")
                if resolved.suffix == ".md":
                    todo.append(resolved)
        self.assertEqual({path.resolve() for path in markdown_files}, reached)
        for view in (dest / "discovery/views").rglob("*.md"):
            for target in re.findall(r"\[record/history\]\(([^)]+)\)",
                                     view.read_text(encoding="utf-8")):
                self.assertTrue((view.parent / target).resolve().is_file())

    def test_cli_reads_and_validates_installed_store(self) -> None:
        built, _manifest = self.build()
        migration.replace_generated(self.root, built)
        script = ROOT / "scripts/discovery_store.py"

        def run(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                [sys.executable, str(script), "--root", str(self.root), *args],
                text=True, capture_output=True, check=True)

        self.assertEqual(run("count", "--state", "pending").stdout.strip(), "2")
        self.assertIn("x:123", run("list", "--platform", "x").stdout)
        shown = json.loads(run("show", "x:123").stdout)
        self.assertEqual(shown["native_id"], "123")
        self.assertIn("valid:", run("validate").stdout)

    def test_schema_and_semantic_root_are_deterministic(self) -> None:
        first, one = self.build()
        second, two = self.build("second")
        self.assertEqual(one["migration_semantic_root"],
                         two["migration_semantic_root"])
        self.assertEqual(one["migration_transactions"],
                         two["migration_transactions"])
        self.assertEqual(
            (first / "DISCOVERY.md").read_bytes(),
            (second / "DISCOVERY.md").read_bytes())
        self.assertEqual(validate_store(first)["candidates"], 8)


if __name__ == "__main__":
    unittest.main()
