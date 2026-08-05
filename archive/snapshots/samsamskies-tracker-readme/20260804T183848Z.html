# Coldcard Hack Tracker

Live single-page dashboard for Bitcoin held after Coldcard seed-entropy sweeps since July 2026.

Monitors consolidation vaults across named clusters (Galaxy Waves 1–3 plus later community waves). **Core vaults** (Waves 1–2 and small community holdings) are polled live in the browser via the public [mempool.space](https://mempool.space) API. **Wave 3** balances come from `public/snapshot.json`, refreshed by a GitHub Actions cron about every 15 minutes — so visitors do not fan out dozens of explorer requests.

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
| Core balances & txs (live) | mempool `/api/address/{addr}` |
| Wave 3 balances | `/snapshot.json` (cron) |
| BTC/USD | `/api/v1/prices` (+ snapshot) |
| Incident facts | Galaxy Research + community cluster reports (static) |

No API key is needed for the dashboard. Core vaults refresh about every 60 seconds; the Wave 3 snapshot is re-read about every 5 minutes.

Optional local research: put `BLOCKCHAIR_API_KEY` in a gitignored `.env` for faster tip wave scouts (`scripts/scan-new-waves.py --blockchair`) and batch balance checks (`scripts/blockchair-balances.py`). Estimate request points and confirm before spending quota; **do not** use that key in the snapshot cron.

### Balance snapshot

```bash
npm run snapshot
```

Writes `public/snapshot.json` (all watched holdings + movements for vaults that look spent). On `main`, [`.github/workflows/snapshot.yml`](.github/workflows/snapshot.yml) runs this on a schedule and commits the file so static hosts redeploy with fresh Wave 3 numbers. Esplora-only by design.

### Tip wave scout

```bash
python3 scripts/scan-new-waves.py --no-escalate --blockchair   # needs .env key
python3 scripts/scan-new-waves.py --no-escalate                # Esplora fallback
```

Scouts the last 3 tip blocks for Coldcard-like 1-vout fee clusters. See `.cursor/skills/new-wave-scan/SKILL.md`.

### Movement alerts

Use the **Alerts** toggle in the header for browser notifications when a new
outbound spend appears (holdings or followed hops). Preference is stored in
`localStorage`; the tab must stay open for polling to continue.

### Explorer mirrors

Some networks, VPNs, and DNS filters block `mempool.space`, which makes every
request fail in the browser. The app probes hosts in order and reuses the first
one that answers:

1. `mempool.space`
2. `mempool.emzy.de`
3. `mempool.bitaroo.net`

The footer shows which host served the current data. Edit `MEMPOOL_HOSTS` in
[src/data/incident.ts](src/data/incident.ts) to add your own instance.

### Hop following

When a holding address spends reported consolidation, the tracker follows the
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
