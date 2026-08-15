# Coldcard Hack Tracker

Static dashboard for Bitcoin held after Coldcard seed-entropy sweeps since July 2026.

Monitors consolidation vaults across named clusters (Galaxy Waves 1–3 plus later community waves). All balances and the movement feed come from `public/snapshot.json`, refreshed by a GitHub Actions cron about every 6 hours — visitors do not hit public explorers.

## Develop

```bash
npm install
npm run dev
```

Open the URL Vite prints (usually `http://localhost:5173`).

```bash
npm test
```

Runs the Vitest unit suite (formatters, outbound/hop logic, incident data invariants).

## Build / deploy

```bash
npm run build
npm run preview
```

`dist/` is a static site — deploy to Vercel, Netlify, Cloudflare Pages, or any static host.

## Data sources

| Data | Source |
|------|--------|
| Holdings & movements | `/snapshot.json` (cron) |
| BTC/USD | snapshot `usdPrice` |
| Incident facts | Galaxy Research + community cluster reports (static) |

No API key is needed for the dashboard. The browser re-reads `/snapshot.json` on load, on tab focus, and about every 15 minutes while the tab is visible.

Optional local research: put `BLOCKCHAIR_API_KEY` in a gitignored `.env` for faster tip wave scouts (`scripts/scan-new-waves.py --blockchair`) and batch balance checks (`scripts/blockchair-balances.py`). Estimate request points and confirm before spending quota; **do not** use that key in the snapshot cron.

### Balance snapshot

```bash
npm run snapshot
```

Writes `public/snapshot.json` (all watched holdings + movements for vaults that look spent). On `main`, [`.github/workflows/snapshot.yml`](.github/workflows/snapshot.yml) runs this on a schedule and commits the file so static hosts redeploy with fresh numbers. Esplora-only by design.

### Tip wave scout

```bash
python3 scripts/scan-new-waves.py --no-escalate --blockchair   # needs .env key
python3 scripts/scan-new-waves.py --no-escalate                # Esplora fallback
```

Scouts the last 3 tip blocks for Coldcard-like 1-vout fee clusters. See `.cursor/skills/new-wave-scan/SKILL.md`.

### Movement alerts

Use the **Alerts** toggle in the header for browser notifications when a new
outbound spend appears in the snapshot (holdings or followed hops). This is
not live chain — the cron is about every 6 hours. Preference is stored in
`localStorage`. Notifications only fire while the tab is open (including after
a snapshot re-read).

### Explorer mirrors

The snapshot cron probes Esplora hosts in this order (see `HOSTS` in
`scripts/build-snapshot.mjs`):

1. `mempool.bitaroo.net`
2. `mempool.space`
3. `mempool.emzy.de`

Address and transaction links in the UI default to `mempool.space`. Edit
`MEMPOOL_HOSTS` in [src/data/incident.ts](src/data/incident.ts) to change
link targets.

### Hop following

When a holding address spends reported consolidation, the snapshot builder follows the
largest destinations (up to hop 2, ignoring dust under 0.01 BTC) and lists those
outbound spends in the movement feed. Still-held % stays based on the watched
holding addresses only.

If a vault later receives extra coins and forwards them while still holding its
reported balance, that pass-through is ignored — it is not treated as the
stolen stack moving.

## Disclaimer

Not affiliated with Coinkite or Coldcard. For public blockchain monitoring only.

## License

[MIT](LICENSE)
