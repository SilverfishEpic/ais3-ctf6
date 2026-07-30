#!/usr/bin/env python3
"""Dump every record on every q-* board, with machine-checkable quality flags.

The flags are deliberately conservative: each one is a property of the record itself, not a
judgement about whether the claim is true. Reading the text is still the point -- this only
narrows where to look.
"""

import glob
import json
import os
import re
import sys
from pathlib import Path

import yaml

REPO = Path("/mnt/c/Users/Ronny/Desktop/AIS3-Project/ais3-ctf6")
SET = REPO / "test-challenge-set"


def norm(t: str) -> str:
    return re.sub(r"\s+", " ", t.strip().lower())


def flags_for(item: dict, section: str, layer: str, norms: dict[str, list[str]]) -> list[str]:
    f = []
    n = norm(item["text"])
    if len(norms[n]) > 1:
        f.append(f"DUP({','.join(x for x in norms[n] if x != item['id'])})")
    if not item.get("tags"):
        f.append("no-tags")
    if not item.get("evidence"):
        f.append("no-evidence")
    if section == "general":
        f.append("catch-all-section")
    if len(item["text"]) < 40:
        f.append("very-short")
    return f


def main() -> int:
    entries = json.loads((SET / "MANIFEST.json").read_text(encoding="utf-8"))
    truth = {}
    for e in entries:
        s = re.sub(r"[^a-z0-9-]+", "-", e["path"].lower()).strip("-")
        truth["q-" + s] = e
        truth["q2-" + s] = e
    out_lines: list[str] = []
    totals = {"items": 0, "dup": 0, "no_tags": 0, "catch_all": 0, "boards": 0}

    # A q2-* board is a re-run of a q-* board with a fixed input harness; it supersedes it.
    dirs = {}
    for d in sorted(glob.glob(str(REPO / "runs" / "q-*"))) + sorted(
        glob.glob(str(REPO / "runs" / "q2-*"))
    ):
        stem = os.path.basename(d).split("-", 1)[1]
        dirs[stem] = d

    for _, d in sorted(dirs.items()):
        state_path = os.path.join(d, "state.yaml")
        if not os.path.exists(state_path):
            continue
        with open(state_path, encoding="utf-8") as handle:
            s = yaml.safe_load(handle)
        cid = s["challenge"]["id"]
        entry = truth.get(cid, {})
        cat_ok = "OK" if s["challenge"]["category"] == entry.get("category") else "MISS"
        totals["boards"] += 1

        norms: dict[str, list[str]] = {}
        for layer in ("fact", "assumption"):
            for items in s[layer].values():
                for it in items:
                    norms.setdefault(norm(it["text"]), []).append(it["id"])

        n_items = sum(len(v) for layer in ("fact", "assumption") for v in s[layer].values())
        out_lines.append("=" * 100)
        out_lines.append(
            f"{cid}   [{entry.get('category','?')}/{entry.get('difficulty','?')}]  "
            f"classified={s['challenge']['category']} ({cat_ok})  "
            f"rev={s['revision']}  items={n_items}"
        )
        out_lines.append(f"SUMMARY: {s['challenge'].get('summary')}")
        out_lines.append("=" * 100)

        for layer in ("fact", "assumption"):
            for section, items in s[layer].items():
                if not items:
                    continue
                out_lines.append(f"\n  [{layer}.{section}]")
                for it in items:
                    fl = flags_for(it, section, layer, norms)
                    totals["items"] += 1
                    if any(x.startswith("DUP") for x in fl):
                        totals["dup"] += 1
                    if "no-tags" in fl:
                        totals["no_tags"] += 1
                    if "catch-all-section" in fl:
                        totals["catch_all"] += 1
                    mark = ("  <<< " + " ".join(fl)) if fl else ""
                    text = re.sub(r"\s+", " ", it["text"])
                    out_lines.append(f"    {it['id']}  {text}")
                    if it.get("tags"):
                        out_lines.append(f"          tags={it['tags']}")
                    if mark:
                        out_lines.append(f"        {mark}")
        out_lines.append("")

    header = [
        f"{totals['boards']} boards, {totals['items']} records",
        f"  duplicate-text records : {totals['dup']}",
        f"  records with no tags   : {totals['no_tags']}",
        f"  records in 'general'   : {totals['catch_all']}",
        "",
    ]
    text = "\n".join(header + out_lines)
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("board-report.txt")
    out.write_text(text, encoding="utf-8")
    print("\n".join(header))
    print(f"wrote {out} ({len(text)} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
