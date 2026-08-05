# Slipstream confirmation-window measurement

Checked 1 Aug 2026. This note preserves the inputs behind the dated estimate on
`/risk/migrating/` (now `/response/migration/`). It is a retrospective pool-share calculation, not a service
guarantee or a forecast for an individual transaction.

## Source observation

- Endpoint: `https://mempool.space/api/v1/mining/pools/1m`
- Retrieved: `2026-08-01T05:56:13Z`
- Window reported by the endpoint: one month
- Blocks in the response: 4,343 (including the endpoint's Unknown category)
- Blocks attributed to MARA Pool: 197
- MARA Pool rank in the response: 6
- Retrieved HTTP body SHA-256: `810e52c0d0c2a19a6877cf8d8bccdd797baeb8d83d8933fd52cc64c492f41e43`
- Held response: [`evidence/mempool-pools-1m-20260801T055613Z.json`](evidence/mempool-pools-1m-20260801T055613Z.json)
- Held file SHA-256: `906d774288ef2e5fee016e90b44c462af835a4932da52c6581975f700b5fb0b7`
  (the held file adds a final newline)

The API response is rolling data and will not retain these exact values. The
response, counts and hash are recorded here so the published arithmetic does
not silently change when the endpoint moves to a later window.

## Derivation

Observed share:

```text
197 / 4,343 = 0.04536035 = 4.536%
```

If network blocks arrive every ten minutes on average and MARA continues to
find the same share independently, the mean interval between MARA blocks is:

```text
10 minutes / 0.04536035 = 220.46 minutes = 3.674 hours
```

## Limits

The calculation assumes the observed one-month share persists, block arrivals
are independent, the submission is accepted, and any MARA block is eligible to
include it. Actual pool share and block intervals vary. Transaction validity,
fees, conflicts, pool policy, service retention and relay behaviour remain
separate questions. A mean interval is not a deadline, and neither the observed
share nor MARA's public Slipstream portal promises inclusion in the next MARA
block.

The endpoint was verified against the current mempool open-source frontend,
which requests `/api/v1/mining/pools/{interval}` for its pool-distribution view.
