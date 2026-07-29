from __future__ import annotations

from pathlib import Path

from human_client import cli
from human_client.config import (
    ClientConfig,
    SavedClientConfig,
    config_from_args,
    save_client_config,
)
from human_client.web.__main__ import _agent_settings
from human_client.web.__main__ import build_parser as build_web_parser


def test_client_config_does_not_use_non_secret_environment(
    tmp_path: Path, monkeypatch: object
) -> None:
    path = tmp_path / "client.json"
    save_client_config(
        path,
        SavedClientConfig(
            api_url="http://saved.invalid",
            challenge="saved-challenge",
            actor="saved-operator",
            timeout=12,
        ),
    )
    monkeypatch.setenv("CTFMEM_API_URL", "http://environment.invalid")  # type: ignore[union-attr]
    monkeypatch.setenv("CTFMEM_CHALLENGE", "environment-challenge")  # type: ignore[union-attr]

    config = config_from_args(type("Args", (), {"config": path})())

    assert config == ClientConfig(
        "http://saved.invalid", "saved-challenge", "saved-operator", 12, False, False, True
    )


def test_explicit_arguments_override_saved_client_config(tmp_path: Path) -> None:
    path = tmp_path / "client.json"
    save_client_config(path, SavedClientConfig(challenge="saved-challenge"))

    config = config_from_args(
        type("Args", (), {"config": path, "challenge": "explicit-challenge"})()
    )

    assert config.challenge == "explicit-challenge"


def test_config_set_writes_non_secret_project_defaults(
    tmp_path: Path, monkeypatch: object, capsys: object
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[union-attr]

    code = cli.main(
        [
            "config",
            "set",
            "--api-url",
            "http://127.0.0.1:8000",
            "--challenge",
            "web-01",
            "--actor",
            "hacker",
        ]
    )

    assert code == 0
    assert "saved non-secret defaults" in capsys.readouterr().out  # type: ignore[union-attr]
    saved = (tmp_path / ".ctfmem/client-config.json").read_text(encoding="utf-8")
    assert '"challenge": "web-01"' in saved
    assert "API_KEY" not in saved


def test_setup_writes_defaults_without_prompting(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[union-attr]

    code = cli.main(
        [
            "setup",
            "--no-input",
            "--api-url",
            "http://127.0.0.1:8000",
            "--challenge",
            "web-01",
            "--actor",
            "hacker",
        ]
    )

    assert code == 0
    config = config_from_args(type("Args", (), {})())
    assert config.challenge == "web-01"
    assert config.actor == "hacker"


def test_agent_init_creates_and_saves_workspace_for_the_web_launcher(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[union-attr]
    workspace = tmp_path / "agent-work"

    assert cli.main(["/agent", "init", str(workspace)]) == 0
    assert workspace.is_dir()

    args = build_web_parser().parse_args([])
    settings = _agent_settings(args)

    assert settings is not None
    assert settings.workspace == workspace.resolve()


def test_explicit_web_workspace_overrides_saved_workspace(tmp_path: Path) -> None:
    saved = tmp_path / "saved"
    explicit = tmp_path / "explicit"
    saved.mkdir()
    explicit.mkdir()
    config_path = tmp_path / "client.json"
    save_client_config(config_path, SavedClientConfig(agent_workspace=str(saved)))

    args = build_web_parser().parse_args(
        ["--config", str(config_path), "--agent-workspace", str(explicit)]
    )
    settings = _agent_settings(args)

    assert settings is not None
    assert settings.workspace == explicit.resolve()
