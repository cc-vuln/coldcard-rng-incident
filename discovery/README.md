# Structured discovery record

Discovery history is stored as immutable, hash-chained JSON transaction
batches under `transactions/YYYY-MM/`. A transaction records observations,
retries, verdicts and explicit supersessions; it is the canonical history.
Its sequence and hash-chain position record insertion order; its `at` value
and month directory follow the latest event in that batch. Git history is the
wall-clock record of when a transaction entered this repository.

Everything under `candidates/` and `views/`, `state.json`, and the repository's
root `DISCOVERY.md` is generated from that chain. Candidate JSON is the easiest
place to inspect one item's complete observation and decision history. The
paged Markdown views are navigation aids, not another ledger.

`migration-v1/legacy/` retains every pre-cutover queue and rotated-history file
byte for byte. `migration-v1/occurrence-semantics.json` maps each exact legacy
bullet and transition to the immutable event or events that preserve it.
`migration-v1/manifest.json` records both files' hashes, every deliberate
repair, the baseline semantic root and a bundle root bound into each migration
transaction. Validation independently reparses the held bullets and checks
those event bindings. This makes later alteration or parser drift detectable
without pretending that the old Markdown was itself append-only.

Writers serialize on `.work/locks/discovery.lock`. Do not edit transactions,
candidate projections, views or the root index directly. Use the discovery
writer APIs, then validate or regenerate with:

```bash
just discovery-check
.venv/bin/python scripts/discovery_store.py render
```

An interrupted transaction remains authoritative and a later render repairs
its projections. The one-time installer additionally journals directory
activation under `.work/` so a restart either restores the legacy inputs or
finishes the validated new tree.

The operator workflow and placement guide is `../docs/DISCOVERY.md`.

The published transaction and candidate JSON Schemas describe each object's
structural envelope. They do not express hash recomputation, transition rules,
cross-file inventory or projection equality. `discovery_store.py validate` is
the normative validator for those contracts and for the migration manifest,
occurrence table and `state.json` formats.
