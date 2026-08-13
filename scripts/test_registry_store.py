#!/usr/bin/env python3
"""Regression tests for the sharded registry and its lossless converter."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock

import migrate_registry
import registry_store


SAMPLE = """# Registry preamble stays with metadata.
[meta]
incident = "test-record"
title = "Test"

# The source list is globally ordered, not filename ordered.
[[source]]
id = "zulu-source"
title = "Zulu"
url = "https://example.com/zulu"
note = '''A multiline value may contain a header-looking line:
[[x_post]]
but it is not a record boundary.
'''

# Watch separator comment remains byte-for-byte present.
[[x_watch]]
handle = "Watched_User"
since = "2026-07-29"
why = "A watched test account."

[[x_post]]
id = "post_1"
title = "Post"
url = "https://x.com/example/status/1"
author = "example"
posted = "2026-07-30T00:00:00Z"
why = "A test post."

[[source]]
id = "alpha-source"
title = "Alpha"
url = "https://example.com/alpha"

[[nostr_post]]
id = "note-1"
title = "Note"
url = "https://njump.me/note1example"
author = "npub1example"
posted = "2026-07-30T00:00:00Z"
why = "A test note."
"""


class RegistryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.legacy = self.root / "sources.toml"
        self.legacy.write_text(SAMPLE, encoding="utf-8")
        self.registry = self.root / "registry"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self) -> dict:
        return migrate_registry.build_tree(self.legacy, self.registry)


class LegacyFallbackTests(RegistryTestCase):
    def test_readme_only_tree_falls_back_to_legacy_file(self) -> None:
        self.registry.mkdir()
        (self.registry / "README.md").write_text("transition", encoding="utf-8")
        loaded = registry_store.load(self.root)
        self.assertEqual(["zulu-source", "alpha-source"], [x["id"] for x in loaded["source"]])

    def test_direct_legacy_path_is_supported_for_small_fixtures(self) -> None:
        self.assertEqual("test-record", registry_store.load(self.legacy)["meta"]["incident"])

    def test_unsafe_legacy_key_is_rejected_before_it_can_become_a_path(self) -> None:
        self.legacy.write_text(SAMPLE.replace('id = "zulu-source"', 'id = "../escape"', 1))
        with self.assertRaisesRegex(registry_store.RegistryError, "must match"):
            registry_store.load(self.root)

    def test_stable_key_contract_accepts_current_id_shape_only(self) -> None:
        for valid in ("a", "A_1", "post-name", "Watched_User"):
            self.assertEqual(valid, registry_store.validate_stable_key(valid))
        for invalid in ("", "-leading", "two words", "../escape", "a/b", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(registry_store.RegistryError):
                    registry_store.validate_stable_key(invalid)


class ConversionTests(RegistryTestCase):
    def test_split_is_exact_and_ignores_header_text_inside_multiline_strings(self) -> None:
        meta, fragments, parsed = migrate_registry.split_legacy(SAMPLE)
        self.assertEqual(5, len(fragments))
        self.assertEqual(SAMPLE, meta + "".join(fragment.text for fragment in fragments))
        self.assertEqual(
            ["source", "x_watch", "x_post", "source", "nostr_post"],
            [fragment.table for fragment in fragments],
        )
        self.assertEqual("[[x_post]]", parsed["source"][0]["note"].splitlines()[1])

    def test_generated_tree_preserves_semantics_global_order_and_comments(self) -> None:
        manifest = self.build()
        legacy = registry_store.load_legacy(self.legacy)
        sharded = registry_store.load_shards(self.registry)
        self.assertTrue(registry_store.semantic_equal(legacy, sharded))
        self.assertEqual(list(legacy), list(sharded))
        self.assertEqual(["zulu-source", "alpha-source"], [x["id"] for x in sharded["source"]])
        self.assertTrue((self.registry / "sources" / "zulu-source.toml").read_text().startswith(
            "# registry-order: 1\n\n# The source list is globally ordered"
        ))
        self.assertEqual(5, len(manifest["fragments"]))
        self.assertEqual(
            hashlib.sha256(SAMPLE.encode()).hexdigest(), manifest["legacy"]["sha256"]
        )
        migrate_registry.verify_tree(self.legacy, self.registry)

    def test_changed_legacy_file_takes_precedence_over_stale_shards(self) -> None:
        self.build()
        self.legacy.write_text(SAMPLE.replace('title = "Test"', 'title = "Changed"'))
        status = registry_store.registry_status(self.root)
        self.assertFalse(status.current)
        self.assertIn("newer or different", status.reason)
        self.assertEqual("Changed", registry_store.load(self.root)["meta"]["title"])

    def test_currentness_helper_accepts_only_a_complete_verified_tree(self) -> None:
        self.build()
        status = registry_store.registry_status(self.root)
        self.assertTrue(status.current, status.reason)
        self.assertTrue(registry_store.shards_current(self.root))
        self.assertEqual("manifest and shards match legacy", status.reason)

    def test_missing_or_partial_manifest_falls_back_to_legacy(self) -> None:
        self.build()
        (self.registry / "manifest.json").unlink()
        self.assertFalse(registry_store.shards_current(self.root))
        self.assertEqual("Test", registry_store.load(self.root)["meta"]["title"])

    def test_tampered_fragment_falls_back_to_legacy(self) -> None:
        self.build()
        fragment = self.registry / "sources" / "zulu-source.toml"
        fragment.write_text(fragment.read_text().replace('title = "Zulu"',
                                                         'title = "Tampered"'))
        status = registry_store.registry_status(self.root)
        self.assertFalse(status.current)
        self.assertIn("manifest file differs", status.reason)
        loaded = registry_store.load(self.root)
        self.assertEqual("Zulu", loaded["source"][0]["title"])

    def test_unsafe_manifest_path_never_becomes_authoritative(self) -> None:
        self.build()
        manifest_path = self.registry / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["fragments"][0]["path"] = "../outside.toml"
        manifest_path.write_text(json.dumps(manifest))
        status = registry_store.registry_status(self.root)
        self.assertFalse(status.current)
        self.assertIn("unsafe", status.reason)
        self.assertEqual("Zulu", registry_store.load(self.root)["source"][0]["title"])

    def test_manifest_semantic_claim_must_match_the_current_legacy_meaning(self) -> None:
        self.build()
        manifest_path = self.registry / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["semantic_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        status = registry_store.registry_status(self.root)
        self.assertFalse(status.current)
        self.assertIn("semantic hash differs", status.reason)
        self.assertEqual("Zulu", registry_store.load(self.root)["source"][0]["title"])

    def test_fragment_filename_must_match_the_record_key(self) -> None:
        self.build()
        original = self.registry / "sources" / "zulu-source.toml"
        original.rename(original.with_name("wrong-name.toml"))
        with self.assertRaisesRegex(registry_store.RegistryError, "does not match record key"):
            registry_store.load_shards(self.registry)

    def test_order_values_are_unique_and_contiguous(self) -> None:
        self.build()
        watch = self.registry / "x-watches" / "Watched_User.toml"
        watch.write_text(watch.read_text().replace("registry-order: 2", "registry-order: 1", 1))
        with self.assertRaisesRegex(registry_store.RegistryError, "duplicate registry-order"):
            registry_store.load_shards(self.registry)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlinks unavailable")
    def test_fragment_symlink_cannot_escape_its_table_directory(self) -> None:
        self.build()
        fragment = self.registry / "sources" / "zulu-source.toml"
        outside = self.root / "outside.toml"
        fragment.rename(outside)
        fragment.symlink_to(outside)
        with self.assertRaisesRegex(registry_store.RegistryError, "symlink|escapes"):
            registry_store.load_shards(self.registry)

    def test_manifest_detects_fragment_tampering_even_when_toml_still_parses(self) -> None:
        self.build()
        fragment = self.registry / "sources" / "zulu-source.toml"
        fragment.write_text(fragment.read_text().replace(
            'title = "Zulu"', 'title = "Zulu changed"'
        ))
        with self.assertRaisesRegex(migrate_registry.MigrationError, "semantic equivalent"):
            migrate_registry.verify_tree(self.legacy, self.registry)

    def test_manifest_records_every_table_count_and_fragment_hash(self) -> None:
        manifest = self.build()
        on_disk = json.loads((self.registry / "manifest.json").read_text())
        self.assertEqual({"source": 2, "x_post": 1, "nostr_post": 1, "x_watch": 1},
                         on_disk["counts"])
        self.assertEqual(manifest, on_disk)
        for entry in on_disk["fragments"]:
            payload = (self.registry / entry["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), entry["sha256"])

    def test_generated_catalogue_is_readable_but_not_group_writable(self) -> None:
        self.build()
        fragment = self.registry / "sources" / "zulu-source.toml"
        self.assertEqual(0o755, stat.S_IMODE(self.registry.stat().st_mode))
        self.assertEqual(0o755, stat.S_IMODE(fragment.parent.stat().st_mode))
        self.assertEqual(0o644, stat.S_IMODE(fragment.stat().st_mode))


class LoaderObservationTests(RegistryTestCase):
    def test_current_load_reads_and_parses_each_fragment_exactly_once(self) -> None:
        """The bytes authenticated by the manifest are the bytes returned."""
        self.build()
        reads: Counter[Path] = Counter()
        parses = 0
        real_read = registry_store._read_bytes
        real_parse = registry_store.tomllib.loads

        def counted_read(path: Path, *, subject: str) -> bytes:
            reads[Path(path)] += 1
            return real_read(path, subject=subject)

        def counted_parse(text: str) -> dict:
            nonlocal parses
            parses += 1
            return real_parse(text)

        with (
            mock.patch.object(registry_store, "_read_bytes", side_effect=counted_read),
            mock.patch.object(registry_store.tomllib, "loads", side_effect=counted_parse),
            mock.patch.object(
                registry_store,
                "load_shards",
                side_effect=AssertionError("load() reopened the verified tree"),
            ),
        ):
            loaded = registry_store.load(self.root)

        manifest = json.loads((self.registry / "manifest.json").read_text())
        held = [self.registry / entry["path"] for entry in manifest["fragments"]]
        self.assertTrue(registry_store.semantic_equal(loaded,
                                                       registry_store.load_legacy(self.legacy)))
        self.assertEqual({path: 1 for path in held}, {path: reads[path] for path in held})
        self.assertEqual(1, reads[self.registry / "meta.toml"])
        # One initial legacy observation and one post-verification concurrency check.
        self.assertEqual(2, reads[self.legacy])
        # Five fragments, metadata, and the already-read monolithic registry.
        self.assertEqual(len(held) + 2, parses)

    def test_same_size_concurrent_legacy_write_wins_after_shard_verification(self) -> None:
        self.build()
        before = self.legacy.read_bytes()
        after = before.replace(b'title = "Test"', b'title = "Move"', 1)
        self.assertEqual(len(before), len(after))
        real_read = registry_store._read_bytes
        legacy_reads = 0

        def changing_read(path: Path, *, subject: str) -> bytes:
            nonlocal legacy_reads
            if Path(path) == self.legacy:
                legacy_reads += 1
                return before if legacy_reads == 1 else after
            return real_read(path, subject=subject)

        with mock.patch.object(registry_store, "_read_bytes", side_effect=changing_read):
            loaded = registry_store.load(self.root)
        self.assertEqual("Move", loaded["meta"]["title"])
        self.assertEqual(2, legacy_reads)

    def test_concurrent_legacy_write_wins_when_projection_check_falls_back(self) -> None:
        """A failed manifest check must not skip the final legacy observation."""
        self.registry.mkdir()
        before = self.legacy.read_bytes()
        after = before.replace(b'title = "Test"', b'title = "Move"', 1)
        real_read = registry_store._read_bytes
        legacy_reads = 0

        def changing_read(path: Path, *, subject: str) -> bytes:
            nonlocal legacy_reads
            if Path(path) == self.legacy:
                legacy_reads += 1
                return before if legacy_reads == 1 else after
            return real_read(path, subject=subject)

        with mock.patch.object(registry_store, "_read_bytes", side_effect=changing_read):
            loaded = registry_store.load(self.root)
        self.assertEqual("Move", loaded["meta"]["title"])
        self.assertEqual(2, legacy_reads)

    def test_supplied_hash_cannot_mask_a_same_size_concurrent_legacy_write(self) -> None:
        self.build()
        before = self.legacy.read_bytes()
        old_sha = hashlib.sha256(before).hexdigest()
        after = before.replace(b'title = "Test"', b'title = "Move"', 1)
        self.assertEqual(len(before), len(after))
        self.legacy.write_bytes(after)

        status = registry_store.registry_status(self.root, legacy_sha256=old_sha)
        self.assertFalse(status.current)
        self.assertIn("changed during currentness check", status.reason)


class CommandTests(RegistryTestCase):
    def test_default_dry_run_writes_no_registry_tree(self) -> None:
        self.assertEqual(0, migrate_registry.main(["--root", str(self.root)]))
        self.assertFalse(self.registry.exists())

    def test_write_then_check_and_idempotent_write(self) -> None:
        self.assertEqual(0, migrate_registry.main(["--root", str(self.root), "--write"]))
        self.assertEqual(0, migrate_registry.main(["--root", str(self.root), "--check"]))
        before = (self.registry / "manifest.json").read_bytes()
        self.assertEqual(0, migrate_registry.main(["--root", str(self.root), "--write"]))
        self.assertEqual(before, (self.registry / "manifest.json").read_bytes())

    def test_refresh_replaces_stale_shards_only_after_verification(self) -> None:
        self.assertEqual(0, migrate_registry.main(["--root", str(self.root), "--write"]))
        self.legacy.write_text(SAMPLE.replace('title = "Test"', 'title = "Refreshed"'))
        self.assertFalse(registry_store.shards_current(self.root))
        # While stale, readers see the current legacy value rather than shards.
        self.assertEqual("Refreshed", registry_store.load(self.root)["meta"]["title"])
        self.assertEqual(0, migrate_registry.main([
            "--root", str(self.root), "--refresh"
        ]))
        self.assertTrue(registry_store.shards_current(self.root))
        self.assertEqual("Refreshed", registry_store.load_shards(self.registry)
                         ["meta"]["title"])

    def test_refresh_can_repair_a_partial_tree(self) -> None:
        self.build()
        (self.registry / "x-posts" / "post_1.toml").unlink()
        self.assertFalse(registry_store.shards_current(self.root))
        manifest = migrate_registry.refresh_tree(self.legacy, self.registry)
        self.assertEqual(5, len(manifest["fragments"]))
        self.assertTrue(registry_store.shards_current(self.root))

    def test_refresh_supports_explicit_nonstandard_paths(self) -> None:
        target = self.root / "catalogue"
        manifest = migrate_registry.refresh_tree(self.legacy, target)
        status = registry_store.registry_status(
            registry_dir=target, legacy_path=self.legacy
        )
        self.assertTrue(status.current, status.reason)
        self.assertEqual(5, len(manifest["fragments"]))

    def test_write_refuses_to_replace_an_unrelated_directory(self) -> None:
        self.registry.mkdir()
        (self.registry / "keep.txt").write_text("owned by somebody else")
        with self.assertRaisesRegex(migrate_registry.MigrationError, "refusing to replace"):
            migrate_registry.write_tree(self.legacy, self.registry)


if __name__ == "__main__":
    unittest.main()
