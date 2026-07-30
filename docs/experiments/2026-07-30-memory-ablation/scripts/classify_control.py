#!/usr/bin/env python3
"""Control arms for the 19/20 classification result.

Arm A  keyword   : templates' classifier_hints only, no LLM at all (the floor).
Arm B  bare-model: same model, same input bytes, but none of Memory Core's scaffolding
                   (no template descriptions, no hints, no Memory Core system prompt).
Arm C  memcore   : already measured at 19/20 via POST /v1/challenges.

The input string is built by the same code that fed Memory Core, so the only thing that
varies between B and C is the scaffolding around it.
"""

import json
import re
import sys
import time
from pathlib import Path

import httpx
import yaml

REPO = Path("/mnt/c/Users/Ronny/Desktop/AIS3-Project/ais3-ctf6")
SET = REPO / "test-challenge-set"
BASE_URL = "https://llm-api.zoolab.org/v1"
MODEL = "ais3/nemotron-3-ultra-550b"
CATEGORIES = ["crypto", "pwn", "rev", "web"]

sys.path.insert(0, "/tmp")
from boards import listing  # noqa: E402  the exact builder Memory Core was fed


def challenge_input(entry: dict) -> str:
    root = SET / entry["path"]
    rd = root / "README.md"
    desc = rd.read_text(encoding="utf-8", errors="replace").strip() if rd.exists() else ""
    return f"{desc[:4000]}\n\n## Files provided\n```\n{listing(root)}\n```"


def arm_keyword(text: str, hints: dict[str, list[str]]) -> str:
    """The fake summarizer's algorithm: most classifier_hints matched wins."""
    hay = text.lower()
    best, best_n = "generic", 0
    for tid, hs in hints.items():
        n = sum(1 for h in hs if h.lower() in hay)
        if n > best_n:
            best, best_n = tid, n
    return best


def arm_bare_model(client: httpx.Client, text: str, key: str) -> tuple[str, float]:
    """Same model, no Memory Core scaffolding. Minimal viable prompt."""
    body = {
        "model": MODEL,
        "messages": [
            {"role": "user", "content":
                f"Which category does this CTF challenge belong to? "
                f"Answer with exactly one word from: {', '.join(CATEGORIES)}.\n\n{text[:8000]}"},
        ],
        "max_tokens": 2048,
        "temperature": 0,
    }
    t0 = time.monotonic()
    r = client.post("/chat/completions", json=body,
                    headers={"Authorization": f"Bearer {key}"})
    dt = time.monotonic() - t0
    r.raise_for_status()
    content = (r.json()["choices"][0]["message"].get("content") or "").strip().lower()
    for c in CATEGORIES:
        if re.search(rf"\b{c}\b", content):
            return c, dt
    return f"?({content[:30]})", dt


def main() -> int:
    manifest = json.loads((SET / "MANIFEST.json").read_text(encoding="utf-8"))
    hints = {}
    for p in sorted((REPO / "templates").glob("*.yaml")):
        t = yaml.safe_load(p.read_text(encoding="utf-8"))
        hints[t["id"]] = t.get("classifier_hints") or []

    key = json.loads(
        Path("/mnt/c/Users/Ronny/.config/opencode/opencode.json").read_text(encoding="utf-8")
    )["provider"]["ais3"]["options"]["apiKey"]

    run_llm = "--keyword-only" not in sys.argv
    rows = []
    with httpx.Client(base_url=BASE_URL, timeout=300.0) as client:
        for i, e in enumerate(manifest, 1):
            text = challenge_input(e)
            kw = arm_keyword(text, hints)
            bare, dt = (arm_bare_model(client, text, key) if run_llm else ("-", 0.0))
            row = {"path": e["path"], "truth": e["category"], "difficulty": e["difficulty"],
                   "keyword": kw, "bare_model": bare, "seconds": round(dt, 1)}
            rows.append(row)
            print(f"[{i:2}/20] {e['path']:<44} truth={e['category']:<7}"
                  f"kw={kw:<9}bare={bare:<9}{row['seconds']}s", flush=True)

    kw_hits = sum(1 for r in rows if r["keyword"] == r["truth"])
    print(f"\nArm A keyword    : {kw_hits}/{len(rows)}")
    if run_llm:
        bm_hits = sum(1 for r in rows if r["bare_model"] == r["truth"])
        print(f"Arm B bare-model : {bm_hits}/{len(rows)}")
    print("Arm C memcore    : 19/20  (previously measured)")

    out = Path(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
        else Path("/tmp/classify-control.json")
    out.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
