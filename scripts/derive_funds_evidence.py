#!/usr/bin/env python3
"""Derive a privacy-safe funds-accounting artefact from a pinned chain dataset.

The source dataset contains victim addresses and transaction identifiers. This
script holds those records only in memory and emits aggregate figures plus the
four final holding addresses already published on the site.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from typing import Any


SOURCE_COMMIT = "47d8f5543812c8244fa95ed90db957ddcc05200c"
SOURCE_URL = (
    "https://raw.githubusercontent.com/Kelbie/coldcard-rng-postmortem/"
    f"{SOURCE_COMMIT}/src/data/chain.json"
)
SOURCE_SHA256 = "82d5f9428085c7dc069f6201abd679aae8180d2d45151d6ab0e951ccbd627bbc"
SELECTED_WAVES = (1, 2, 3)
API_BASES = (
    "https://mempool.space/api",
    "https://blockstream.info/api",
)

# These are the four destination holdings already disclosed on /record/funds/.
HOLDINGS = (
    {
        "label": "wave_3_vault",
        "dataset_wave": 3,
        "dataset_role": "consolidation_destination",
        "address": "bc1qq85v2c926eg6pgxhwp6q7lf6cnsz80qs3fcu9r",
    },
    {
        "label": "wave_2_vault",
        "dataset_wave": 2,
        "dataset_role": "consolidation_destination",
        "address": "bc1qx76cae2706qd5q576feh7xq8rfcsjpf2htfhe3",
    },
    {
        "label": "wave_1_vault",
        "dataset_wave": 1,
        "dataset_role": "consolidation_destination",
        "address": "bc1q8jy96fe5lf8vfugydnte3cguk92gpev7kwtp3q",
    },
    {
        "label": "wave_3_retained_collector",
        "dataset_wave": 3,
        "dataset_role": "sweep_destination",
        "address": "bc1qnk4zh9qcnap2mycp56qjrgza3cc8ylrh8fecp0",
    },
)


class EvidenceError(RuntimeError):
    """Raised when an input or invariant does not match the expected record."""


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "cc-vuln.org evidence derivation/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def iso_utc(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def movement_summary(movements: list[dict[str, Any]]) -> dict[str, Any]:
    gross_inputs = []
    for item in movements:
        received_plus_fee = item["receivedSats"] + item["feeSats"]
        if item["kind"] == "sweep":
            victim_value = sum(victim["valueSats"] for victim in item["victims"])
            require(victim_value == received_plus_fee, "sweep input value mismatch")
            require(item["outputCount"] == 1, "selected sweep has multiple outputs")
            gross_inputs.append(victim_value)
        else:
            gross_inputs.append(received_plus_fee)
    return {
        "transaction_count": len(movements),
        "input_count": sum(item["inputCount"] for item in movements),
        "output_count": sum(item["outputCount"] for item in movements),
        "net_received_sats": sum(item["receivedSats"] for item in movements),
        "fee_sats": sum(item["feeSats"] for item in movements),
        "gross_input_sats": sum(gross_inputs),
        "first_block": min(item["blockHeight"] for item in movements),
        "last_block": max(item["blockHeight"] for item in movements),
        "first_block_time": iso_utc(min(item["blockTime"] for item in movements)),
        "last_block_time": iso_utc(max(item["blockTime"] for item in movements)),
    }


def main() -> int:
    source_body = fetch(SOURCE_URL)
    source_hash = hashlib.sha256(source_body).hexdigest()
    require(source_hash == SOURCE_SHA256, "pinned source hash changed")
    dataset = json.loads(source_body)

    selected = [
        movement
        for movement in dataset["movements"]
        if movement["wave"] in SELECTED_WAVES
    ]
    sweeps = [movement for movement in selected if movement["kind"] == "sweep"]
    consolidations = [
        movement for movement in selected if movement["kind"] == "consolidation"
    ]
    require(len(sweeps) == 1195, "selected sweep count is not 1,195")
    require(len(consolidations) == 3, "selected consolidation count is not three")

    source_types: dict[str, str] = {}
    source_values_by_wave: dict[int, dict[str, int]] = {
        wave: {} for wave in SELECTED_WAVES
    }
    for movement in sweeps:
        for victim in movement["victims"]:
            previous = source_types.setdefault(victim["address"], victim["scriptType"])
            require(previous == victim["scriptType"], "source script type changed")
            wave_values = source_values_by_wave[movement["wave"]]
            wave_values[victim["address"]] = (
                wave_values.get(victim["address"], 0) + victim["valueSats"]
            )

    sweep_by_wave = {
        str(wave): movement_summary(
            [movement for movement in sweeps if movement["wave"] == wave]
        )
        for wave in SELECTED_WAVES
    }
    sweep_totals = movement_summary(sweeps)
    consolidation_totals = movement_summary(consolidations)

    consolidation_by_wave = {
        movement["wave"]: movement for movement in consolidations
    }
    for holding in HOLDINGS:
        wave = holding["dataset_wave"]
        if holding["dataset_role"] == "consolidation_destination":
            consolidation = consolidation_by_wave[wave]
            require(
                consolidation["outputCount"] == 1,
                f"dataset consolidation for wave {wave} has multiple outputs",
            )
            destinations = set(consolidation["destinations"])
        else:
            destinations = {
                destination
                for movement in sweeps
                if movement["wave"] == wave
                for destination in movement["destinations"]
            }
        require(
            destinations == {holding["address"]},
            f"dataset destinations do not match {holding['label']}",
        )

    theft_linked_by_label = {
        "wave_1_vault": consolidation_by_wave[1]["receivedSats"],
        "wave_2_vault": consolidation_by_wave[2]["receivedSats"],
        "wave_3_vault": consolidation_by_wave[3]["receivedSats"],
        "wave_3_retained_collector": (
            sweep_by_wave["3"]["net_received_sats"]
            - consolidation_by_wave[3]["receivedSats"]
            - consolidation_by_wave[3]["feeSats"]
        ),
    }
    theft_linked_total = sum(theft_linked_by_label.values())
    total_fee_sats = sweep_totals["fee_sats"] + consolidation_totals["fee_sats"]
    require(
        sweep_totals["gross_input_sats"] - total_fee_sats == theft_linked_total,
        "gross minus fees does not equal the theft-linked holdings",
    )

    wave_3_values = sorted(source_values_by_wave[3].values())
    require(len(wave_3_values) == 500, "wave 3 source-address count is not 500")
    wave_3_median_twice = wave_3_values[249] + wave_3_values[250]
    wave_3_distribution = {
        "source_address_count": len(wave_3_values),
        "gross_sats": sum(wave_3_values),
        "minimum_sats": wave_3_values[0],
        "median_sats": f"{wave_3_median_twice // 2}.5"
        if wave_3_median_twice % 2
        else str(wave_3_median_twice // 2),
        "maximum_sats": wave_3_values[-1],
        "greater_than_1_btc": sum(value > 100_000_000 for value in wave_3_values),
        "exactly_1_btc": sum(value == 100_000_000 for value in wave_3_values),
    }
    require(
        wave_3_distribution["gross_sats"]
        == sweep_by_wave["3"]["gross_input_sats"],
        "wave 3 distribution does not equal its gross input",
    )

    api_results: dict[str, Any] = {}
    for base in API_BASES:
        name = "mempool_space" if "mempool.space" in base else "blockstream"
        responses = []
        for holding in HOLDINGS:
            body = fetch(f"{base}/address/{holding['address']}")
            parsed = json.loads(body)
            responses.append(
                {
                    "address": holding["address"],
                    "body_sha256": hashlib.sha256(body).hexdigest(),
                    "chain_stats": parsed["chain_stats"],
                    "mempool_stats": parsed["mempool_stats"],
                }
            )
        tip_body = fetch(f"{base}/blocks/tip/height")
        api_results[name] = {
            "base_url": base,
            "tip_height": int(tip_body),
            "tip_response_sha256": hashlib.sha256(tip_body).hexdigest(),
            "address_responses": responses,
        }

    mempool_results = {
        item["address"]: item
        for item in api_results["mempool_space"]["address_responses"]
    }
    blockstream_results = {
        item["address"]: item
        for item in api_results["blockstream"]["address_responses"]
    }
    tip_heights = [result["tip_height"] for result in api_results.values()]
    require(
        max(tip_heights) - min(tip_heights) <= 1,
        "explorer tip heights differ by more than one block",
    )

    current_holdings = []
    no_post_consolidation_outflow = True
    for holding in HOLDINGS:
        address = holding["address"]
        mempool_item = mempool_results[address]
        blockstream_item = blockstream_results[address]
        require(
            mempool_item["chain_stats"] == blockstream_item["chain_stats"]
            and mempool_item["mempool_stats"] == blockstream_item["mempool_stats"],
            f"explorer address responses disagree for {address}",
        )
        chain = mempool_item["chain_stats"]
        pending = mempool_item["mempool_stats"]
        chain_balance = chain["funded_txo_sum"] - chain["spent_txo_sum"]
        pending_balance = pending["funded_txo_sum"] - pending["spent_txo_sum"]

        if holding["label"] == "wave_3_retained_collector":
            expected_spent_count = consolidation_by_wave[3]["inputCount"]
            expected_spent_sats = (
                consolidation_by_wave[3]["receivedSats"]
                + consolidation_by_wave[3]["feeSats"]
            )
        else:
            expected_spent_count = 0
            expected_spent_sats = 0
        holding_has_no_later_outflow = (
            chain["spent_txo_count"] == expected_spent_count
            and chain["spent_txo_sum"] == expected_spent_sats
            and pending["spent_txo_count"] == 0
            and pending["spent_txo_sum"] == 0
        )
        no_post_consolidation_outflow &= holding_has_no_later_outflow
        current_holdings.append(
            {
                "label": holding["label"],
                "address": address,
                "theft_linked_sats": theft_linked_by_label[holding["label"]],
                "chain_funded_txo_count": chain["funded_txo_count"],
                "chain_funded_sats": chain["funded_txo_sum"],
                "chain_spent_txo_count": chain["spent_txo_count"],
                "chain_spent_sats": chain["spent_txo_sum"],
                "chain_balance_sats": chain_balance,
                "mempool_balance_sats": pending_balance,
                "later_inbound_sats": (
                    chain_balance + pending_balance
                    - theft_linked_by_label[holding["label"]]
                ),
                "no_post_consolidation_outflow": holding_has_no_later_outflow,
            }
        )

    live_total = sum(
        item["chain_balance_sats"] + item["mempool_balance_sats"]
        for item in current_holdings
    )
    checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    artefact = {
        "schema": 1,
        "purpose": "Privacy-preserving accounting from a pinned chain-derived dataset for the first three attributed waves",
        "checked_at": checked_at,
        "source": {
            "repository": "Kelbie/coldcard-rng-postmortem",
            "commit": SOURCE_COMMIT,
            "path": "src/data/chain.json",
            "url": SOURCE_URL,
            "generated_at": dataset["generatedAt"],
            "sha256": source_hash,
        },
        "selection": {
            "waves": list(SELECTED_WAVES),
            "source_address_count": len(source_types),
            "source_output_types": dict(sorted(Counter(source_types.values()).items())),
            "first_block": sweep_totals["first_block"],
            "last_block": sweep_totals["last_block"],
            "first_block_time": sweep_totals["first_block_time"],
            "last_block_time": sweep_totals["last_block_time"],
        },
        "sweep_accounting": {
            "by_wave": sweep_by_wave,
            "total": sweep_totals,
        },
        "consolidation_accounting": {
            "transaction_count": consolidation_totals["transaction_count"],
            "input_count": consolidation_totals["input_count"],
            "output_count": consolidation_totals["output_count"],
            "fee_sats": consolidation_totals["fee_sats"],
            "theft_linked_holdings_sats": theft_linked_total,
        },
        "privacy_safe_distributions": {
            "wave_3_500_source_addresses": wave_3_distribution,
        },
        "reconciliation": {
            "gross_source_sats": sweep_totals["gross_input_sats"],
            "first_collector_sats": sweep_totals["net_received_sats"],
            "source_fee_sats": sweep_totals["fee_sats"],
            "consolidation_fee_sats": consolidation_totals["fee_sats"],
            "total_fee_sats": total_fee_sats,
            "theft_linked_holdings_sats": theft_linked_total,
            "equation": "gross_source_sats - total_fee_sats = theft_linked_holdings_sats",
        },
        "current_holdings": {
            "api_results": api_results,
            "holdings": current_holdings,
            "theft_linked_total_sats": theft_linked_total,
            "live_total_sats": live_total,
            "later_inbound_total_sats": live_total - theft_linked_total,
            "no_post_consolidation_outflow": no_post_consolidation_outflow,
        },
        "checks": {
            "pinned_source_sha256_matches": source_hash == SOURCE_SHA256,
            "explorers_agree": True,
            "explorer_tip_height_spread_at_most_one": True,
            "holding_addresses_match_dataset_destinations": True,
            "gross_minus_fees_equals_holdings": True,
            "wave_3_distribution_equals_gross": True,
            "no_unconfirmed_balance": all(
                item["mempool_balance_sats"] == 0 for item in current_holdings
            ),
            "no_post_consolidation_outflow": no_post_consolidation_outflow,
        },
        "privacy": (
            "No source address, transaction identifier, script or public key is "
            "retained. The only addresses emitted are the four destination holdings "
            "already published on the funds-accounting page."
        ),
        "scope_limit": (
            "The arithmetic checks internal consistency of the selected records in "
            "the pinned chain-derived dataset and compares current address summaries "
            "from two explorers. It does not independently reconstruct raw "
            "transactions, establish common control, identify the hardware used, "
            "establish the cause of each spend, or reproduce Galaxy's unpublished "
            "address-counting method."
        ),
    }
    json.dump(artefact, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (EvidenceError, OSError, ValueError, KeyError) as error:
        print(f"funds evidence derivation failed: {error}", file=sys.stderr)
        raise SystemExit(1)
