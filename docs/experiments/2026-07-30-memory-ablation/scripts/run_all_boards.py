#!/usr/bin/env python3
"""Run all 20 fixture challenges as independent boards, then ingest each one's own files.

Each challenge gets its own board: create (README + file listing -> classification), then a
single ingest of the handed-out source (-> fact/assumption extraction). One HTTP call per
step with a generous timeout: retrying an ingest duplicates the board, so never retry.
The solution/ directory is excluded everywhere.
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

TEXT_SUFFIXES = {
    ".py", ".js", ".mjs", ".ts", ".php", ".rb", ".go", ".rs", ".c", ".h", ".cc", ".cpp",
    ".java", ".sol", ".sh", ".yaml", ".yml", ".json", ".toml", ".txt", ".md", ".html",
    ".css", ".sage", ".s", ".asm", ".conf", ".cfg", ".ini", ".sql", ".xml", ".env",
    ".cs", ".csproj", ".kt", ".swift", ".pl", ".lua", ".ex", ".erl", ".hs", ".scala",
    ".vue", ".svelte", ".jsx", ".tsx", ".gradle", ".cmake", ".pro", ".m", ".mm",
}
TEXT_NAMES = {"Dockerfile", "Makefile", "xinetd", "docker-compose.yml", "requirements.txt"}

ARCHIVE_SUFFIXES = {".zip"}

PER_FILE_CAP = 4000
TOTAL_CAP = 14000


def slug(path: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", path.lower()).strip("-")


def handed_out(root: Path) -> list[Path]:
    return [
        p for p in sorted(root.rglob("*"))
        if p.is_file() and "solution" not in p.relative_to(root).parts
    ]


def is_text(p: Path) -> bool:
    return p.suffix.lower() in TEXT_SUFFIXES or p.name in TEXT_NAMES


def listing(root: Path) -> str:
    lines = []
    for p in handed_out(root):
        rel = p.relative_to(root)
        kind = "" if is_text(p) else "  [binary]"
        lines.append(f"{rel}  ({p.stat().st_size} bytes){kind}")
    return "\n".join(lines)


def archive_text(p: Path) -> str:
    """Players unzip what they are given, so the harness has to as well."""
    import zipfile

    chunks = []
    try:
        with zipfile.ZipFile(p) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            chunks.append(f"--- {p.name} contains ---\n" + "\n".join(names) + "\n")
            for n in names:
                if Path(n).suffix.lower() not in TEXT_SUFFIXES and Path(n).name not in TEXT_NAMES:
                    continue
                try:
                    body = zf.read(n).decode("utf-8", errors="replace")
                except (OSError, zipfile.BadZipFile):
                    continue
                chunks.append(f"--- {p.name}:{n} ---\n{body[:PER_FILE_CAP]}\n")
    except (OSError, zipfile.BadZipFile) as exc:
        chunks.append(f"--- {p.name} (unreadable archive: {exc}) ---\n")
    return "".join(chunks)


def binary_text(p: Path) -> str:
    """What a human pastes for a binary they cannot read: type plus printable strings."""
    import subprocess

    parts = [f"--- {p.name} (binary, {p.stat().st_size} bytes) ---"]
    try:
        ftype = subprocess.run(["file", "-b", str(p)], capture_output=True, text=True,
                               timeout=20).stdout.strip()
        parts.append(f"file: {ftype}")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        s = subprocess.run(["strings", "-n", "8", str(p)], capture_output=True, text=True,
                           timeout=30).stdout.splitlines()
        parts.append("strings (first 60):\n" + "\n".join(s[:60]))
    except (OSError, subprocess.SubprocessError):
        pass
    return "\n".join(parts) + "\n"


def sources(root: Path) -> str:
    """Concatenate the readable source files, newest-relevant first, under a total cap."""
    out, total = [], 0
    files = [p for p in handed_out(root) if is_text(p) and p.name != "README.md"]
    extras = [p for p in handed_out(root) if not is_text(p)]
    # Prefer what players actually get: dist/ over challenge/, and code over config.
    files.sort(key=lambda p: (0 if "dist" in p.parts else 1, 0 if p.suffix == ".py" else 1, str(p)))
    for p in files:
        if total >= TOTAL_CAP:
            out.append(f"\n[... {len(files) - len(out)} more files omitted for length ...]")
            break
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(body) > PER_FILE_CAP:
            body = body[:PER_FILE_CAP] + "\n[... truncated ...]"
        chunk = f"--- {p.relative_to(root)} ---\n{body}\n"
        out.append(chunk)
        total += len(chunk)

    # Binaries and archives carry the whole challenge when no source is handed out.
    for p in extras:
        if total >= TOTAL_CAP:
            break
        chunk = archive_text(p) if p.suffix.lower() in ARCHIVE_SUFFIXES else binary_text(p)
        chunk = chunk[: TOTAL_CAP - total]
        out.append(chunk)
        total += len(chunk)
    return "".join(out)


def main() -> int:
    manifest = json.loads((SET / "MANIFEST.json").read_text(encoding="utf-8"))
    # Optional: re-run a subset under a fresh prefix, so a harness fix does not have to
    # invalidate the boards that were already built correctly.
    only = sys.argv[2:]
    prefix = "q-" if not only else "q2-"
    if only:
        manifest = [e for e in manifest if any(o in e["path"] for o in only)]
    report = []
    with httpx.Client(base_url=API, timeout=600.0) as client:
        for i, entry in enumerate(manifest, 1):
            root = SET / entry["path"]
            cid = prefix + slug(entry["path"])
            readme = (root / "README.md")
            desc = (
                readme.read_text(encoding="utf-8", errors="replace").strip()
                if readme.exists()
                else ""
            )
            row = {"path": entry["path"], "id": cid, "truth": entry["category"],
                   "difficulty": entry["difficulty"]}

            t0 = time.monotonic()
            r = client.post("/v1/challenges", json={
                "id": cid,
                "title": f"{entry['name']} ({entry['event']})",
                "initial_input": f"{desc[:4000]}\n\n## Files provided\n```\n{listing(root)}\n```",
            })
            if r.status_code >= 400:
                row["error"] = f"create HTTP {r.status_code}: {r.text[:150]}"
                print(f"[{i:2}/20] FAIL create {cid}: {row['error']}", flush=True)
                report.append(row)
                continue
            row["guess"] = r.json().get("challenge", {}).get("category")

            src = sources(root)
            if not src.strip():
                src = "(no readable source files were handed out; challenge ships binaries only)"
            r = client.post(f"/v1/challenges/{cid}/ingest", json={
                "source": {"type": "human", "actor": "ronny"},
                "input": {"type": "note",
                          "content": f"Challenge files handed to players:\n\n{src}"},
            })
            row["seconds"] = round(time.monotonic() - t0, 1)
            if r.status_code >= 400:
                row["error"] = f"ingest HTTP {r.status_code}: {r.text[:150]}"
            else:
                state = client.get(f"/v1/challenges/{cid}/memory").json()
                row["revision"] = state["revision"]
                row["facts"] = sum(len(v) for v in state["fact"].values())
                row["assumptions"] = sum(len(v) for v in state["assumption"].values())

            print(
                f"[{i:2}/20] {cid:<42} cat={str(row.get('guess')):<8} "
                f"rev={row.get('revision')} F={row.get('facts')} A={row.get('assumptions')} "
                f"{row['seconds']}s" + (f"  {row['error']}" if row.get("error") else ""),
                flush=True,
            )
            report.append(row)

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("boards.json")
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    ok = [r for r in report if not r.get("error")]
    print(f"\n=== {len(ok)}/{len(report)} boards built, "
          f"{sum(r.get('facts', 0) for r in ok)} facts, "
          f"{sum(r.get('assumptions', 0) for r in ok)} assumptions ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
