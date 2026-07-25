#!/usr/bin/env python3
"""memctl — a smoke-test client for Memory Core.

**This is not the Human Client.** Person 2 owns `cli/` and the real `ctfmem` UX; this is
the "tiny test-only client" the brief allows, and it exists for one reason: to prove the
API boundary is usable by something that only knows the HTTP contract. It imports nothing
from `memory_core` on purpose — if this works, so can a client written in any language.

Usage:

    python -m memory_core --fake-summarizer &          # or the real AIS3 backend
    scripts/memctl.py create demo-web --title "Demo Web"
    scripts/memctl.py note demo-web "I ran nmap and port 80 is open."
    scripts/memctl.py capture demo-web -- nmap -sV target
    scripts/memctl.py show demo-web
    scripts/memctl.py query demo-web "which ports are confirmed open?"
    scripts/memctl.py guess demo-web --seed 42
    scripts/memctl.py promote demo-web A001 --reason "verified in a browser"
    scripts/memctl.py template demo-web web --reason "it is a web challenge"

Every command takes --json for machine-readable output.
Server address: --url, or MEMORY_CORE_URL, default http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any

import httpx

DEFAULT_URL = os.environ.get("MEMORY_CORE_URL", "http://127.0.0.1:8000")

# Generous: a real summarize was measured at up to ~57s and the server's own read budget
# defaults to 300s, so a client that gives up sooner would just hide successful work.
CLIENT_TIMEOUT = 360.0

# Actionable advice per error code. The auth/unavailable split is the whole point of
# having distinct codes: one is worth retrying, the other never is.
HINTS = {
    "summarizer_unauthorized": (
        "The server's AIS3 key was rejected. Fix AIS3_API_KEY where Memory Core runs, "
        "or start it with --fake-summarizer. Retrying will not help."
    ),
    "summarizer_unavailable": (
        "The summarizer backend was down or too slow. Your input is already in the "
        "journal, so it is safe to retry this command."
    ),
    "revision_conflict": (
        "Someone else changed the board first. Re-read with `show` and try again."
    ),
    "challenge_not_found": "No such challenge. `memctl ls` lists the ones that exist.",
    "proposal_invalid": "The summarizer returned something unusable. Retrying often helps.",
}


class ApiError(Exception):
    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body
        error = body.get("error") if isinstance(body, dict) else None
        self.code = (error or {}).get("code", f"http_{status}")
        self.message = (error or {}).get("message", json.dumps(body)[:300])
        self.details = (error or {}).get("details") or {}
        super().__init__(self.message)


def call(args: argparse.Namespace, method: str, path: str, **kwargs: Any) -> Any:
    with httpx.Client(base_url=args.url, timeout=CLIENT_TIMEOUT) as client:
        try:
            response = client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise SystemExit(f"cannot reach Memory Core at {args.url}: {exc}") from exc
    if response.status_code >= 400:
        try:
            body = response.json()
        except ValueError:
            body = {"error": {"code": f"http_{response.status_code}", "message": response.text}}
        raise ApiError(response.status_code, body)
    return response.json() if response.content else None


def current_revision(args: argparse.Namespace, challenge: str) -> int:
    """Read the revision a mutation will be based on.

    This narrows the lost-update window but does not close it: if an agent commits between
    this read and our write, the server answers 409 — which is the contract working, not a
    bug. Pass --expected-revision to pin a specific one instead.
    """

    return int(call(args, "GET", f"/v1/challenges/{challenge}/memory")["revision"])


# -- rendering ---------------------------------------------------------------

LAYER_LABEL = {"fact": "FACT", "assumption": "ASSUMPTION"}


def render_memory(board: dict[str, Any]) -> str:
    lines = [
        f"{board['challenge']['title']} ({board['challenge']['id']})  "
        f"rev {board['revision']}  template {board['challenge']['template']}@"
        f"{board['challenge']['template_version']}"
    ]
    if board["challenge"].get("summary"):
        lines += ["", f"  {board['challenge']['summary']}"]

    for layer in ("fact", "assumption"):
        sections = board.get(layer) or {}
        if not any(sections.values()):
            continue
        lines += ["", LAYER_LABEL[layer]]
        for section, items in sections.items():
            for item in items:
                origin = f"  <- {item['promoted_from']}" if item.get("promoted_from") else ""
                lines.append(f"  {item['id']}  ({section}) {item['text']}{origin}")
                if item.get("evidence"):
                    lines.append(f"        evidence: {', '.join(item['evidence'])}")

    guessing = board.get("guessing")
    if guessing:
        tried = [k for k, v in guessing["state"].items() if v.get("tried")]
        lines += ["", f"GUESSING  {guessing['catalog']}  ({len(tried)} tried)"]
    return "\n".join(lines)


def render_diff(envelope: dict[str, Any]) -> str:
    """The diff is the collaboration artifact; this is the whole reason it is machine
    readable. Person 2's renderer will be prettier — the shape is what matters here."""

    diff = envelope["diff"]
    before, after = envelope["revision_before"], envelope["revision_after"]
    if before == after:
        return f"rev {before} (unchanged — nothing worth recording)"

    lines = [f"rev {before} -> {after}"]
    if envelope.get("summary"):
        lines.append(f"  {envelope['summary']}")

    rows = [
        ("FACT", "+", diff["fact_added"]),
        ("FACT", "~", diff["fact_updated"]),
        ("FACT", "-", diff["fact_removed"]),
        ("ASSUMPTION", "+", diff["assumption_added"]),
        ("ASSUMPTION", "~", diff["assumption_updated"]),
        ("ASSUMPTION", "-", diff["assumption_removed"]),
        ("PROMOTED", ">", diff["promoted"]),
        ("DEMOTED", "<", diff["demoted"]),
    ]
    for label, mark, ids in rows:
        if ids:
            lines.append(f"  {mark} {label}: {', '.join(ids)}")
    if diff.get("template_changed"):
        change = diff["template_changed"]
        lines.append(f"  TEMPLATE: {change['from']} -> {change['to']}")
    return "\n".join(lines)


def emit(args: argparse.Namespace, payload: Any, text: str) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else text)


# -- commands ----------------------------------------------------------------


def cmd_ls(args: argparse.Namespace) -> None:
    ids = call(args, "GET", "/v1/challenges")
    emit(args, ids, "\n".join(ids) if ids else "(no challenges yet)")


def cmd_create(args: argparse.Namespace) -> None:
    body: dict[str, Any] = {"id": args.challenge, "title": args.title or args.challenge}
    if args.initial_input:
        body["initial_input"] = args.initial_input
    if args.template:
        body["template"] = args.template
    result = call(args, "POST", "/v1/challenges", json=body)
    emit(
        args,
        result,
        f"created {result['challenge_id']} at rev {result['revision']} "
        f"with template {result['template']}@{result['template_version']}",
    )


def cmd_note(args: argparse.Namespace) -> None:
    envelope = {
        "source": {"type": "human", "actor": args.actor},
        "input": {"type": "note", "content": args.text},
    }
    result = call(args, "POST", f"/v1/challenges/{args.challenge}/ingest", json=envelope)
    emit(args, result, render_diff(result))


def cmd_capture(args: argparse.Namespace) -> None:
    """Run a command, show the user its output, then ingest the result.

    Command capture only — no PTY recording. The user sees exactly what they would have
    seen running it themselves, and the argv/exit code/stdout/stderr become one journal
    record rather than one memory item per output line.
    """

    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("nothing to run: put the command after --")
    print(f"$ {' '.join(command)}", file=sys.stderr)
    completed = subprocess.run(command, capture_output=True, text=True)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)

    envelope = {
        "source": {"type": "human", "actor": args.actor},
        "input": {
            "type": "command_result",
            "command": command,
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        },
    }
    result = call(args, "POST", f"/v1/challenges/{args.challenge}/ingest", json=envelope)
    print("", file=sys.stderr)
    emit(args, result, render_diff(result))


def cmd_show(args: argparse.Namespace) -> None:
    params = {k: v for k, v in (("layer", args.layer), ("section", args.section)) if v}
    board = call(args, "GET", f"/v1/challenges/{args.challenge}/memory", params=params)
    emit(args, board, render_memory(board))


def cmd_query(args: argparse.Namespace) -> None:
    body = {"question": args.question}
    if args.layers:
        body["layers"] = args.layers
    result = call(args, "POST", f"/v1/challenges/{args.challenge}/query", json=body)
    refs = f"  [{', '.join(result['references'])}]" if result["references"] else ""
    emit(args, result, f"{result['answer']}{refs}  (rev {result['revision']})")


def cmd_guess(args: argparse.Namespace) -> None:
    body = {"count": args.count, "only_untried": not args.all, "seed": args.seed}
    result = call(args, "POST", f"/v1/challenges/{args.challenge}/guess", json=body)
    lines = [f"from {result['catalog']}:"] + [
        f"  {c['id']:<14} {c['title']}{'  (tried)' if c['tried'] else ''}"
        for c in result["candidates"]
    ]
    emit(args, result, "\n".join(lines))


def _mutate(args: argparse.Namespace, operations: list[dict[str, Any]]) -> None:
    revision = args.expected_revision
    if revision is None:
        revision = current_revision(args, args.challenge)
    body = {
        "expected_revision": revision,
        "actor": args.actor,
        "reason": args.reason,
        "operations": operations,
    }
    result = call(args, "POST", f"/v1/challenges/{args.challenge}/mutate", json=body)
    emit(args, result, render_diff(result))


def cmd_promote(args: argparse.Namespace) -> None:
    _mutate(args, [{"op": "promote", "id": args.item}])


def cmd_demote(args: argparse.Namespace) -> None:
    _mutate(args, [{"op": "demote", "id": args.item}])


def cmd_remove(args: argparse.Namespace) -> None:
    _mutate(args, [{"op": "remove", "id": args.item}])


def cmd_add(args: argparse.Namespace) -> None:
    _mutate(args, [{"op": "add", "layer": args.layer, "section": args.section, "text": args.text}])


def cmd_mark_tried(args: argparse.Namespace) -> None:
    _mutate(args, [{"op": "mark_tried", "technique": args.technique, "tried": not args.untried}])


def cmd_template(args: argparse.Namespace) -> None:
    revision = args.expected_revision
    if revision is None:
        revision = current_revision(args, args.challenge)
    body = {
        "template": args.template,
        "expected_revision": revision,
        "actor": args.actor,
        "reason": args.reason,
    }
    result = call(args, "POST", f"/v1/challenges/{args.challenge}/template", json=body)
    emit(args, result, render_diff(result))


# -- argument parsing --------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memctl", description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Memory Core base URL [{DEFAULT_URL}]")
    parser.add_argument("--actor", default=os.environ.get("USER", "human"))
    parser.add_argument("--json", action="store_true", help="raw JSON instead of prose")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    def with_challenge(name: str, func: Any, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("challenge")
        p.set_defaults(func=func)
        return p

    sub.add_parser("ls", help="list challenges").set_defaults(func=cmd_ls)

    p = with_challenge("create", cmd_create, "create a challenge at revision 0")
    p.add_argument("--title")
    p.add_argument("--initial-input", help="text the summarizer may classify from")
    p.add_argument("--template", help="pick the template explicitly, skipping classification")

    p = with_challenge("note", cmd_note, "ingest a human note")
    p.add_argument("text")

    p = with_challenge("capture", cmd_capture, "run a command and ingest its result")
    p.add_argument("command", nargs=argparse.REMAINDER, help="put the command after --")

    p = with_challenge("show", cmd_show, "print the whiteboard")
    p.add_argument("--layer", choices=["fact", "assumption", "guessing"])
    p.add_argument("--section")

    p = with_challenge("query", cmd_query, "ask a question, answered only from the board")
    p.add_argument("question")
    p.add_argument("--layers", nargs="+", choices=["fact", "assumption", "guessing"])

    p = with_challenge("guess", cmd_guess, "draw techniques from the template catalog")
    p.add_argument("--count", type=int, default=3)
    p.add_argument("--seed", type=int, help="fix the draw so a run is reproducible")
    p.add_argument("--all", action="store_true", help="include already-tried techniques")

    def mutation(name: str, func: Any, help_text: str) -> argparse.ArgumentParser:
        p = with_challenge(name, func, help_text)
        p.add_argument("--reason", required=True, help="required: overrides are attributable")
        p.add_argument(
            "--expected-revision",
            type=int,
            help="pin the revision; default re-reads it first",
        )
        return p

    mutation("promote", cmd_promote, "Assumption -> Fact (mints a new id)").add_argument("item")
    mutation("demote", cmd_demote, "Fact -> Assumption (mints a new id)").add_argument("item")
    mutation("remove", cmd_remove, "delete an item").add_argument("item")

    p = mutation("add", cmd_add, "add a Fact or Assumption by hand")
    p.add_argument("text")
    p.add_argument("--layer", choices=["fact", "assumption"], default="assumption")
    p.add_argument("--section", default="general")

    p = mutation("mark-tried", cmd_mark_tried, "flag a guessing technique as attempted")
    p.add_argument("technique")
    p.add_argument("--untried", action="store_true", help="clear the flag instead")

    p = mutation("template", cmd_template, "reclassify the challenge")
    p.add_argument("template")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except ApiError as exc:
        print(f"error [{exc.code}] {exc.message}", file=sys.stderr)
        if exc.details:
            print(f"  details: {json.dumps(exc.details, ensure_ascii=False)}", file=sys.stderr)
        hint = HINTS.get(exc.code)
        if hint:
            print(f"  {hint}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
