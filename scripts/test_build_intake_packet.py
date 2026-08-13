#!/usr/bin/env python3
"""Tests for deterministic, scoped intake evidence packets."""
from __future__ import annotations

import json
import builtins
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_intake_packet as bip  # noqa: E402

try:  # The packet adapter can ship before the structured discovery store.
    import discovery_store  # type: ignore  # noqa: E402
except ImportError:  # pragma: no cover - exercised when this slice stands alone
    discovery_store = None

SCRIPT = Path(__file__).with_name("build_intake_packet.py")


def queue(url: str, title: str = "candidate") -> str:
    return f"- 2026-08-12 [{title}]({url}) by author, 3 comments (fixture)"


def hydrated(lines: list[str], bodies: list[str | None], *,
             nonce: str = "abc123", states: list[str] | None = None) -> str:
    states = states or ["complete" if body is not None else "failed"
                        for body in bodies]
    out: list[str] = []
    for index, (line, body, state) in enumerate(zip(lines, bodies, states), 1):
        out.extend([
            f"### Candidate {index}",
            f"Queue line: {line}",
            "Platform: fixture (id fixture)",
        ])
        if state in {"complete", "truncated"}:
            out.extend([
                f"Body: hydrated, {state}",
                f"<<<UNTRUSTED-{nonce}",
                body or "",
                f"UNTRUSTED-{nonce}>>>",
            ])
        elif state == "failed":
            out.append("Body: fetch failed (fixture fetch failed)")
            out.append("Leave this candidate Pending and report the failure.")
        elif state == "not-hydrated":
            out.append("Body: not hydrated (fixture unsupported URL)")
        else:
            raise AssertionError(state)
        out.append("")
    return "\n".join(out)


REGISTRY = """\
[[source]]
id = "stacker-existing"
title = "Existing Stacker News discussion"
url = "https://stacker.news/items/111"
org = "Stacker News"

[[source]]
id = "reddit-existing"
title = "Existing Reddit discussion"
url = "https://old.reddit.com/r/Bitcoin/comments/abc123/a_title/"
org = "reddit"

[[source]]
id = "bitcointalk-existing"
title = "Existing BitcoinTalk topic"
url = "https://bitcointalk.org/index.php?foo=1&topic=222.40"
org = "BitcoinTalk"

[[x_post]]
id = "x-existing"
title = "Existing X statement"
url = "https://x.com/old_handle/status/333"
author = "old_handle"

[[nostr_post]]
id = "nostr-existing"
title = "Existing nostr note"
url = "https://njump.me/note1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"
author = "npub1fixture"
"""

DISCOVERY = """\
# Discovery intake

## Pending

## Assessed

- 2026-08-01 [old](https://example.invalid/old) -> dismissed: already represented by reddit-existing (20260801T000000Z)

## Deferred
"""


class PacketFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "discovery").mkdir()
        self.registry = self.root / "sources.toml"
        self.discovery = self.root / "DISCOVERY.md"
        self.registry.write_text(REGISTRY, encoding="utf-8")
        self.discovery.write_text(DISCOVERY, encoding="utf-8")
        self.candidates = self.root / "candidates.txt"
        self.hydrated = self.root / "hydrated.md"

    def build(self, lines: list[str], bodies: list[str | None], *,
              lane: str = "community", nonce: str = "abc123",
              states: list[str] | None = None,
              max_bytes: int = 100_000) -> tuple[dict, str]:
        self.candidates.write_text("\n".join(lines) + "\n", encoding="utf-8")
        text = hydrated(lines, bodies, nonce=nonce, states=states)
        self.hydrated.write_text(text, encoding="utf-8")
        return bip.build_packet(
            root=self.root, lane=lane, candidates_path=self.candidates,
            hydrated_path=self.hydrated, registry_path=self.registry,
            discovery_path=self.discovery,
            rotated_dir=self.root / "discovery",
            max_markdown_bytes=max_bytes)


class CanonicalKeyTests(unittest.TestCase):
    def test_every_intake_platform_has_a_native_external_key(self) -> None:
        cases = {
            "https://x.com/new_handle/status/333?ref=home":
                ("x", "x:status:333"),
            "https://twitter.com/other_handle/status/333/":
                ("x", "x:status:333"),
            "https://www.reddit.com/r/Bitcoin/comments/ABC123/changed_slug/":
                ("reddit", "reddit:submission:abc123"),
            "https://redd.it/AbC123":
                ("reddit", "reddit:submission:abc123"),
            "https://stacker.news/items/111/":
                ("stackernews", "stackernews:item:111"),
            "https://bitcointalk.org/index.php?topic=222.80&all=1":
                ("bitcointalk", "bitcointalk:topic:222"),
            "https://njump.me/note1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq":
                ("nostr", "nostr:note:note1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq"),
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(expected, bip.canonical_external_key(url))

    def test_unknown_permalink_fails_instead_of_getting_a_fuzzy_key(self) -> None:
        with self.assertRaises(bip.PacketError):
            bip.canonical_external_key("https://example.invalid/a-post")


class PacketContentTests(PacketFixture):
    def test_exact_matches_use_native_identity_not_url_spelling(self) -> None:
        lines = [
            queue("https://stacker.news/items/111/", "stacker"),
            queue("https://www.reddit.com/r/Other/comments/ABC123/new_slug/", "reddit"),
            queue("https://bitcointalk.org/index.php?topic=222.99", "bt"),
            queue("https://njump.me/note1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq", "nostr"),
        ]
        packet, _markdown = self.build(lines, ["a", "b", "c", "d"])
        matches = [candidate["registry_exact_match"]["source_ids"]
                   for candidate in packet["candidates"]]
        self.assertEqual([
            ["stacker-existing"], ["reddit-existing"],
            ["bitcointalk-existing"], ["nostr-existing"],
        ], matches)

        xline = queue("https://x.com/new_handle/status/333", "x")
        xpacket, _ = self.build([xline], ["x body"], lane="x")
        self.assertEqual(["x-existing"],
                         xpacket["candidates"][0]["registry_exact_match"]
                         ["source_ids"])

    def test_candidate_record_has_stable_ids_hashes_and_body(self) -> None:
        line = queue("https://stacker.news/items/999", "new item")
        packet, markdown = self.build([line], ["body text"])
        candidate = packet["candidates"][0]
        self.assertEqual("stackernews:item:999", candidate["external_key"])
        self.assertEqual("stackernews:999", candidate["candidate_id"])
        self.assertEqual("999", candidate["native_id"])
        self.assertEqual(bip.sha256_text(line), candidate["queue_line_sha256"])
        self.assertEqual(bip.sha256_text("body text"), candidate["body_sha256"])
        self.assertEqual("body text", candidate["body"])
        self.assertEqual("complete", candidate["hydration_status"])
        self.assertFalse(candidate["registry_exact_match"]["matched"])
        # The old prompt carried it once in CANDIDATES and once in HYDRATED.
        self.assertEqual(1, markdown.count(line))

    def test_failed_and_truncated_hydration_are_explicit(self) -> None:
        lines = [
            queue("https://stacker.news/items/991", "failed"),
            queue("https://stacker.news/items/992", "truncated"),
        ]
        packet, _ = self.build(lines, [None, "partial body"],
                               states=["failed", "truncated"])
        failed, truncated = packet["candidates"]
        self.assertEqual("failed", failed["hydration_status"])
        self.assertEqual("fixture fetch failed", failed["hydration_detail"])
        self.assertIsNone(failed["body"])
        self.assertIsNone(failed["body_sha256"])
        self.assertEqual("truncated", truncated["hydration_status"])
        self.assertEqual("partial body", truncated["body"])

    def test_candidate_shaped_heading_inside_body_does_not_split_packet(self) -> None:
        line = queue("https://stacker.news/items/999", "new item")
        body = "first line\n### Candidate 2\nthis is untrusted body text"
        packet, _ = self.build([line], [body])
        self.assertEqual(1, len(packet["candidates"]))
        self.assertEqual(body, packet["candidates"][0]["body"])

    def test_lane_mismatch_fails(self) -> None:
        line = queue("https://x.com/person/status/999", "x")
        with self.assertRaises(bip.PacketError):
            self.build([line], ["body"], lane="community")


class SaturationTests(PacketFixture):
    @unittest.skipIf(discovery_store is None,
                     "structured discovery adapter is not installed")
    def test_valid_migration_marker_activates_structured_verdict_adapter(self) -> None:
        line = ("- 2026-08-01 [structured](https://stacker.news/items/444) "
                "-> dismissed: already represented by stacker-existing "
                "(20260801T000000Z)")
        store = discovery_store.DiscoveryStore(self.root)
        candidate = store.record_observation({
            "url": "https://stacker.news/items/444",
            "legacy_line": line,
            "legacy_candidate_line": line.split(" -> ", 1)[0],
            "legacy_path": str(self.discovery),
            "legacy_line_number": 5,
        }, state="assessed", event_at="20260801T000000Z")
        store.record_verdict(
            candidate["identity"], "dismissed",
            reason="already represented by stacker-existing",
            at="20260801T000000Z", legacy_line=line)
        manifest = {
            "schema": 1,
            "source_files": [{
                "path": "DISCOVERY.md", "sha256": "0" * 64, "entries": 1,
            }],
            "legacy_entries": 1,
            "candidate_records": 1,
            "event_records": 2,
            "legacy_lines_sha256": discovery_store.digest([line]),
            "every_legacy_occurrence_preserved": True,
            "missing_legacy_occurrences": [],
        }
        (self.root / "discovery" / "migration-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8")
        self.assertEqual([line], bip.load_assessed_lines(self.root))

    def test_invalid_migration_marker_fails_instead_of_resetting_history(self) -> None:
        (self.root / "discovery" / "migration-manifest.json").write_text(
            '{"schema":1}', encoding="utf-8")
        with self.assertRaises(bip.PacketError):
            bip.load_assessed_lines(self.root)

    def test_unmigrated_discovery_store_module_does_not_shadow_legacy(self) -> None:
        # The new module and the data migration can land in separate commits.
        # Until the migration manifest exists, legacy DISCOVERY.md remains
        # authoritative even if a partial candidates/ tree has appeared.
        (self.root / "discovery" / "candidates" / "reddit").mkdir(parents=True)
        lines = bip.load_assessed_lines(self.root)
        self.assertTrue(any("reddit-existing" in line for line in lines))

    def test_no_discovery_module_or_marker_uses_legacy_ledger(self) -> None:
        """The minimal packet slice has no hard dependency on reorganization."""
        real_import = builtins.__import__

        def without_discovery(name, *args, **kwargs):
            if name == "discovery_store":
                raise ImportError("fixture: structured discovery unavailable")
            return real_import(name, *args, **kwargs)

        old_import = builtins.__import__
        try:
            builtins.__import__ = without_discovery
            lines = bip.load_assessed_lines(self.root)
        finally:
            builtins.__import__ = old_import
        self.assertTrue(any("reddit-existing" in line for line in lines))

    def test_only_complete_nonzero_rows_are_included(self) -> None:
        line = queue("https://stacker.news/items/999", "new")
        packet, markdown = self.build([line], ["body"])
        coverage = packet["coverage"]
        self.assertEqual(5, coverage["total_registry_entries"])
        self.assertEqual(1, coverage["included_nonzero_entries"])
        self.assertEqual(4, coverage["omitted_zero_entries"])
        self.assertEqual("reddit-existing", coverage["rows"][0]["id"])
        self.assertNotIn("stacker-existing` (absorbed", markdown)
        self.assertEqual(
            bip.sha256_bytes(bip.canonical_json_bytes(coverage)),
            packet["source_ledger_hashes"]["saturation_semantic_sha256"])

    def test_actual_rotated_header_only_format_contributes_history(self) -> None:
        (self.root / "discovery" / "assessed-2026-07.md").write_text(
            "# Discovery intake verdicts — 2026-07\n\n"
            "Assessed entries rotated out of DISCOVERY.md by the rotation.\n\n"
            "- 2026-07-30 [old](https://example.invalid/2) -> dismissed: "
            "duplicate of stacker-existing (20260730T000000Z)\n",
            encoding="utf-8")
        packet, _ = self.build(
            [queue("https://stacker.news/items/999", "new")], ["body"])
        counts = {row["id"]: row["absorbed"]
                  for row in packet["coverage"]["rows"]}
        self.assertEqual(1, counts["stacker-existing"])
        self.assertEqual(1, counts["reddit-existing"])

    def test_storage_order_and_fence_nonce_do_not_change_semantic_hashes(self) -> None:
        line = queue("https://stacker.news/items/999", "new")
        first, first_md = self.build([line], ["same body"], nonce="aaaa")
        # Sharding and later consolidation may change physical table order.
        # The ledger hash describes records, not where a serializer placed
        # them, so a pure reorder is semantically identical.
        blocks = REGISTRY.strip().split("\n\n")
        self.registry.write_text("\n\n".join(reversed(blocks)) + "\n",
                                 encoding="utf-8")
        second, second_md = self.build([line], ["same body"], nonce="bbbb")
        self.assertEqual(first, second)
        self.assertEqual(first_md, second_md)
        self.assertEqual(
            bip.sha256_bytes(bip.canonical_json_bytes(first)),
            bip.sha256_bytes(bip.canonical_json_bytes(second)))


class SizeAndCliTests(PacketFixture):
    def test_size_report_is_deterministic(self) -> None:
        line = queue("https://stacker.news/items/999", "new")
        packet, markdown = self.build([line], ["body"])
        report = packet["size_report"]
        self.assertEqual(len(markdown.encode("utf-8")),
                         report["markdown_bytes"])
        self.assertTrue(report["within_limit"])
        self.assertGreater(report["coverage_markdown_bytes"], 0)
        self.assertEqual(4, report["hydrated_body_bytes"])

    def test_cli_keeps_oversize_outputs_for_inspection_and_exits_two(self) -> None:
        line = queue("https://stacker.news/items/999", "new")
        self.candidates.write_text(line + "\n", encoding="utf-8")
        self.hydrated.write_text(hydrated([line], ["x" * 500]),
                                 encoding="utf-8")
        json_out = self.root / "packet.json"
        md_out = self.root / "packet.md"
        result = subprocess.run([
            sys.executable, str(SCRIPT),
            "--lane", "community",
            "--candidates", str(self.candidates),
            "--hydrated", str(self.hydrated),
            "--registry", str(self.registry),
            "--discovery", str(self.discovery),
            "--rotated-dir", str(self.root / "discovery"),
            "--json-out", str(json_out),
            "--markdown-out", str(md_out),
            "--max-markdown-bytes", "200",
        ], text=True, capture_output=True, check=False)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("exceeds", result.stderr)
        self.assertTrue(json_out.exists())
        self.assertTrue(md_out.exists())
        packet = json.loads(json_out.read_text(encoding="utf-8"))
        self.assertFalse(packet["size_report"]["within_limit"])
        self.assertGreater(packet["size_report"]["markdown_bytes"], 200)


if __name__ == "__main__":
    unittest.main()
