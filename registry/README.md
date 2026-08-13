# Source registry

The registry's discoverable projection is organised as one TOML record per
file:

```text
meta.toml
sources/<id>.toml
x-posts/<id>.toml
nostr-posts/<id>.toml
x-watches/<handle>.toml
manifest.json
```

Each record file contains exactly one array-of-tables block and begins with a
global `# registry-order: N` marker. The marker preserves the legacy table
order independently of directory or filename ordering. Record filenames are
stable keys restricted to letters, digits, `_` and `-`.

`scripts/registry_store.py` is the only layout-aware reader. During the
transition it selects this tree only when `manifest.json` proves every shard
still matches the current `sources.toml`; otherwise it reads the legacy file.
That keeps small fixtures simple and makes an interrupted or stale refresh
fail safe.

Generate or verify the tree with:

```bash
python3 scripts/migrate_registry.py --dry-run
python3 scripts/migrate_registry.py --check
python3 scripts/migrate_registry.py --write
python3 scripts/migrate_registry.py --refresh
```

The converter stages and verifies the complete tree before replacing it. Its
manifest pins the legacy file hash, parsed semantic hash, record counts, global
order, and every TOML fragment hash. A successful check also reconstructs the
legacy input byte for byte, proving that comments and presentation text were
not discarded by the split.

During the transition, `sources.toml` remains the write target. The loader uses
the shards only while their manifest and every held fragment match that file.
A new legacy append therefore falls back safely to `sources.toml`; run
`--refresh` after the append to build and verify a sibling tree and swap it in
atomically. `--write` is for initial installation and deliberately refuses to
overwrite divergent existing shards.

Captured snapshots and diffs are unaffected by this layout. Their preservation
rule is independent of how the source catalogue is organised.
