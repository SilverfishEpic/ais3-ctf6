#!/usr/bin/env python3
"""Measure what memory injection COSTS the main brain, per ablation level.

This is the half of the hypothesis that needs no agent and no API key: given a real
whiteboard, how much text does the bridge push into the agent's context at turn start
under each of the four policies in policy.py (規劃書 §19.2)?

The other half -- what memory SAVES -- needs real agent runs and cannot be estimated
from disk, because it depends on what the agent would otherwise have re-derived.
"""

import glob
import json
import os
import sys
from pathlib import Path

import yaml

from agent_bridge import policy as P
from agent_bridge.inject import render_board

REPO = Path("/mnt/c/Users/Ronny/Desktop/AIS3-Project/ais3-ctf6")

# A stand-in for what Memory Core's /guess returns, so FULL is not undercounted.
FAKE_CANDIDATES = [
    {"id": "CRYPTO-STREAM-KEY-REUSE", "title": "Stream Cipher Keystream Reuse", "tried": False},
    {"id": "CRYPTO-LATTICE-LLL", "title": "Lattice Reduction via LLL", "tried": False},
    {"id": "CRYPTO-RSA-FACTOR", "title": "RSA Modulus Factorisation", "tried": False},
]


def boards() -> dict[str, dict]:
    found = {}
    for d in sorted(glob.glob(str(REPO / "runs" / "q-*"))) + sorted(
        glob.glob(str(REPO / "runs" / "q2-*"))
    ):
        stem = os.path.basename(d).split("-", 1)[1]
        p = os.path.join(d, "state.yaml")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as handle:
                found[stem] = yaml.safe_load(handle)
    return found


def main() -> int:
    data = boards()
    levels = P.ABLATION_LEVELS
    print(f"{'board':<38}" + "".join(f"{lv.name:>22}" for lv in levels))
    totals = {lv.name: 0 for lv in levels}
    rows = []
    for stem, snap in sorted(data.items()):
        cells = []
        for lv in levels:
            text = render_board(snap, lv, guess_candidates=FAKE_CANDIDATES)
            n = len(text)
            totals[lv.name] += n
            cells.append(n)
        rows.append((stem, cells))
        print(f"{stem:<38}" + "".join(f"{c:>22,}" for c in cells))

    n = len(data)
    print(f"\n{'TOTAL chars':<38}" + "".join(f"{totals[lv.name]:>22,}" for lv in levels))
    print(f"{'MEAN chars / board':<38}" + "".join(f"{totals[lv.name] // n:>22,}" for lv in levels))
    print(
        f"{'MEAN ~tokens / turn':<38}"
        + "".join(f"{totals[lv.name] // n // 4:>22,}" for lv in levels)
    )

    # What the whole 20-challenge suite would inject on turn 1 alone.
    print()
    for lv in levels:
        print(f"{lv.name:<22} 20 boards x turn 1 = {totals[lv.name]:>9,} chars "
              f"~ {totals[lv.name] // 4:>7,} tokens")

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("injection.json")
    out.write_text(json.dumps({"totals": totals, "rows": rows}, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
