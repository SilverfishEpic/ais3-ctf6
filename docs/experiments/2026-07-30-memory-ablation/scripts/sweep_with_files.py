#!/usr/bin/env python3
"""Second sweep: same 20 challenges, but initial_input now carries the artifact listing.

Run 1 fed the README alone and scored 16/20. This checks whether adding the file listing
that a player actually sees lifts the score without regressing the ones already correct.
Goes through the public HTTP contract, same as a real client.
"""

import json
import re
import sys
import time
from pathlib import Path

import httpx

REPO = Path("/mnt/c/Users/Ronny/Desktop/AIS3-Project/ais3-ctf6")
SET = REPO / "test-challenge-set"
API = "http://127.0.0.1:8777"


def slug(path: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", path.lower()).strip("-")


def listing(path: Path) -> str:
    """What a player sees: handed-out files only, never the solution directory."""
    names = [
        str(p.relative_to(path))
        for p in sorted(path.rglob("*"))
        if p.is_file() and "solution" not in p.relative_to(path).parts
    ]
    return "\n".join(names)


def initial_input(entry: dict) -> str:
    d = SET / entry["path"]
    readme = d / "README.md"
    text = readme.read_text(encoding="utf-8", errors="replace").strip() if readme.exists() else ""
    if not text:
        text = f"CTF challenge {entry['name']} from {entry['event']}."
    return f"{text[:4000]}\n\n## Files provided\n```\n{listing(d)}\n```"


def main() -> int:
    manifest = json.loads((SET / "MANIFEST.json").read_text(encoding="utf-8"))
    results = []
    with httpx.Client(base_url=API, timeout=330.0) as client:
        for i, entry in enumerate(manifest, 1):
            cid = "tcs2-" + slug(entry["path"])
            t0 = time.monotonic()
            r = client.post(
                "/v1/challenges",
                json={
                    "id": cid,
                    "title": f"{entry['name']} ({entry['event']})",
                    "initial_input": initial_input(entry),
                },
            )
            elapsed = round(time.monotonic() - t0, 1)
            if r.status_code >= 400:
                row = {
                    "path": entry["path"],
                    "truth": entry["category"],
                    "guess": None,
                    "error": f"HTTP {r.status_code}",
                    "seconds": elapsed,
                }
            else:
                ch = client.get(f"/v1/challenges/{cid}/memory").json()["challenge"]
                row = {
                    "path": entry["path"],
                    "truth": entry["category"],
                    "difficulty": entry["difficulty"],
                    "guess": ch.get("category"),
                    "seconds": elapsed,
                }
            hit = row.get("guess") == row["truth"]
            print(
                f"[{i:2}/20] {'OK  ' if hit else 'MISS'} {row['path']:<45} "
                f"truth={row['truth']:<6} guess={str(row.get('guess')):<8} {elapsed}s",
                flush=True,
            )
            results.append(row)

    hits = sum(1 for r in results if r.get("guess") == r["truth"])
    print(f"\n=== README + files: {hits}/{len(results)} category matches ===")

    prev_path = Path("/tmp/sweep-results.json")
    if prev_path.exists():
        prev = {r["path"]: r.get("guess") for r in json.loads(prev_path.read_text())}
        fixed = [r["path"] for r in results
                 if r.get("guess") == r["truth"] and prev.get(r["path"]) != r["truth"]]
        broke = [r["path"] for r in results
                 if r.get("guess") != r["truth"] and prev.get(r["path"]) == r["truth"]]
        print(f"fixed vs run 1   ({len(fixed)}): {fixed}")
        print(f"regressed vs run 1 ({len(broke)}): {broke}")

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("sweep2-results.json")
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
