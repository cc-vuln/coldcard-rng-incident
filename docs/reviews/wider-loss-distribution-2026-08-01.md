# Wider attributed-set loss distribution

Checked 1 Aug 2026. This note preserves a privacy-safe distribution derived
from the first three waves in Kelbie's independent chain dataset at commit
`47d8f5543812c8244fa95ed90db957ddcc05200c`.

The raw `src/data/chain.json` file is not mirrored here because it contains
source addresses and transaction identifiers. The held aggregate contains no
address, transaction identifier, script or public key. It records the pinned
source URL, source-file hash, selection rule, quantile method, summary counts
and coarse histogram bins.

## Scope

The selected waves cover blocks 960,183 to 960,191 and contain 1,195 original
source addresses with 1,082.65318922 BTC gross. That reproduces this site's
local original-source count and Galaxy's rounded 1,082.65 BTC figure. Galaxy
published 1,196 addresses but did not publish its counting method. The aggregate
therefore does not claim to be Galaxy's unpublished address set and does not
resolve the one-address counting difference.

The same-operator relationship between the selected waves remains an
attribution. Chain data verifies transactions and values, not common control or
the COLDCARD cause by itself.

## Results

- Median source-address value: 0.27001931 BTC
- Interquartile range: 0.15 BTC to 0.624631895 BTC
- Minimum: 0.000045 BTC
- Maximum: 51.07343852 BTC
- At least 1 BTC: 231 addresses
- At least 10 BTC: 17 addresses
- Below 0.01 BTC: 68 addresses

The complete privacy-safe result is held at
[`evidence/wider-loss-distribution-47d8f554.json`](evidence/wider-loss-distribution-47d8f554.json).
Quantiles use R-7 linear interpolation over integer-satoshi source-address
values sorted in ascending order.

Address counts are not owner counts. One person can control many addresses, and
the public data do not establish how many distinct people were affected.
