from __future__ import annotations

import argparse
import getpass
import shlex
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any

from .agent_cli import (
    PROVIDERS,
    _add_flagstop_arguments,
    add_agent_parser,
    cmd_agent_run,
    cmd_agent_status,
    cmd_agent_stop,
)
from .capture import run_capture
from .client import MemoryCoreClient
from .config import (
    DEFAULT_API_URL,
    DEFAULT_TIMEOUT,
    ClientConfig,
    SavedClientConfig,
    config_from_args,
    config_path_from_args,
    load_saved_client_config,
    merge_saved_client_config,
    require_challenge,
    save_client_config,
)
from .errors import (
    CAPTURE_START_ERROR,
    NOT_FOUND,
    OK,
    USAGE_ERROR,
    CoreConnectionError,
    CoreError,
    exit_code_for_error,
)
from .models import CommandResult
from .pending_capture import PendingCapture, PendingCaptureStore
from .render import emit_json, render_diff, render_error, render_guess, render_memory, render_query
from .shell import add_shell_parsers

_GLOBAL_OPTIONS_WITH_VALUE = frozenset(
    {"--api-url", "--challenge", "--actor", "--timeout", "--config"}
)
_GLOBAL_SWITCHES = frozenset({"--json", "--verbose", "--no-color"})
_COMPACT_SHELL_COMMANDS = frozenset({"status", "diff", "note", "memory"})


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = argv if argv is not None else sys.argv[1:]
    args = parser.parse_args(_normalise_global_options(raw_argv))
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
        return _handle_core_error(exc, config, client)
    finally:
        client.close()


def _normalise_global_options(argv: Sequence[str]) -> list[str]:
    """Accept global settings after a subcommand without changing subcommand semantics.

    ``argparse`` otherwise requires ``--challenge`` before ``/memory``.  Stop reordering at
    ``--`` so ``ctfmem capture -- curl --challenge value`` remains an untouched child command.
    """

    options: list[str] = []
    command: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            command.extend(argv[index:])
            break
        if token in _GLOBAL_SWITCHES:
            options.append(token)
            index += 1
            continue
        if token in _GLOBAL_OPTIONS_WITH_VALUE:
            options.append(token)
            if index + 1 < len(argv):
                options.append(argv[index + 1])
                index += 2
                continue
            index += 1
            continue
        command.append(token)
        index += 1

    compact = _expand_compact_shell_command(command)
    return options + compact


def _expand_compact_shell_command(command: list[str]) -> list[str]:
    """Expand ``challenge/memory`` to ``--challenge challenge /memory``."""

    if not command or command[0].startswith("-"):
        return command
    challenge, separator, name = command[0].rpartition("/")
    if not separator or not challenge or name not in _COMPACT_SHELL_COMMANDS:
        return command
    return ["--challenge", challenge, f"/{name}", *command[1:]]


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
    parser.add_argument(
        "--config",
        help="local non-secret defaults file (default: .ctfmem/client-config.json)",
    )
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

    add = sub.add_parser("add", help="interactively add a Fact or Assumption")
    add.add_argument("layer", choices=("fact", "assumption"), nargs="?")
    add.add_argument("--text")
    add.add_argument("--section")
    add.add_argument("--reason")
    add.add_argument("--revision", type=int)
    add.add_argument("--evidence", action="append", default=[])
    add.add_argument("--tag", dest="tags", action="append", default=[])
    add.set_defaults(handler=cmd_add)

    config = sub.add_parser("config", help="manage local, non-secret CLI defaults")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_show = config_sub.add_parser("show")
    config_show.set_defaults(handler=cmd_config_show)
    config_set = config_sub.add_parser("set")
    config_set.add_argument("--api-url", dest="config_api_url")
    config_set.add_argument("--challenge", dest="config_challenge")
    config_set.add_argument("--actor", dest="config_actor")
    config_set.add_argument("--timeout", dest="config_timeout", type=float)
    config_set.add_argument("--agent-workspace", dest="config_agent_workspace")
    config_set.set_defaults(handler=cmd_config_set)

    setup = sub.add_parser(
        "setup", aliases=["init"], help="interactively create local non-secret defaults"
    )
    setup.add_argument("--no-input", action="store_true", help="fail instead of prompting")
    setup.add_argument("--agent-workspace")
    setup.set_defaults(handler=cmd_setup)

    capture = sub.add_parser("capture")
    capture.add_argument(
        "--wait",
        action="store_true",
        help="wait for Memory Core ingest instead of delivering the saved result in background",
    )
    capture.add_argument("command_argv", nargs=argparse.REMAINDER)
    capture.set_defaults(handler=cmd_capture)

    pending_capture = sub.add_parser(
        "capture-pending", help="inspect or explicitly retry capture results awaiting ingest"
    )
    pending_capture_sub = pending_capture.add_subparsers(
        dest="pending_capture_command", required=True
    )
    pending_capture_sub.add_parser("list").set_defaults(handler=cmd_pending_capture_list)
    retry_capture = pending_capture_sub.add_parser("retry")
    retry_capture.add_argument("id")
    retry_capture.set_defaults(handler=cmd_pending_capture_retry)
    deliver_capture = pending_capture_sub.add_parser("deliver", help=argparse.SUPPRESS)
    deliver_capture.add_argument("id")
    deliver_capture.set_defaults(handler=cmd_pending_capture_deliver)

    show = sub.add_parser("show")
    _add_memory_lookup_arguments(show)
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
    add_shell_parsers(sub)
    _add_slash_parsers(sub)
    return parser


def _mutation_common(parser: argparse.ArgumentParser, handler: object) -> argparse.ArgumentParser:
    parser.add_argument("--revision", type=int, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--section")
    parser.add_argument("--text")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--tag", dest="tags", action="append", default=[])
    parser.add_argument("--clear-evidence", action="store_true")
    parser.add_argument("--clear-tags", action="store_true")
    parser.set_defaults(handler=handler)
    return parser


def _add_slash_parsers(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add thin, non-fullscreen aliases for the operations exposed by the local page.

    Long-form commands remain the scripting interface.  Slash parsers intentionally leave
    a few values optional so a human at an interactive terminal can be prompted one field at
    a time; their handlers then call the same long-form command functions below.
    """

    challenge = subparsers.add_parser("/challenge", help="slash alias for challenge actions")
    challenge_sub = challenge.add_subparsers(dest="slash_challenge_command", required=True)
    challenge_sub.add_parser("list").set_defaults(handler=cmd_challenge_list)
    use = challenge_sub.add_parser("use")
    use.add_argument("id", nargs="?")
    use.set_defaults(handler=cmd_slash_challenge_use)
    create = challenge_sub.add_parser("create")
    create.add_argument("id", nargs="?")
    create.add_argument("--title")
    create.add_argument("--template")
    create.add_argument("--initial-input")
    create.set_defaults(handler=cmd_slash_challenge_create)

    memory = subparsers.add_parser(
        "/memory", help="show or deterministically find whiteboard items"
    )
    _add_memory_lookup_arguments(memory)
    memory.add_argument("--layer", choices=("fact", "assumption", "guessing"))
    memory.set_defaults(handler=cmd_slash_show)

    note = subparsers.add_parser("/note", help="record a human observation")
    note.add_argument("text", nargs="?")
    note.add_argument("--stdin", action="store_true")
    note.set_defaults(handler=cmd_slash_note)

    capture = subparsers.add_parser("/capture", help="run a command and record its result")
    capture.add_argument("--wait", action="store_true")
    capture.add_argument("command_argv", nargs=argparse.REMAINDER)
    capture.set_defaults(handler=cmd_slash_capture)

    pending_capture = subparsers.add_parser(
        "/capture-pending", help="inspect or explicitly retry capture results awaiting ingest"
    )
    pending_capture_sub = pending_capture.add_subparsers(
        dest="slash_pending_capture_command", required=True
    )
    pending_capture_sub.add_parser("list").set_defaults(handler=cmd_pending_capture_list)
    retry_capture = pending_capture_sub.add_parser("retry")
    retry_capture.add_argument("id")
    retry_capture.set_defaults(handler=cmd_slash_pending_capture_retry)

    add_direct = subparsers.add_parser("/add", help="interactively add a Fact or Assumption")
    add_direct.add_argument("layer", choices=("fact", "assumption"), nargs="?")
    add_direct.add_argument("--text")
    add_direct.add_argument("--section")
    add_direct.add_argument("--reason")
    add_direct.add_argument("--revision", type=int)
    add_direct.add_argument("--evidence", action="append", default=[])
    add_direct.add_argument("--tag", dest="tags", action="append", default=[])
    add_direct.set_defaults(handler=cmd_slash_add)

    command = subparsers.add_parser("/command", help="record an already-run command result")
    command.add_argument("--command")
    command.add_argument("--exit-code", type=int)
    command.add_argument("--stdout")
    command.add_argument("--stderr")
    command.add_argument(
        "--stdin",
        nargs="?",
        const="json",
        choices=("json", "stdout", "stderr"),
        help="read a JSON result, or the named output stream, from standard input",
    )
    command.set_defaults(handler=cmd_slash_command)

    edit = subparsers.add_parser("/edit", help="apply one explicit whiteboard mutation")
    edit_sub = edit.add_subparsers(dest="slash_edit_command", required=True)
    add = _slash_mutation_parser(edit_sub.add_parser("add"), cmd_slash_mutate_add)
    add.add_argument("layer", choices=("fact", "assumption"))
    replace_item = _slash_mutation_parser(edit_sub.add_parser("replace"), cmd_slash_mutate_replace)
    replace_item.add_argument("id")
    for name, handler in (
        ("remove", cmd_slash_mutate_remove),
        ("promote", cmd_slash_mutate_promote),
        ("demote", cmd_slash_mutate_demote),
        ("move", cmd_slash_mutate_move),
    ):
        item = _slash_mutation_parser(edit_sub.add_parser(name), handler)
        item.add_argument("id")

    template = subparsers.add_parser("/template", help="reclassify the whiteboard")
    template_sub = template.add_subparsers(dest="slash_template_command", required=True)
    set_template = template_sub.add_parser("set")
    set_template.add_argument("template", nargs="?")
    set_template.add_argument("--revision", type=int)
    set_template.add_argument("--reason")
    set_template.set_defaults(handler=cmd_slash_template_set)

    guess = subparsers.add_parser("/guess", help="draw techniques from the guessing catalog")
    guess.add_argument("--count", type=int, default=3)
    guess.add_argument("--seed", type=int)
    guess.add_argument("--only-untried", action=argparse.BooleanOptionalAction, default=True)
    guess.set_defaults(handler=cmd_slash_guess)

    tried = subparsers.add_parser("/try", help="mark a guessing technique as tried")
    tried.add_argument("technique")
    tried.add_argument("--note")
    tried.add_argument("--revision", type=int)
    tried.add_argument("--reason")
    tried.set_defaults(handler=cmd_slash_try)

    ask = subparsers.add_parser("/ask", help="query fact, assumption, or guessing layers")
    ask.add_argument("question", nargs="?")
    ask.add_argument("--layer", choices=("fact", "assumption", "guessing"), action="append")
    ask.add_argument("--fact", action="store_true")
    ask.add_argument("--assumption", action="store_true")
    ask.add_argument("--guessing", action="store_true")
    ask.set_defaults(handler=cmd_slash_ask)

    agent = subparsers.add_parser("/agent", help="initialize or control an agent run")
    agent_sub = agent.add_subparsers(dest="slash_agent_command", required=True)
    init = agent_sub.add_parser("init")
    init.add_argument("path", nargs="?")
    init.set_defaults(handler=cmd_slash_agent_init)
    run = agent_sub.add_parser("run")
    run.add_argument("prompt", nargs="?")
    _add_agent_run_options(run, workspace_default=None)
    run.set_defaults(handler=cmd_slash_agent_run)
    agent_commands: tuple[tuple[str, object], ...] = (
        ("status", cmd_slash_agent_status),
        ("stop", cmd_slash_agent_stop),
    )
    for name, agent_handler in agent_commands:
        item = agent_sub.add_parser(name)
        item.add_argument("--runtime-dir", default=None)
        item.set_defaults(handler=agent_handler)


def _add_memory_lookup_arguments(parser: argparse.ArgumentParser) -> None:
    """Add compatible browsing and deterministic Fact/Assumption lookup arguments."""

    parser.add_argument("lookup_layer", choices=("fact", "assumption", "guessing"), nargs="?")
    parser.add_argument("item_id", nargs="?")
    parser.add_argument("--facts", action="store_true")
    parser.add_argument("--assumptions", action="store_true")
    parser.add_argument("--guessing", action="store_true")
    parser.add_argument("--section")
    parser.add_argument("--find", dest="find_text")
    parser.add_argument("--tag", dest="tags", action="append", default=[])


def _slash_mutation_parser(
    parser: argparse.ArgumentParser, handler: object
) -> argparse.ArgumentParser:
    parser.add_argument("--revision", type=int)
    parser.add_argument("--reason")
    parser.add_argument("--section")
    parser.add_argument("--text")
    parser.add_argument("--evidence", action="append", default=[])
    parser.add_argument("--tag", dest="tags", action="append", default=[])
    parser.add_argument("--clear-evidence", action="store_true")
    parser.add_argument("--clear-tags", action="store_true")
    parser.set_defaults(handler=handler)
    return parser


def _add_agent_run_options(
    parser: argparse.ArgumentParser, *, workspace_default: str | None
) -> None:
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default=None,
        help="Agent provider. Defaults to CTFMEM_AGENT_PROVIDER or claude.",
    )
    parser.add_argument("--mode", choices=("normal", "yolo"), default="normal")
    parser.add_argument("--session", choices=("same", "new"), default="same")
    parser.add_argument("--workspace", default=workspace_default)
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=int, default=10)
    parser.add_argument("--max-cost-usd", type=float, default=5.0)
    parser.add_argument("--idle-turns", type=int, default=3)
    parser.add_argument(
        "--memory-policy",
        choices=("full", "facts", "facts-assumptions", "none"),
        default="full",
    )
    parser.add_argument("--guess-seed", type=int)
    parser.add_argument("--fake-agent", metavar="SCENARIO")
    parser.add_argument("--runtime-dir", default=None)
    parser.add_argument("--no-memory-skill", action="store_true")
    _add_flagstop_arguments(parser)


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


def cmd_slash_challenge_use(
    args: argparse.Namespace, _config: ClientConfig, _client: MemoryCoreClient
) -> int:
    challenge = _required_slash_value(args.id, "challenge id")
    path = config_path_from_args(args)
    save_client_config(
        path, merge_saved_client_config(load_saved_client_config(path), challenge=challenge)
    )
    print(f"ctfmem: current challenge is {challenge}")
    return OK


def cmd_slash_challenge_create(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    args.id = _required_slash_value(args.id, "challenge id")
    return cmd_challenge_create(args, config, client)


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
        content = _required_slash_value(None, "observation")
    payload = client.ingest_note(challenge, content.strip(), actor=config.actor)
    return _emit(payload, config, renderer=render_diff)


def cmd_add(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    """CLI equivalent of the Web UI Override form, with prompts for terminal users."""

    args.layer = _required_slash_value(args.layer, "layer (fact or assumption)")
    if args.layer not in {"fact", "assumption"}:
        raise ValueError("layer must be fact or assumption")
    args.text = _required_slash_value(args.text, "text")
    challenge = require_challenge(config)
    board = client.read_memory(challenge)
    sections = board.get(args.layer, {})
    if not isinstance(sections, dict):
        raise ValueError("Memory Core returned an invalid section layout")
    if args.section is None:
        args.section = _choose_section(sections)
    if args.section not in sections:
        raise ValueError(f"section {args.section!r} is not valid for {args.layer}")
    args.reason = _required_slash_value(args.reason, "reason")
    if args.revision is None:
        revision = board.get("revision")
        if not isinstance(revision, int):
            raise ValueError("Memory Core returned a whiteboard without an integer revision")
        args.revision = revision
    return cmd_mutate_add(args, config, client)


def cmd_slash_add(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    return cmd_add(args, _slash_config_with_challenge(config), client)


def cmd_slash_note(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    if not args.stdin and not args.text:
        args.text = _required_slash_value(None, "note text")
    return cmd_note(args, _slash_config_with_challenge(config), client)


def cmd_config_show(
    args: argparse.Namespace, _config: ClientConfig, _client: MemoryCoreClient
) -> int:
    path = config_path_from_args(args)
    saved = load_saved_client_config(path)
    print(f"ctfmem config: {path}")
    for name, value in saved.to_dict().items():
        print(f"{name}={value}")
    return OK


def cmd_config_set(
    args: argparse.Namespace, _config: ClientConfig, _client: MemoryCoreClient
) -> int:
    api_url = args.config_api_url or args.api_url
    challenge = args.config_challenge or args.challenge
    actor = args.config_actor or args.actor
    timeout = args.config_timeout if args.config_timeout is not None else args.timeout
    agent_workspace = args.config_agent_workspace
    values = (api_url, challenge, actor, timeout, agent_workspace)
    if all(value is None for value in values):
        raise ValueError(
            "set at least one of --api-url, --challenge, --actor, --timeout, or --agent-workspace"
        )
    if timeout is not None and float(timeout) <= 0:
        raise ValueError("--timeout must be positive")
    path = config_path_from_args(args)
    saved = merge_saved_client_config(
        load_saved_client_config(path),
        api_url=api_url,
        challenge=challenge,
        actor=actor,
        timeout=float(timeout) if timeout is not None else None,
        agent_workspace=(
            str(Path(agent_workspace).expanduser().resolve()) if agent_workspace else None
        ),
    )
    save_client_config(path, saved)
    print(f"ctfmem: saved non-secret defaults to {path}")
    return OK


def cmd_setup(args: argparse.Namespace, _config: ClientConfig, _client: MemoryCoreClient) -> int:
    """Guide first-time configuration without placing ordinary settings in ENV."""

    path = config_path_from_args(args)
    saved = load_saved_client_config(path)
    api_url = _setup_value(args.api_url, saved.api_url, DEFAULT_API_URL, "Memory Core URL", args)
    challenge = _setup_value(args.challenge, saved.challenge, None, "Challenge ID", args)
    actor = _setup_value(args.actor, saved.actor, getpass.getuser(), "Operator name", args)
    timeout_text = _setup_value(
        args.timeout, saved.timeout, DEFAULT_TIMEOUT, "HTTP timeout (seconds)", args
    )
    workspace = _setup_value(
        args.agent_workspace,
        saved.agent_workspace,
        None,
        "Agent workspace (optional)",
        args,
    )
    if not challenge:
        raise ValueError("a challenge id is required; rerun ctfmem setup --challenge <id>")
    try:
        timeout = float(str(timeout_text))
    except ValueError as exc:
        raise ValueError("timeout must be numeric") from exc
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    save_client_config(
        path,
        SavedClientConfig(
            api_url=str(api_url),
            challenge=str(challenge),
            actor=str(actor),
            timeout=timeout,
            agent_workspace=(
                str(Path(str(workspace)).expanduser().resolve()) if workspace else None
            ),
        ),
    )
    print(f"ctfmem: setup complete; saved non-secret defaults to {path}")
    print("ctfmem: use 'ctfmem /memory' now; model credentials belong only in the server process.")
    return OK


def _setup_value(
    supplied: object,
    saved: object,
    default: object,
    label: str,
    args: argparse.Namespace,
) -> object:
    value = supplied if supplied is not None else saved if saved is not None else default
    if args.no_input:
        return value
    prompt = f"{label}"
    if value is not None:
        prompt += f" [{value}]"
    entered = input(f"{prompt}: ").strip()
    return entered or value


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
    command_result = CommandResult(
        command=result.command,
        exit_code=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr,
        stdout_base64=result.stdout_base64,
        stderr_base64=result.stderr_base64,
    )
    pending = _pending_capture_store(args).enqueue(
        challenge=challenge, actor=config.actor, result=command_result
    )
    if not args.wait:
        delivery_started = _start_background_capture_delivery(args, config, challenge, pending)
        if config.json_output:
            emit_json(
                {
                    "ok": True,
                    "child_exit_code": result.exit_code,
                    "pending_capture_id": pending.id,
                    "delivery": "background" if delivery_started else "queued",
                }
            )
        else:
            if delivery_started:
                print(
                    f"Capture saved as {pending.id}; "
                    "Memory Core delivery continues in the background.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Capture saved as {pending.id}; delivery was not started. "
                    f"Retry it explicitly with `ctfmem capture-pending retry {pending.id}`.",
                    file=sys.stderr,
                )
        return result.exit_code
    try:
        payload = client.ingest_command_result(challenge, command_result, actor=config.actor)
    except (CoreError, CoreConnectionError) as exc:
        return _capture_ingest_failed(exc, config, pending)
    _pending_capture_store(args).remove(pending.id)
    if config.json_output:
        emit_json({"ok": True, "child_exit_code": result.exit_code, "ingest": payload})
    else:
        render_diff(payload)
    return result.exit_code


def cmd_slash_capture(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    return cmd_capture(args, _slash_config_with_challenge(config), client)


def cmd_pending_capture_list(
    args: argparse.Namespace, config: ClientConfig, _client: MemoryCoreClient
) -> int:
    pending = [entry.summary() for entry in _pending_capture_store(args).load()]
    text = "\n".join(_format_pending_capture(entry) for entry in pending)
    return _emit(pending, config, text=text)


def cmd_pending_capture_retry(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    store = _pending_capture_store(args)
    pending = store.get(args.id)
    try:
        payload = client.ingest_command_result(
            pending.challenge, pending.result, actor=pending.actor
        )
    except (CoreError, CoreConnectionError) as exc:
        return _capture_ingest_failed(exc, config, pending)
    store.remove(pending.id)
    if config.json_output:
        emit_json({"ok": True, "pending_capture_id": pending.id, "ingest": payload})
    else:
        render_diff(payload)
    return OK


def cmd_pending_capture_deliver(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    """Deliver one newly captured result from the detached uploader.

    This is deliberately separate from ``retry``: it performs the one initial request that
    follows a successful local capture.  A later retry remains an explicit user action because
    a failed HTTP request may already have reached Memory Core.
    """

    store = _pending_capture_store(args)
    pending = store.get(args.id)
    try:
        client.ingest_command_result(pending.challenge, pending.result, actor=pending.actor)
    except (CoreError, CoreConnectionError) as exc:
        return _capture_ingest_failed(exc, config, pending)
    store.remove(pending.id)
    return OK


def cmd_slash_pending_capture_retry(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    return cmd_pending_capture_retry(args, _slash_config_with_challenge(config), client)


def _pending_capture_store(args: argparse.Namespace) -> PendingCaptureStore:
    return PendingCaptureStore(config_path_from_args(args).parent / "pending-captures.json")


def _start_background_capture_delivery(
    args: argparse.Namespace,
    config: ClientConfig,
    challenge: str,
    pending: PendingCapture,
) -> bool:
    """Start one detached delivery attempt without retaining the operator's terminal.

    The complete result has already reached the durable local queue at this point.  The child
    inherits neither terminal stream nor stdin, so it cannot consume keystrokes or delay the
    prompt.  If it cannot deliver, it leaves the queue entry for an explicit retry.
    """

    config_path = config_path_from_args(args).resolve()
    command = [
        sys.executable,
        "-m",
        "human_client.cli",
        "--api-url",
        config.api_url,
        "--challenge",
        challenge,
        "--actor",
        config.actor,
        "--timeout",
        str(config.timeout),
        "--config",
        str(config_path),
        "capture-pending",
        "deliver",
        pending.id,
    ]
    try:
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return True
    except OSError as exc:
        print(
            f"ctfmem: could not start background delivery ({exc}); "
            f"capture remains queued as {pending.id}.",
            file=sys.stderr,
        )
        return False


def _format_pending_capture(entry: dict[str, object]) -> str:
    command = entry["command"]
    display = " ".join(str(part) for part in command) if isinstance(command, list) else str(command)
    return f"{entry['id']}  exit {entry['exit_code']}  {display}"


def _capture_ingest_failed(
    error: CoreError | CoreConnectionError, config: ClientConfig, pending: PendingCapture
) -> int:
    """Keep a failed capture locally; retry must remain an explicit operator decision."""

    code = exit_code_for_error(error)
    if config.json_output:
        payload: dict[str, Any] = {
            "ok": False,
            "pending_capture_id": pending.id,
        }
        if isinstance(error, CoreError):
            payload["error"] = {"type": error.code or "core_error", "message": error.message}
        else:
            payload["error"] = {"type": "connection", "message": str(error)}
        emit_json(payload)
    else:
        render_error(error)
        print(
            f"Capture output retained as {pending.id}. Retry explicitly with "
            f"`ctfmem capture-pending retry {pending.id}`.",
            file=sys.stderr,
        )
    return code


def cmd_slash_command(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    """Ingest a result collected outside `ctfmem /capture`.

    ``--stdin`` without a stream name accepts a JSON object so scripts can preserve both
    multiline stdout and stderr without shell quoting.  Named stream mode is convenient for
    a single pasted heredoc.
    """

    stdin_payload: dict[str, Any] = {}
    if args.stdin:
        raw = sys.stdin.read()
        if args.stdin == "json":
            import json

            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("--stdin expects a JSON command result object") from exc
            if not isinstance(parsed, dict):
                raise ValueError("--stdin expects a JSON command result object")
            stdin_payload = parsed
        else:
            stdin_payload[args.stdin] = raw

    raw_command = args.command or stdin_payload.get("command")
    if isinstance(raw_command, list):
        command = [str(part) for part in raw_command]
    elif isinstance(raw_command, str):
        try:
            command = shlex.split(raw_command)
        except ValueError as exc:
            raise ValueError("--command must be valid shell-style argv") from exc
    else:
        command = []
    if not command:
        raw_command = _required_slash_value(None, "command")
        command = shlex.split(raw_command)

    exit_code = args.exit_code
    if exit_code is None:
        candidate = stdin_payload.get("exit_code")
        if isinstance(candidate, int):
            exit_code = candidate
        elif isinstance(candidate, str) and candidate.lstrip("-").isdigit():
            exit_code = int(candidate)
        else:
            exit_code = int(_required_slash_value(None, "exit code"))
    stdout = args.stdout if args.stdout is not None else stdin_payload.get("stdout", "")
    stderr = args.stderr if args.stderr is not None else stdin_payload.get("stderr", "")
    if not isinstance(stdout, str) or not isinstance(stderr, str):
        raise ValueError("stdout and stderr must be strings")
    payload = client.ingest_command_result(
        require_challenge(_slash_config_with_challenge(config)),
        CommandResult(command=command, exit_code=exit_code, stdout=stdout, stderr=stderr),
        actor=config.actor,
    )
    return _emit(payload, config, renderer=render_diff)


def cmd_show(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    challenge = require_challenge(config)
    layer, item_id, find_text, tags = _memory_lookup_arguments(args)
    payload = client.read_memory(challenge, layer=layer, section=args.section)
    if item_id is None and find_text is None and not tags:
        return _emit(payload, config, renderer=render_memory)

    filtered, count = _filter_memory_payload(
        payload, layer=layer, item_id=item_id, find_text=find_text, tags=tags
    )
    if item_id is not None and count == 0:
        return _emit_memory_item_not_found(item_id, layer, config)
    if count == 0:
        return _emit_memory_no_matches(layer, filtered, config)
    payload = filtered
    return _emit(payload, config, renderer=render_memory)


def cmd_slash_show(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    if getattr(args, "layer", None):
        setattr(args, f"{args.layer}s" if args.layer != "guessing" else "guessing", True)
    return cmd_show(args, _slash_config_with_challenge(config), client)


def cmd_query(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    challenge = require_challenge(config)
    payload = client.query(challenge, args.question, layers=args.layer)
    return _emit(payload, config, renderer=render_query)


def cmd_slash_ask(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    args.question = _required_slash_value(args.question, "question")
    selected = [
        layer
        for layer, enabled in (
            ("fact", args.fact),
            ("assumption", args.assumption),
            ("guessing", args.guessing),
        )
        if enabled
    ]
    args.layer = (args.layer or []) + selected
    return cmd_query(args, _slash_config_with_challenge(config), client)


def cmd_guess(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    challenge = require_challenge(config)
    payload = client.guess(
        challenge, count=args.count, only_untried=args.only_untried, seed=args.seed
    )
    return _emit(payload, config, renderer=render_guess)


def cmd_slash_guess(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    return cmd_guess(args, _slash_config_with_challenge(config), client)


def cmd_mutate_add(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    if not args.text:
        raise ValueError("--text is required")
    operation = {"op": "add", "layer": args.layer, "text": args.text}
    if args.section:
        operation["section"] = args.section
    if args.evidence:
        operation["evidence"] = args.evidence
    if args.tags:
        operation["tags"] = args.tags
    return _mutate(args, config, client, [operation])


def cmd_slash_mutate_add(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    args.text = _required_slash_value(args.text, "text")
    _prepare_slash_mutation(args, config, client)
    return cmd_mutate_add(args, _slash_config_with_challenge(config), client)


def cmd_mutate_replace(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    if not args.text:
        raise ValueError("--text is required")
    operation: dict[str, Any] = {"op": "replace", "id": args.id, "text": args.text}
    if args.section:
        operation["section"] = args.section
    if args.clear_evidence or args.evidence:
        operation["evidence"] = args.evidence
    if args.clear_tags or args.tags:
        operation["tags"] = args.tags
    return _mutate(args, config, client, [operation])


def cmd_slash_mutate_replace(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    args.text = _required_slash_value(args.text, "text")
    _prepare_slash_mutation(args, config, client)
    return cmd_mutate_replace(args, _slash_config_with_challenge(config), client)


def cmd_mutate_remove(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    return _mutate(args, config, client, [{"op": "remove", "id": args.id}])


def cmd_slash_mutate_remove(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    _prepare_slash_mutation(args, config, client)
    return cmd_mutate_remove(args, _slash_config_with_challenge(config), client)


def cmd_mutate_promote(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    operation = {"op": "promote", "id": args.id}
    if args.section:
        operation["section"] = args.section
    return _mutate(args, config, client, [operation])


def cmd_slash_mutate_promote(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    _prepare_slash_mutation(args, config, client)
    return cmd_mutate_promote(args, _slash_config_with_challenge(config), client)


def cmd_mutate_demote(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    operation = {"op": "demote", "id": args.id}
    if args.section:
        operation["section"] = args.section
    return _mutate(args, config, client, [operation])


def cmd_slash_mutate_demote(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    _prepare_slash_mutation(args, config, client)
    return cmd_mutate_demote(args, _slash_config_with_challenge(config), client)


def cmd_mutate_move(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    if not args.section:
        raise ValueError("--section is required")
    return _mutate(args, config, client, [{"op": "move", "id": args.id, "section": args.section}])


def cmd_slash_mutate_move(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    args.section = _required_slash_value(args.section, "section")
    _prepare_slash_mutation(args, config, client)
    return cmd_mutate_move(args, _slash_config_with_challenge(config), client)


def cmd_guessing_mark_tried(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    operation: dict[str, Any] = {"op": "mark_tried", "technique": args.technique, "tried": True}
    if args.note:
        operation["note"] = args.note
    return _mutate(args, config, client, [operation])


def cmd_slash_try(args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient) -> int:
    _prepare_slash_mutation(args, config, client)
    return cmd_guessing_mark_tried(args, _slash_config_with_challenge(config), client)


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


def cmd_slash_template_set(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    args.template = _required_slash_value(args.template, "template")
    _prepare_slash_mutation(args, config, client)
    return cmd_template_set(args, _slash_config_with_challenge(config), client)


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


def cmd_slash_agent_init(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    args.path = _required_slash_value(args.path, "agent workspace path")
    # Import here to keep `agent_cli` as the owner of the persistent workspace operation.
    from .agent_cli import cmd_agent_init

    return cmd_agent_init(args, config, client)


def cmd_slash_agent_run(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    args.prompt = _required_slash_value(args.prompt, "agent prompt")
    if args.workspace is None:
        workspace = config.agent_workspace
        if not workspace:
            raise ValueError(
                "agent workspace is not initialized; run `ctfmem /agent init <path>` "
                "or pass --workspace"
            )
        args.workspace = workspace
    return cmd_agent_run(args, _slash_config_with_challenge(config), client)


def cmd_slash_agent_status(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    return cmd_agent_status(args, _slash_config_with_challenge(config), client)


def cmd_slash_agent_stop(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> int:
    return cmd_agent_stop(args, _slash_config_with_challenge(config), client)


def _prepare_slash_mutation(
    args: argparse.Namespace, config: ClientConfig, client: MemoryCoreClient
) -> None:
    """Fill only the values a terminal user may omit; never retry a failed mutation."""

    slash_config = _slash_config_with_challenge(config)
    args.reason = _required_slash_value(args.reason, "reason")
    if args.revision is None:
        board = client.read_memory(require_challenge(slash_config))
        revision = board.get("revision")
        if not isinstance(revision, int):
            raise ValueError("Memory Core returned a whiteboard without an integer revision")
        args.revision = revision


def _slash_config_with_challenge(config: ClientConfig) -> ClientConfig:
    if config.challenge:
        return config
    return replace(config, challenge=_required_slash_value(None, "challenge id"))


def _required_slash_value(value: str | None, label: str) -> str:
    if value is not None and value.strip():
        return value
    if not sys.stdin.isatty():
        raise ValueError(f"{label} is required")
    try:
        entered = input(f"{label}: ").strip()
    except EOFError as exc:
        raise ValueError(f"{label} is required") from exc
    if not entered:
        raise ValueError(f"{label} is required")
    return entered


def _choose_section(sections: dict[str, Any]) -> str:
    """Choose a valid section, accepting either its number or its name on a TTY."""

    names = [str(name) for name in sections]
    if not names:
        raise ValueError("the selected layer has no sections")
    if not sys.stdin.isatty():
        raise ValueError(f"section is required; choose one of: {', '.join(names)}")
    print("Available sections:", file=sys.stderr)
    for index, name in enumerate(names, start=1):
        print(f"  {index}. {name}", file=sys.stderr)
    selected = _required_slash_value(None, "section number or name")
    if selected.isdigit():
        index = int(selected)
        if 1 <= index <= len(names):
            return names[index - 1]
    if selected in names:
        return selected
    raise ValueError(f"section {selected!r} is not valid; choose a listed section")


def _show_layer(args: argparse.Namespace) -> str | None:
    selected = [("fact", args.facts), ("assumption", args.assumptions), ("guessing", args.guessing)]
    layers = [layer for layer, enabled in selected if enabled]
    if len(layers) > 1:
        raise ValueError("choose only one of --facts, --assumptions, --guessing")
    return layers[0] if layers else None


def _memory_lookup_arguments(
    args: argparse.Namespace,
) -> tuple[str | None, str | None, str | None, list[str]]:
    """Resolve compact layer syntax without weakening the original flag syntax."""

    flagged_layer = _show_layer(args)
    positional_layer = args.lookup_layer
    if (
        flagged_layer is not None
        and positional_layer is not None
        and flagged_layer != positional_layer
    ):
        raise ValueError("the positional layer and --facts/--assumptions/--guessing must agree")
    layer = positional_layer or flagged_layer
    item_id = args.item_id
    find_text = args.find_text
    tags = list(args.tags)
    has_lookup = item_id is not None or find_text is not None or bool(tags)
    if has_lookup and layer not in {"fact", "assumption"}:
        raise ValueError("Fact/Assumption lookup requires a fact or assumption layer")
    if item_id is not None and (find_text is not None or tags):
        raise ValueError("an item ID cannot be combined with --find or --tag")
    return layer, item_id, find_text, tags


def _filter_memory_payload(
    payload: dict[str, Any],
    *,
    layer: str | None,
    item_id: str | None,
    find_text: str | None,
    tags: list[str],
) -> tuple[dict[str, Any], int]:
    """Apply local, deterministic filters to a Memory Core read response."""

    if layer not in {"fact", "assumption"}:
        raise ValueError("Fact/Assumption lookup requires a fact or assumption layer")
    sections = payload.get(layer)
    if not isinstance(sections, dict):
        raise ValueError("Memory Core returned an invalid section layout")

    filtered: dict[str, list[dict[str, Any]]] = {}
    for section, items in sections.items():
        if not isinstance(items, list):
            continue
        matches = [
            item
            for item in items
            if isinstance(item, dict)
            and _memory_item_matches(item, item_id=item_id, find_text=find_text, tags=tags)
        ]
        if matches:
            filtered[str(section)] = matches
    result = dict(payload)
    result[layer] = filtered
    return result, sum(len(items) for items in filtered.values())


def _memory_item_matches(
    item: dict[str, Any], *, item_id: str | None, find_text: str | None, tags: list[str]
) -> bool:
    if item_id is not None:
        value = item.get("id")
        return isinstance(value, str) and value.casefold() == item_id.casefold()

    if find_text is not None:
        needle = find_text.casefold()
        fields = [
            item.get("id"),
            item.get("text"),
            *(item.get("tags") or []),
            *(item.get("evidence") or []),
        ]
        if not any(needle in str(value).casefold() for value in fields):
            return False

    item_tags = {str(tag).casefold() for tag in item.get("tags") or []}
    return all(tag.casefold() in item_tags for tag in tags)


def _emit_memory_item_not_found(item_id: str, layer: str | None, config: ClientConfig) -> int:
    label = "Fact" if layer == "fact" else "Assumption"
    message = f"{label} {item_id!r} was not found"
    if config.json_output:
        emit_json({"ok": False, "error": {"type": "not_found", "message": message}})
    else:
        print(message, file=sys.stderr)
    return NOT_FOUND


def _emit_memory_no_matches(
    layer: str | None, payload: dict[str, Any], config: ClientConfig
) -> int:
    label = "Fact" if layer == "fact" else "Assumption"
    if config.json_output:
        emit_json(payload)
    else:
        print(f"No matching {label} items.")
    return OK


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


def _handle_core_error(
    error: CoreError | CoreConnectionError, config: ClientConfig, client: MemoryCoreClient
) -> int:
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
        if isinstance(error, CoreError) and (
            error.status_code == 409 or error.code == "revision_conflict"
        ):
            with suppress(CoreError, CoreConnectionError, ValueError):
                payload["current_memory"] = client.read_memory(require_challenge(config))
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
        try:
            board = client.read_memory(require_challenge(config))
        except (CoreError, CoreConnectionError, ValueError):
            return code
        print("\nCurrent whiteboard:", file=sys.stderr)
        render_memory(board)
    else:
        render_error(error)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
