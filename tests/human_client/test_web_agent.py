from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge.runtime import AgentStatus, stop_file, write_status
from human_client.cli import build_parser
from human_client.config import ClientConfig
from human_client.web import agent as web_agent
from human_client.web.agent import AgentSettings
from human_client.web.app import create_web_app
from human_client.web.catalog import Catalog

CORE = "http://127.0.0.1:8777"
PORT = 8787
ORIGIN = f"http://127.0.0.1:{PORT}"


def call(app: FastAPI, method: str, url: str, **kwargs: Any) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(send())


@pytest.fixture
def settings(tmp_path: Path) -> AgentSettings:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return AgentSettings(
        workspace=workspace,
        runtime_dir=tmp_path / "runtime",
        max_turns=6,
        max_cost_usd=2.5,
    )


def argv_for(settings: AgentSettings, body: dict[str, Any]) -> list[str]:
    return web_agent.build_argv(
        settings, body, api_url=CORE, actor="operator", challenge_id="demo-web"
    )


def parsed(argv: list[str]) -> Any:
    """Run the argument list through the CLI's own parser.

    This is the anti-drift check. If `ctfmem agent run` renames a flag, changes a choice or
    adds a required argument, the list this module builds stops parsing and this test fails
    rather than a child process dying into a log file nobody is watching.
    """

    return build_parser().parse_args(argv[3:])


def test_the_entry_point_this_module_launches_actually_runs(tmp_path: Path) -> None:
    """Spawn the real entry point the way ``default_spawn`` does, and make it do something.

    Parsing the arguments is not enough. ``python -m human_client.cli`` was silently a
    no-op until the module grew a ``__main__`` guard: it imported, defined everything, and
    exited 0 without reading argv. Every run started from the page succeeded and did
    nothing. This asserts the child is runnable, using the one agent subcommand that needs
    no network and no model.
    """

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "human_client.cli",
            "--challenge",
            "demo-web",
            "--json",
            "agent",
            "status",
            "--runtime-dir",
            str(tmp_path),
        ],
        cwd=web_agent.PACKAGE_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["status"] == "not_started"


def test_the_argument_list_parses_with_the_cli_own_parser(settings: AgentSettings) -> None:
    argv = argv_for(settings, {"prompt": "look around", "mode": "yolo", "session": "new"})

    args = parsed(argv)

    assert argv[1:3] == ["-m", "human_client.cli"]
    assert (args.command, args.agent_command) == ("agent", "run")
    assert args.prompt == "look around"
    assert args.mode == "yolo"
    assert args.session == "new"
    assert args.challenge == "demo-web"
    assert args.actor == "operator"
    assert args.api_url == CORE
    assert args.json is True


def test_limits_clamp_downward_and_never_up(settings: AgentSettings) -> None:
    args = parsed(argv_for(settings, {"prompt": "go", "max_turns": 999, "max_cost_usd": 100}))

    assert args.max_turns == 6
    assert args.max_cost_usd == 2.5


def test_a_smaller_limit_from_the_page_is_respected(settings: AgentSettings) -> None:
    args = parsed(argv_for(settings, {"prompt": "go", "max_turns": 2, "max_cost_usd": 0.5}))

    assert args.max_turns == 2
    assert args.max_cost_usd == 0.5


def test_omitted_limits_fall_back_to_the_server_ceiling(settings: AgentSettings) -> None:
    args = parsed(argv_for(settings, {"prompt": "go"}))

    assert args.max_turns == 6
    assert args.max_cost_usd == 2.5


def test_the_workspace_comes_from_the_server_not_the_body(settings: AgentSettings) -> None:
    args = parsed(argv_for(settings, {"prompt": "go", "workspace": "/etc"}))

    assert args.workspace == str(settings.workspace)
    assert "/etc" not in " ".join(argv_for(settings, {"prompt": "go", "workspace": "/etc"}))


def test_the_runtime_directory_is_the_one_the_cli_would_read(settings: AgentSettings) -> None:
    args = parsed(argv_for(settings, {"prompt": "go"}))

    assert args.runtime_dir == str(settings.runtime_dir)


def test_a_missing_prompt_is_refused(settings: AgentSettings) -> None:
    with pytest.raises(ValueError, match="prompt is required"):
        argv_for(settings, {"prompt": "   "})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"prompt": "go", "mode": "turbo"}, "mode must be one of"),
        ({"prompt": "go", "session": "resume"}, "session must be one of"),
        ({"prompt": "go", "memory_policy": "everything"}, "policy must be one of"),
        ({"prompt": "go", "fake_agent": "not-a-scenario"}, "fake_agent must be one of"),
        ({"prompt": "go", "max_turns": "many"}, "max_turns must be a whole number"),
        ({"prompt": "go", "max_turns": 0}, "max_turns must be at least 1"),
        ({"prompt": "go", "max_cost_usd": 0}, "max_cost_usd must be greater than 0"),
    ],
)
def test_a_choice_the_cli_would_reject_is_refused_here(
    settings: AgentSettings, body: dict[str, Any], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        argv_for(settings, body)


def test_a_recorded_scenario_is_passed_through(settings: AgentSettings) -> None:
    args = parsed(argv_for(settings, {"prompt": "go", "fake_agent": "multi_tool"}))

    assert args.fake_agent == "multi_tool"


def test_a_real_run_carries_no_fake_agent_flag(settings: AgentSettings) -> None:
    args = parsed(argv_for(settings, {"prompt": "go"}))

    assert args.fake_agent is None


def test_start_reports_what_it_launched_and_truncates_the_log(settings: AgentSettings) -> None:
    launched: list[tuple[list[str], Path]] = []

    def spawn(argv: list[str], path: Path) -> int:
        launched.append((argv, path))
        return 4321

    path = web_agent.log_path(settings, "demo-web")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("output from a previous run\n", encoding="utf-8")

    result = web_agent.start(
        settings,
        {"prompt": "go"},
        api_url=CORE,
        actor="operator",
        challenge_id="demo-web",
        spawn=spawn,
    )

    assert result["pid"] == 4321
    assert result["log"] == str(path)
    assert result["argv"] == launched[0][0]
    assert path.read_text(encoding="utf-8") == ""


def test_a_refused_body_never_reaches_the_spawn(settings: AgentSettings) -> None:
    def spawn(argv: list[str], path: Path) -> int:
        raise AssertionError("the child must not start when the request is invalid")

    with pytest.raises(ValueError):
        web_agent.start(
            settings,
            {"prompt": ""},
            api_url=CORE,
            actor="operator",
            challenge_id="demo-web",
            spawn=spawn,
        )


def test_status_is_not_started_before_anything_runs(settings: AgentSettings) -> None:
    payload = web_agent.status(settings, "demo-web")

    assert payload["status"] == "not_started"
    assert payload["running"] is False
    assert payload["log"] == ""


def test_status_reports_a_finished_run_and_its_output(settings: AgentSettings) -> None:
    write_status(
        AgentStatus(
            challenge="demo-web",
            mode="yolo",
            session="same",
            status="completed",
            started_at="2026-07-27T00:00:00+00:00",
            updated_at="2026-07-27T00:04:00+00:00",
            pid=os.getpid(),
            turns=4,
            productive_turns=2,
            last_revision=17,
            stopped_because="idle",
            total_cost_usd=0.42,
        ),
        settings.runtime_dir,
    )
    web_agent.log_path(settings, "demo-web").write_text('{"ok": true}\n', encoding="utf-8")

    payload = web_agent.status(settings, "demo-web")

    assert payload["status"] == "completed"
    assert payload["turns"] == 4
    assert payload["productive_turns"] == 2
    assert payload["total_cost_usd"] == 0.42
    assert payload["log"].strip() == '{"ok": true}'
    assert payload["stop_file"] == str(stop_file("demo-web", settings.runtime_dir))


def test_a_finished_run_is_not_running_even_while_its_pid_resolves(
    settings: AgentSettings,
) -> None:
    """The zombie case. A child this server started is nobody's to wait on until it is
    reaped, and a zombie answers ``os.kill(pid, 0)`` like a live process. Asking the pid
    alone reported every completed run as still running, which left the run button disabled
    for the rest of the session."""

    write_status(
        AgentStatus(
            challenge="demo-web",
            mode="normal",
            session="same",
            status="completed",
            started_at="2026-07-27T00:00:00+00:00",
            updated_at="2026-07-27T00:01:00+00:00",
            pid=os.getpid(),
            stopped_because="completed",
        ),
        settings.runtime_dir,
    )

    payload = web_agent.status(settings, "demo-web")

    assert payload["status"] == "completed"
    assert payload["running"] is False
    assert web_agent.is_running(settings, "demo-web") is False


def test_a_run_whose_process_died_is_reported_as_stale(settings: AgentSettings) -> None:
    write_status(
        AgentStatus(
            challenge="demo-web",
            mode="normal",
            session="same",
            status="running",
            started_at="2026-07-27T00:00:00+00:00",
            updated_at="2026-07-27T00:00:00+00:00",
            pid=4_000_000,
        ),
        settings.runtime_dir,
    )

    payload = web_agent.status(settings, "demo-web")

    assert payload["status"] == "stale"
    assert payload["running"] is False
    assert web_agent.is_running(settings, "demo-web") is False


def test_a_live_run_blocks_a_second_one(settings: AgentSettings) -> None:
    write_status(
        AgentStatus(
            challenge="demo-web",
            mode="yolo",
            session="same",
            status="running",
            started_at="2026-07-27T00:00:00+00:00",
            updated_at="2026-07-27T00:00:00+00:00",
            pid=os.getpid(),
        ),
        settings.runtime_dir,
    )

    assert web_agent.is_running(settings, "demo-web") is True


def test_stop_writes_the_file_the_cli_and_the_loop_both_watch(settings: AgentSettings) -> None:
    result = web_agent.stop(settings, "demo-web")
    path = stop_file("demo-web", settings.runtime_dir)

    assert result["stop_file"] == str(path)
    assert path.exists()


def test_options_are_off_until_a_workspace_is_configured() -> None:
    payload = web_agent.options(None)

    assert payload["enabled"] is False
    assert "--agent-workspace" in payload["reason"]


def test_options_advertise_the_ceiling_and_the_recorded_scenarios(
    settings: AgentSettings,
) -> None:
    payload = web_agent.options(settings)

    assert payload["enabled"] is True
    assert payload["max_turns"] == 6
    assert payload["modes"] == ["normal", "yolo"]
    assert "multi_tool" in payload["scenarios"]


@pytest.fixture
def agent_app(settings: AgentSettings) -> tuple[FastAPI, list[list[str]]]:
    """The app with the agent enabled and the spawn replaced by a recorder."""

    launched: list[list[str]] = []

    def spawn(argv: list[str], path: Path) -> int:
        launched.append(argv)
        return 4321

    app = create_web_app(
        client=object(),  # type: ignore[arg-type]
        summarizing_client=object(),  # type: ignore[arg-type]
        config=ClientConfig(
            api_url=CORE,
            challenge=None,
            actor="operator",
            timeout=30.0,
            json_output=False,
            verbose=False,
            color=False,
        ),
        catalog=Catalog(directory="/tmp/templates", templates=[], problems=[]),
        port=PORT,
        agent=settings,
        spawn=spawn,
    )
    return app, launched


def test_a_run_started_from_the_page_launches_the_cli(
    agent_app: tuple[FastAPI, list[list[str]]],
) -> None:
    app, launched = agent_app

    response = call(app, "POST", "/api/local/agent/demo-web/run", json={"prompt": "look around"})

    assert response.status_code == 200
    assert response.json()["pid"] == 4321
    assert launched[0][1:3] == ["-m", "human_client.cli"]
    assert "look around" in launched[0]


def test_a_bad_request_is_refused_before_anything_is_launched(
    agent_app: tuple[FastAPI, list[list[str]]],
) -> None:
    app, launched = agent_app

    response = call(app, "POST", "/api/local/agent/demo-web/run", json={"prompt": ""})

    assert response.status_code == 400
    assert response.json()["webui_error"]["code"] == "invalid_agent_request"
    assert launched == []


def test_a_challenge_id_that_is_a_path_never_starts_a_run(
    agent_app: tuple[FastAPI, list[list[str]]],
) -> None:
    app, launched = agent_app

    response = call(app, "POST", "/api/local/agent/%2E%2E/run", json={"prompt": "go"})

    assert response.status_code == 400
    assert response.json()["webui_error"]["code"] == "invalid_challenge_id"
    assert launched == []


def test_a_second_run_is_refused_while_one_is_live(
    agent_app: tuple[FastAPI, list[list[str]]], settings: AgentSettings
) -> None:
    app, launched = agent_app
    write_status(
        AgentStatus(
            challenge="demo-web",
            mode="yolo",
            session="same",
            status="running",
            started_at="2026-07-27T00:00:00+00:00",
            updated_at="2026-07-27T00:00:00+00:00",
            pid=os.getpid(),
        ),
        settings.runtime_dir,
    )

    response = call(app, "POST", "/api/local/agent/demo-web/run", json={"prompt": "go"})

    assert response.status_code == 409
    assert response.json()["webui_error"]["code"] == "agent_already_running"
    assert launched == []


def test_stop_from_the_page_writes_the_file_the_cli_watches(
    agent_app: tuple[FastAPI, list[list[str]]], settings: AgentSettings
) -> None:
    app, _ = agent_app

    response = call(app, "POST", "/api/local/agent/demo-web/stop")

    assert response.status_code == 200
    assert stop_file("demo-web", settings.runtime_dir).exists()


def test_status_from_the_page_reads_the_same_file_the_cli_writes(
    agent_app: tuple[FastAPI, list[list[str]]],
) -> None:
    app, _ = agent_app

    payload = call(app, "GET", "/api/local/agent/demo-web/status").json()

    assert payload["status"] == "not_started"
