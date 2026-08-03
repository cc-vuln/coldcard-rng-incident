# Funds-accounting recheck

Checked 1 Aug 2026. This note records a privacy-safe recheck of the first three
waves in Kelbie's independent chain dataset at commit
`47d8f5543812c8244fa95ed90db957ddcc05200c`.

The raw `src/data/chain.json` file is not mirrored because it contains source
addresses and transaction identifiers. The derivation script holds those
records in memory and emits only aggregate counts and amounts, source metadata,
and the four destination holdings already published on the funds-accounting
page.

## Method

`scripts/derive_funds_evidence.py` downloads the commit-pinned dataset, requires
its SHA-256 to equal
`82d5f9428085c7dc069f6201abd679aae8180d2d45151d6ab0e951ccbd627bbc`,
and selects waves 1, 2 and 3. It sums integer satoshis for each sweep and the
three consolidation transactions. It verifies that the four published holding
addresses match the corresponding sweep or consolidation destinations in the
pinned dataset. It also queries the address-summary endpoints at mempool.space
and Blockstream for those holdings, requires the address summaries to agree and
the reported tips to be no more than one block apart, and checks that observed
spends have not increased past the known consolidations.

Run the derivation through the repository virtual environment:

```bash
source .venv/bin/activate
python scripts/derive_funds_evidence.py
```

## Result

- 1,195 source transactions consumed 1,082.65318922 BTC gross and paid
  0.06638490 BTC in source-transaction fees.
- First collectors received 1,082.58680432 BTC.
- Three consolidations paid 0.01760841 BTC in fees.
- Gross value minus 0.08399331 BTC in total fees equals the four
  theft-linked holdings, 1,082.56919591 BTC.
- The 500-address wave ranges from 0.14999770 BTC to 29.89252501 BTC. Its
  statistical median is 0.409059365 BTC, 110 values exceed 1 BTC and another
  16 equal 1 BTC exactly.
- At 06:48 UTC, mempool.space and Blockstream agreed at block 960,525. The four
  addresses held 1,082.56966903 BTC including 0.00047312 BTC in later inbound
  payments, with no unconfirmed balance and no spend after the known
  consolidations.

The complete held result is
[`evidence/funds-accounting-47d8f554-20260801T064817Z.json`](evidence/funds-accounting-47d8f554-20260801T064817Z.json).
It contains no original source address, transaction identifier, script or
public key.

## Limits

The arithmetic checks the internal consistency of selected records in a pinned
chain-derived dataset and compares current address summaries from two explorers.
It does not independently reconstruct the raw transactions, establish that the
first three waves had common control, show that a COLDCARD generated any
particular source key, or reproduce how Galaxy counted its reported 1,196
addresses. The address-type counts in the artefact are output classifications
reported by the dataset. They do not independently reveal wallet derivation
paths.
