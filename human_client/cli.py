from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import Any

from .agent_cli import add_agent_parser
from .capture import run_capture
from .client import MemoryCoreClient
from .config import ClientConfig, config_from_args, require_challenge
from .errors import (
    CAPTURE_START_ERROR,
    OK,
    USAGE_ERROR,
    CoreConnectionError,
    CoreError,
    exit_code_for_error,
)
from .models import CommandResult
from .render import emit_json, render_diff, render_error, render_guess, render_memory, render_query


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return OK
    config = config_from_args(args)
    client = MemoryCoreClient(config.api_url, timeout=config.timeout)
    try:
        return int(args.handler(args, config, client))
    except ValueError as exc:
        if config.json_output:
            emit_json({"ok": False, "error": {"type": "usage", "message": str(exc)}})
        else:
            render_error(exc)
        return USAGE_ERROR
    except (CoreError, CoreConnectionError) as exc:
        return _handle_core_error(exc, config)
    finally:
        client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctfmem", description="Human HTTP client for Memory Core."
    )
    parser.add_argument("--api-url", default=None)
    parser.add_argument("--challenge", default=None)
    parser.add_argument("--actor", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--timeout", default=None)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--no-color", action="store_true")
    sub = parser.add_subparsers(dest="command")

    challenge = sub.add_parser("challenge")
    challenge_sub = challenge.add_subparsers(dest="challenge_command", required=True)
    create = challenge_sub.add_parser("create")
    create.add_argument("id")
    create.add_argument("--title")
    create.add_argument("--template")
    create.add_argument("--initial-input")
    create.set_defaults(handler=cmd_challenge_create)
    list_cmd = challenge_sub.add_parser("list")
    list_cmd.set_defaults(handler=cmd_challenge_list)
    show_challenge = challenge_sub.add_parser("show")
    show_challenge.add_argument("id", nargs="?")
    show_challenge.set_defaults(handler=cmd_challenge_show)

    note = sub.add_parser("note")
    note.add_argument("text", nargs="?")
    note.add_argument("--stdin", action="store_true")
    note.set_defaults(handler=cmd_note)

    capture = sub.add_parser("capture")
    capture.add_argument("command_argv", nargs=argparse.REMAINDER)
    capture.set_defaults(handler=cmd_capture)

    show = sub.add_parser("show")
    show.add_argument("--facts", action="store_true")
    show.add_argument("--assumptions", action="store_true")
    show.add_argument("--guessing", action="store_true")
    show.add_argument("--section")
    show.set_defaults(handler=cmd_show)

    query = sub.add_parser("query")
    query.add_argument("question")
    query.add_argument("--layer", choices=["fact", "assumption", "guessing"], action="append")
    query.set_defaults(handler=cmd_query)

    guess = sub.add_parser("guess")
    guess.add_argument("--count", type=int, default=3)
    guess.add_argument("--seed", type=int)
    guess.add_argument("--only-untried", action=argparse.BooleanOptionalAction, default=True)
    guess.set_defaults(handler=cmd_guess)

    mutate = sub.add_parser("mutate")
    mutate_sub = mutate.add_subparsers(dest="mutate_command", required=True)
    _mutation_common(mutate_sub.add_parser("add"), cmd_mutate_add).add_argument(
        "layer", choices=["fact", "assumption"]
    )
    _mutation_common(mutate_sub.add_parser("replace"), cmd_mutate_replace).add_argument("id")
    _mutation_common(mutate_sub.add_parser("remove"), cmd_mutate_remove).add_argument("id")
    _mutation_common(mutate_sub.add_parser("promote"), cmd_mutate_promote).add_argument("id")
    _mutation_common(mutate_sub.add_parser("demote"), cmd_mutate_demote).add_argument("id")
    _mutation_common(mutate_sub.add_parser("move"), cmd_mutate_move).add_argument("id")

    guessing = sub.add_parser("guessing")
    guessing_sub = guessing.add_subparsers(dest="guessing_command", required=True)
    mark = _mutation_common(guessing_sub.add_parser("mark-tried"), cmd_guessing_mark_tried)
    mark.add_argument("technique")
    mark.add_argument("--note")

    template = sub.add_parser("template")
    template_sub = template.add_subparsers(dest="template_command", required=True)
    set_template = template_sub.add_parser("set")
    set_template.add_argument("template")
    set_template.add_argument("--revision", type=int, required=True)
    set_template.add_argument("--reason", required=True)
    set_template.set_defaults(handler=cmd_template_set)

    add_agent_parser(sub)
    return parser


def _mutation_common(parser: argparse.ArgumentParser, handler: object) -> argparse.ArgumentParser:
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--section")
    parser.add_argument("--text")
    parser.set_defaults(handler=handler)
    return parser


def cmd_challenge_create(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    title = args.title or args.id
    payload = client.create_challenge(
        args.id, title=title, template=args.template, initial_input=args.initial_input
    )
    return _emit(payload, config)


def cmd_challenge_list(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    payload = client.list_challenges()
    return _emit(payload, config, text="\n".join(payload))


def cmd_challenge_show(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    challenge = require_challenge(config, args.id)
    payload = client.read_memory(challenge)
    return _emit(payload, config, renderer=render_memory)


def cmd_note(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    challenge = require_challenge(config)
    if args.stdin or args.text == "-":
        content = sys.stdin.read()
    elif args.text:
        content = args.text
    else:
        raise ValueError("note text is required")
    payload = client.ingest_note(challenge, content.strip(), actor=config.actor)
    return _emit(payload, config, renderer=render_diff)


def cmd_capture(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    challenge = require_challenge(config)
    command = list(args.command_argv)
    if command and command[0] == "--":
        command = command[1:]
    try:
        stream = sys.stderr if config.json_output else None
        result = run_capture(command, stdout=stream, stderr=stream)
    except OSError as exc:
        render_error(exc)
        return CAPTURE_START_ERROR
    payload = client.ingest_command_result(
        challenge,
        CommandResult(
            command=result.command,
            exit_code=result.exit_code,
            stdout=result.stdout,
            stderr=result.stderr,
        ),
        actor=config.actor,
    )
    if config.json_output:
        emit_json({"ok": True, "child_exit_code": result.exit_code, "ingest": payload})
    else:
        render_diff(payload)
    return result.exit_code


def cmd_show(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    challenge = require_challenge(config)
    layer = _show_layer(args)
    payload = client.read_memory(challenge, layer=layer, section=args.section)
    return _emit(payload, config, renderer=render_memory)


def cmd_query(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    challenge = require_challenge(config)
    payload = client.query(challenge, args.question, layers=args.layer)
    return _emit(payload, config, renderer=render_query)


def cmd_guess(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    challenge = require_challenge(config)
    payload = client.guess(
        challenge, count=args.count, only_untried=args.only_untried, seed=args.seed
    )
    return _emit(payload, config, renderer=render_guess)


def cmd_mutate_add(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    if not args.text:
        raise ValueError("--text is required")
    operation = {"op": "add", "layer": args.layer, "text": args.text}
    if args.section:
        operation["section"] = args.section
    return _mutate(args, config, client, [operation])


def cmd_mutate_replace(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    if not args.text:
        raise ValueError("--text is required")
    operation: dict[str, Any] = {"op": "replace", "id": args.id, "text": args.text}
    if args.section:
        operation["section"] = args.section
    return _mutate(args, config, client, [operation])


def cmd_mutate_remove(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    return _mutate(args, config, client, [{"op": "remove", "id": args.id}])


def cmd_mutate_promote(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    operation = {"op": "promote", "id": args.id}
    if args.section:
        operation["section"] = args.section
    return _mutate(args, config, client, [operation])


def cmd_mutate_demote(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    operation = {"op": "demote", "id": args.id}
    if args.section:
        operation["section"] = args.section
    return _mutate(args, config, client, [operation])


def cmd_mutate_move(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    if not args.section:
        raise ValueError("--section is required")
    return _mutate(args, config, client, [{"op": "move", "id": args.id, "section": args.section}])


def cmd_guessing_mark_tried(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    operation: dict[str, Any] = {"op": "mark_tried", "technique": args.technique, "tried": True}
    if args.note:
        operation["note"] = args.note
    return _mutate(args, config, client, [operation])


def cmd_template_set(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    challenge = require_challenge(config)
    payload = client.set_template(
        challenge,
        template=args.template,
        expected_revision=args.revision,
        actor=config.actor,
        reason=args.reason,
    )
    return _emit(payload, config, renderer=render_diff)


def _mutate(
    args: argparse.Namespace,
    config: ClientConfig,
    client: MemoryCoreClient,
    operations: list[dict[str, Any]],
) -> int:
    challenge = require_challenge(config)
    payload = client.mutate(
        challenge,
        expected_revision=args.revision,
        actor=config.actor,
        reason=args.reason,
        operations=operations,
    )
    return _emit(payload, config, renderer=render_diff)


def _show_layer(args: argparse.Namespace) -> str | None:
    selected = [("fact", args.facts), ("assumption", args.assumptions), ("guessing", args.guessing)]
    layers = [layer for layer, enabled in selected if enabled]
    if len(layers) > 1:
        raise ValueError("choose only one of --facts, --assumptions, --guessing")
    return layers[0] if layers else None


def _emit(
    payload: Any,
    config: ClientConfig,
    *,
    text: str | None = None,
    renderer: Callable[[Any], None] | None = None,
) -> int:
    if config.json_output:
        emit_json(payload)
    elif renderer is not None:
        renderer(payload)
    elif text is not None:
        print(text)
    else:
        print(payload)
    return OK


def _handle_core_error(error: CoreError | CoreConnectionError, config: ClientConfig) -> int:
    code = exit_code_for_error(error)
    if config.json_output:
        payload: dict[str, Any] = {"ok": False, "error": {"type": "connection"}}
        if isinstance(error, CoreError):
            payload["error"] = {
                "type": error.code or "core_error",
                "status_code": error.status_code,
                "message": error.message,
                "details": error.details,
            }
        else:
            payload["error"]["message"] = str(error)
        emit_json(payload)
    elif isinstance(error, CoreError) and (
        error.status_code == 409 or error.code == "revision_conflict"
    ):
        expected = error.details.get("expected_revision")
        current = error.details.get("current_revision")
        print("Mutation rejected: memory revision changed.", file=sys.stderr)
        print(f"Requested revision: {expected}", file=sys.stderr)
        print(f"Current revision:   {current}", file=sys.stderr)
        print(
            "No change was applied. Run `ctfmem show` and review before retrying.", file=sys.stderr
        )
    else:
        render_error(error)
    return code
