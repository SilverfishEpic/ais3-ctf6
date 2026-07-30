from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from agent_bridge.completion import sha256_token
from agent_bridge.memory.fake import FakeMemoryClient, demo_snapshot
from human_client import agent_cli, cli
from human_client.agent_cli import _expected_flag_sha256
from human_client.errors import OK, USAGE_ERROR

FLAG = "SEKAI{n0t_a_r34l_flag_but_shaped_l1ke_one}"


class FakeHttpMemoryClient(FakeMemoryClient):
    instances: list[FakeHttpMemoryClient] = []

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        super().__init__(demo_snapshot("demo-web"))
        self.base_url = base_url
        self.timeout = timeout
        self._challenges["demo-web"] = demo_snapshot("demo-web")
        self.instances.append(self)


@pytest.fixture(autouse=True)
def patch_agent_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeHttpMemoryClient.instances = []
    monkeypatch.setattr(agent_cli, "HttpMemoryClient", FakeHttpMemoryClient)


def test_agent_run_fake_agent_ingests_agent_run_log(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--fake-agent",
            "multi_tool",
            "--runtime-dir",
            str(tmp_path),
            "繼續分析",
        ]
    )
    captured = capsys.readouterr()

    assert code == OK
    assert "Agent run completed." in captured.out
    memory = FakeHttpMemoryClient.instances[0]
    assert len(memory.ingested) == 1
    envelope = memory.ingested[0]
    assert envelope["source"]["type"] == "agent"
    assert envelope["source"]["provider"] == "fake"
    assert envelope["input"]["type"] == "run_log"


def test_agent_run_codex_allows_memory_skill_by_default(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_agent_adapter(**kwargs: Any) -> object:
        assert kwargs["provider"] == "codex"
        assert kwargs["no_memory_skill"] is False
        raise ValueError("adapter factory reached")

    monkeypatch.setattr(agent_cli, "build_agent_adapter", fake_build_agent_adapter)

    code = cli.main(
        [
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--provider",
            "codex",
            "--runtime-dir",
            str(tmp_path),
            "繼續分析",
        ]
    )
    captured = capsys.readouterr()

    assert code != OK
    assert "adapter factory reached" in captured.err


def test_slash_agent_run_accepts_codex_provider(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_agent_adapter(**kwargs: Any) -> object:
        assert kwargs["provider"] == "codex"
        raise ValueError("slash adapter factory reached")

    monkeypatch.setattr(agent_cli, "build_agent_adapter", fake_build_agent_adapter)

    code = cli.main(
        [
            "--challenge",
            "demo-web",
            "/agent",
            "run",
            "--provider",
            "codex",
            "--workspace",
            str(tmp_path),
            "繼續分析",
        ]
    )
    captured = capsys.readouterr()

    assert code != OK
    assert "slash adapter factory reached" in captured.err


def test_agent_run_json_output_is_machine_safe(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--fake-agent",
            "single_tool",
            "--runtime-dir",
            str(tmp_path),
            "查一下",
        ]
    )
    captured = capsys.readouterr()

    assert code == OK
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["mode"] == "normal"
    assert payload["agent"]["tool_calls"] >= 1
    assert payload["memory"]["source_id"].startswith("I-fake")


def test_agent_stop_and_status_use_challenge_local_runtime(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stop_code = cli.main(
        [
            "--challenge",
            "demo-web",
            "agent",
            "stop",
            "--runtime-dir",
            str(tmp_path),
        ]
    )
    stop_out = capsys.readouterr().out

    assert stop_code == OK
    assert "demo-web" in stop_out
    assert (tmp_path / "demo-web" / "agent.stop").exists()

    status_code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "status",
            "--runtime-dir",
            str(tmp_path),
        ]
    )
    status_out = capsys.readouterr().out
    payload: dict[str, Any] = json.loads(status_out)

    assert status_code == OK
    assert payload["ok"] is True
    assert payload["challenge"] == "demo-web"
    assert payload["status"] == "not_started"
    assert Path(payload["stop_file"]).parts[-2:] == ("demo-web", "agent.stop")


def test_agent_status_reports_completed_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "--challenge",
                "demo-web",
                "agent",
                "run",
                "--fake-agent",
                "talk_only",
                "--runtime-dir",
                str(tmp_path),
                "說明目前狀態",
            ]
        )
        == OK
    )
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--challenge",
                "demo-web",
                "agent",
                "status",
                "--runtime-dir",
                str(tmp_path),
            ]
        )
        == OK
    )
    out = capsys.readouterr().out

    assert "Agent: completed" in out
    assert "Turns: 1" in out


def test_agent_accepts_human_readable_memory_policy_alias(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--fake-agent",
            "talk_only",
            "--memory-policy",
            "facts-assumptions",
            "--runtime-dir",
            str(tmp_path),
            "說明目前狀態",
        ]
    )
    captured = capsys.readouterr()

    assert code == OK
    assert json.loads(captured.out)["ok"] is True


# ---- 完成訊號（`--flagstop`）：預設關閉 --------------------------------
#
# `--workspace` 一律指到 tmp_path 底下：t=0 baseline 是真的會去讀磁碟的，
# 指到 repo 根目錄會掃進整個工作樹。


def test_yolo_without_flag_options_never_arms_the_brake(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """預設關閉：沒有明確要求的人，行為與這個煞車存在之前完全相同。"""
    (tmp_path / "ws").mkdir()
    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--mode",
            "yolo",
            "--fake-agent",
            "multi_tool",
            "--workspace",
            str(tmp_path / "ws"),
            "--runtime-dir",
            str(tmp_path / "rt"),
            "繼續分析",
        ]
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)

    assert code == OK
    assert payload["flagstop"] == {"armed": False, "error": None}
    assert payload["stop_class"] == "idle"
    assert payload["solved"] is False


def test_flag_prefix_arms_the_brake_and_reports_the_t0_baseline(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """開跑前的掃描要看得見 —— 12/20 題的真 flag 就躺在題目目錄裡。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "flag.txt").write_text(FLAG, encoding="utf-8")

    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--mode",
            "yolo",
            "--fake-agent",
            "multi_tool",
            "--flag-prefix",
            "SEKAI",
            "--workspace",
            str(workspace),
            "--runtime-dir",
            str(tmp_path / "rt"),
            "繼續分析",
        ]
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)

    assert code == OK
    flagstop = payload["flagstop"]
    assert flagstop["armed"] is True
    assert flagstop["ground_truth"] is False
    assert "SEKAI" in flagstop["pattern"]
    assert (flagstop["baseline_tokens"], flagstop["baseline_files"]) == (1, 1)
    assert flagstop["baseline_complete"] is True
    # multi_tool 的紀錄裡沒有 flag 形狀的字串，所以照樣跑到 idle 停損
    assert payload["stop_class"] == "idle"
    assert payload["solved"] is False


def test_flagstop_off_wins_over_every_other_flag_option(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """消融實驗的對照組：兩隻手臂只差這一個 token。"""
    (tmp_path / "ws").mkdir()
    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--mode",
            "yolo",
            "--fake-agent",
            "multi_tool",
            "--flagstop",
            "--flag-prefix",
            "SEKAI",
            "--flagstop-off",
            "--workspace",
            str(tmp_path / "ws"),
            "--runtime-dir",
            str(tmp_path / "rt"),
            "繼續分析",
        ]
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)

    assert code == OK
    assert payload["flagstop"]["armed"] is False


def test_normal_mode_never_builds_a_brake(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一輪就回來，沒有第二圈可以省，t=0 掃描會是白付的錢。"""
    (tmp_path / "ws").mkdir()
    code = cli.main(
        [
            "--json",
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--flagstop",
            "--fake-agent",
            "talk_only",
            "--workspace",
            str(tmp_path / "ws"),
            "--runtime-dir",
            str(tmp_path / "rt"),
            "說明目前狀態",
        ]
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)

    assert code == OK
    assert payload["mode"] == "normal"
    assert "flagstop" not in payload
    status = json.loads(
        (tmp_path / "rt" / "demo-web" / "agent-status.json").read_text(encoding="utf-8")
    )
    assert status["stop_class"] is None


def test_expect_flag_file_refuses_a_placeholder(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """拿 placeholder 當 ground truth 會讓整份 eval 的分數是假的。"""
    (tmp_path / "ws").mkdir()
    expected = tmp_path / "flag.txt"
    expected.write_text("SEKAI{}\n", encoding="utf-8")

    code = cli.main(
        [
            "--challenge",
            "demo-web",
            "agent",
            "run",
            "--mode",
            "yolo",
            "--fake-agent",
            "multi_tool",
            "--expect-flag-file",
            str(expected),
            "--workspace",
            str(tmp_path / "ws"),
            "--runtime-dir",
            str(tmp_path / "rt"),
            "繼續分析",
        ]
    )
    captured = capsys.readouterr()

    assert code == USAGE_ERROR
    assert "placeholder" in captured.err


def test_the_answer_files_own_directory_is_always_scanned(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """workspace 不是 agent 摸得到的範圍：eval 的答案就放在 workspace 的兄弟目錄。

    只掃 workspace 的話，第 1 圈 `cat ../solution/README.md` 會 hash 命中、不在
    抑制名單上，於是報成 `solved` —— `LEAKED` 這個分類唯一存在的理由就是抓這件事，
    卻剛好在該抓的時候永遠不會觸發。
    """
    workspace = tmp_path / "challenge"
    workspace.mkdir()
    solution = tmp_path / "solution"
    solution.mkdir()
    (solution / "flag.txt").write_text(FLAG + "\n", encoding="utf-8")
    (solution / "README.md").write_text(f"答案是 {FLAG}", encoding="utf-8")

    code = cli.main(
        [
            "--json", "--challenge", "demo-web", "agent", "run",
            "--mode", "yolo", "--fake-agent", "multi_tool",
            "--expect-flag-file", str(solution / "flag.txt"),
            "--workspace", str(workspace),
            "--runtime-dir", str(tmp_path / "rt"),
            "繼續分析",
        ]
    )
    flagstop: dict[str, Any] = json.loads(capsys.readouterr().out)["flagstop"]

    assert code == OK
    assert flagstop["ground_truth"] is True
    # workspace 自己是空的；抑制名單來自那個兄弟目錄
    assert (flagstop["baseline_tokens"], flagstop["baseline_files"]) == (1, 2)


def test_expect_flag_file_accepts_an_answer_that_merely_looks_odd(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """偵測端對有 sha256 的 run 會整層跳過形狀猜測，答案檔的閘門就不能比它嚴。"""
    (tmp_path / "ws").mkdir()
    expected = tmp_path / "flag.txt"
    expected.write_text("SEKAI{hello world}\n", encoding="utf-8")

    code = cli.main(
        [
            "--json", "--challenge", "demo-web", "agent", "run",
            "--mode", "yolo", "--fake-agent", "multi_tool",
            "--expect-flag-file", str(expected),
            "--workspace", str(tmp_path / "ws"),
            "--runtime-dir", str(tmp_path / "rt"),
            "繼續分析",
        ]
    )

    assert code == OK
    assert json.loads(capsys.readouterr().out)["flagstop"]["ground_truth"] is True


def test_a_byte_order_mark_does_not_silently_hash_the_wrong_answer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """靜靜地雜湊到別的字串，會讓 run 永遠停不下來又記成未解出 —— eval 最壞的形狀。"""
    (tmp_path / "ws").mkdir()
    expected = tmp_path / "flag.txt"
    expected.write_bytes(b"\xef\xbb\xbf" + FLAG.encode() + b"\n")

    code = cli.main(
        [
            "--json", "--challenge", "demo-web", "agent", "run",
            "--mode", "yolo", "--fake-agent", "multi_tool",
            "--expect-flag-file", str(expected),
            "--workspace", str(tmp_path / "ws"),
            "--runtime-dir", str(tmp_path / "rt"),
            "繼續分析",
        ]
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)

    assert code == OK
    assert payload["flagstop"]["ground_truth"] is True
    assert _expected_flag_sha256(str(expected)) == sha256_token(FLAG)


def test_a_non_utf8_answer_file_is_refused_rather_than_mangled(tmp_path: Path) -> None:
    expected = tmp_path / "flag.txt"
    expected.write_bytes("SEKAI{café_r3v3rs3d}".encode("latin-1"))

    with pytest.raises(ValueError, match="not valid UTF-8"):
        _expected_flag_sha256(str(expected))


@pytest.mark.parametrize(
    ("option", "value", "message"),
    [
        ("--flag-regex", "SEKAI{[", "正規表示式"),
        ("--expect-flag-file", "does-not-exist.txt", "cannot be read"),
    ],
)
def test_a_bad_flag_option_is_a_usage_error_before_any_state_is_written(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    value: str,
    message: str,
) -> None:
    """驗參數要排在 `write_status` 之前，而且要用 usage 的離開碼，不是 agent_error。"""
    (tmp_path / "ws").mkdir()
    runtime = tmp_path / "rt"

    code = cli.main(
        [
            "--challenge", "demo-web", "agent", "run",
            "--mode", "yolo", "--fake-agent", "multi_tool",
            option, value,
            "--workspace", str(tmp_path / "ws"),
            "--runtime-dir", str(runtime),
            "繼續分析",
        ]
    )

    assert code == USAGE_ERROR
    assert message in capsys.readouterr().err
    # 沒有任何 "running" 的殘骸留在磁碟上
    assert not (runtime / "demo-web" / "agent-status.json").exists()


def test_agent_status_json_keeps_runtime_fields_after_completed_run(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        cli.main(
            [
                "--challenge",
                "demo-web",
                "agent",
                "run",
                "--fake-agent",
                "talk_only",
                "--runtime-dir",
                str(tmp_path),
                "說明目前狀態",
            ]
        )
        == OK
    )
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--json",
                "--challenge",
                "demo-web",
                "agent",
                "status",
                "--runtime-dir",
                str(tmp_path),
            ]
        )
        == OK
    )
    payload: dict[str, Any] = json.loads(capsys.readouterr().out)

    assert payload["status"] == "completed"
    assert payload["runtime_dir"].endswith("demo-web")
    assert Path(payload["stop_file"]).parts[-2:] == ("demo-web", "agent.stop")
