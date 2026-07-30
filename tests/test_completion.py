"""完成訊號的判斷本身：什麼算目擊、什麼一定不算（`agent_bridge.completion`）。

這一檔只測純判斷 —— 不起 loop、不碰 CLI、不需要網路。

分這麼多條是因為誤報的代價不對稱：漏掉一次目擊只是多跑一圈；
誤判一次卻會在題目還沒解完的時候，把一次真金白銀的執行結束掉。
所以「不該停」的每一條都有自己的回歸測試，對應設計文件的四層防線：

1. **頻道隔離** —— 只讀本輪真的執行過的工具輸出，不讀白板、不讀 assistant 講的話
2. **t=0 baseline** —— 開跑前就在磁碟上的 token 不是這一輪發現的
3. **合理性過濾** —— placeholder、散文、格式字串、字元類一律不信
4. **可選的 ground truth** —— 有 sha256 才分得出「解出來」與「去讀答案」

第 1 層與第 3 層各自擋掉一個實測存在的誤報：白板上真的有一條
`SEKAI{''; Flag suffix: ''}`（summarizer 把一句描述 flag *格式* 的話收成了條目），
而 `test-challenge-set` 有 12/20 題的真 flag 就放在 `challenge/` 底下。

完整設計：`docs/experiments/2026-07-30-memory-ablation/flag-stop-design.md`。
這一檔裡所有 flag 字串都是編出來的，沒有一個是真題目的答案。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_bridge.completion import (
    GENERIC_FLAG_RE,
    SYNTHETIC_MODEL,
    Baseline,
    BaselineLimits,
    CompletionDetector,
    FlagStopConfig,
    StopClass,
    build_flag_pattern,
    implausible,
    is_evidence,
    is_provider_failure,
    mask_flag,
    placeholder_reason,
    scan_events,
    scan_roots,
    scan_workspace,
    sha256_token,
)
from agent_bridge.normalize import AgentRunEvent, NormalizedRun

FLAG = "SEKAI{n0t_a_r34l_flag_but_shaped_l1ke_one}"
OTHER_FLAG = "SEKAI{a_d1fferent_fake_flag}"

#: summarizer 從一句描述 flag *格式* 的話產生的垃圾條目，這個 repo 的白板上真的有一條。
#: 任何讀白板、或不做合理性過濾的 oracle，都會在這裡誤觸。
SUMMARIZER_JUNK = "SEKAI{''; Flag suffix: ''}"

SEKAI_ONLY = build_flag_pattern(["SEKAI"])
PROSE = "body contains whitespace, quoting or code punctuation"


def tool_out(content: str, *, name: str = "Bash", outcome: str = "succeeded") -> AgentRunEvent:
    return AgentRunEvent(
        seq=1, role="tool", kind="tool_result", name=name, content=content, outcome=outcome
    )


def said(content: str) -> AgentRunEvent:
    return AgentRunEvent(seq=1, role="assistant", kind="message", content=content)


def turn(*events: AgentRunEvent, **meta: Any) -> NormalizedRun:
    return NormalizedRun(events=list(events), run_meta={"model": "claude-x", **meta})


def detector(**config: Any) -> CompletionDetector:
    baseline = config.pop("baseline", None)
    return CompletionDetector(FlagStopConfig(**config), baseline=baseline)


# ---- 第 3 層：合理性過濾 -----------------------------------------------


def test_summarizer_junk_is_rejected_as_prose() -> None:
    """白板上那條垃圾是散文，不是 flag —— 這是整個煞車最重要的一條。"""
    assert implausible(SUMMARIZER_JUNK) == PROSE


@pytest.mark.parametrize(
    "token",
    [
        SUMMARIZER_JUNK,
        "SEKAI{}",                 # 空 body
        "SEKAI{...}",              # 省略號
        "SEKAI{…}",
        "SEKAI{____}",             # 只有裝飾字元
        "SEKAI{test_flag}",        # 名單上的 placeholder
        "SEKAI{PLACEHOLDER}",      # 大小寫不影響
        "SEKAI{abc}",              # 太短
        "SEKAI{[a-z_]+}",          # README 在講字母表
        "SEKAI{%s}",               # 格式字串
        "SEKAI{value_%d_here}",
        "SEKAI{a\\x41b_c}",        # 跳脫序列
        "SEKAI{the flag is here}",  # 有空白就是句子
    ],
)
def test_implausible_bodies_are_rejected(token: str) -> None:
    assert implausible(token) is not None


@pytest.mark.parametrize(
    "token",
    [
        FLAG,
        "flag{th1s_1s_shaped_l1ke_a_flag}",
        "SEKAI{40_byte_ch4rt_r3c0v3r3d}",
        "CTF{a1b2c3d4}",
        "SEKAI{ThisOneHasNoUnderscores}",
    ],
)
def test_real_looking_flags_pass(token: str) -> None:
    assert implausible(token) is None


@pytest.mark.parametrize(
    "snippet",
    [
        "body{margin:0}",        # 壓縮過的 CSS
        "div{display:flex}",
        "sqrt{x+y+z}",           # LaTeX
        "frac{numerator}",
        "vector{1,2,3}",         # brace initializer
        "int{1,2,3}",
        "if{x==1}",
        "main{return0}",
        "sum_{i=0}",
        "Point{X:1,Y:2}",        # Go / Rust struct literal：prefix 有大寫，靠冒號擋
        "Vec3{1.0,2.0,3.0}",
    ],
)
def test_the_generic_shape_does_not_take_ordinary_code_for_a_flag(snippet: str) -> None:
    """泛用形狀（沒有 `--flag-prefix`）會被餵進每一次 `cat` 原始碼、每一次 `curl`。

    第 3 層是拿散文調出來的，不是拿程式碼；prefix 必須帶大寫或數字這條規則，
    加上冒號與逗號的過濾，才是擋住這些的結構性理由。
    """
    match = GENERIC_FLAG_RE.search(snippet)
    assert match is None or implausible(match.group(0)) is not None


@pytest.mark.parametrize(
    "token",
    ["SEKAI{aaaa_bbbb}", "Sekai{Mixed_Case}", "flag{aaaa_bbbb}", "ctf{aaaa_bbbb}",
     "HTB{aaaa_bbbb}", "picoCTF{aaaa_bbbb}", "CTF{a1b2c3d4}"],
)
def test_the_generic_shape_still_matches_flags_that_name_themselves(token: str) -> None:
    """大寫、數字，或字面上的 `flag` / `ctf` —— 這三種都還是要命中。"""
    match = GENERIC_FLAG_RE.search(f"printed: {token}")

    assert match is not None and match.group(0) == token
    assert implausible(token) is None


@pytest.mark.parametrize(
    "token",
    ["SEKAI{hello world}", "SEKAI{it's_me}", "SEKAI{a;b;c}", "SEKAI{a:b,c}", "SEKAI{[a-z]+}"],
)
def test_an_odd_looking_real_answer_is_not_a_placeholder(token: str) -> None:
    """`placeholder_reason` 是 ground truth 唯一可以套用的子集。

    偵測端對有 sha256 的 run 會整層跳過 `implausible`，所以答案檔的閘門也不能用它 ——
    用了就會在啟動時拒絕掉一個偵測端明明收得下的真答案。
    """
    assert implausible(token) is not None
    assert placeholder_reason(token) is None


@pytest.mark.parametrize(
    "token", ["SEKAI{}", "SEKAI{flag_here}", "FLAG{...}", "SEKAI{____}", "SEKAI{TODO}"]
)
def test_a_real_placeholder_is_still_refused_as_ground_truth(token: str) -> None:
    """拿模板當 ground truth 會讓整份 eval 的分數是假的。"""
    assert placeholder_reason(token) is not None


@pytest.mark.parametrize(
    "token", ["0123456789abcdef0123456789abcdef", "flag_abc123", "FLAG{a{b}}"]
)
def test_a_flag_format_without_a_plain_brace_tail_is_judgeable(token: str) -> None:
    """`--flag-regex` 可以合法描述不帶大括號的格式。

    舊版對這些一律回「no brace body」，等於讓那個選項成為一個永遠踩不下去的煞車：
    會武裝、會回報 `armed: true`、會把每一個真 flag 累積進 `rejected`，然後什麼都不做。
    """
    assert implausible(token) is None


def test_a_custom_regex_without_braces_can_actually_fire() -> None:
    config = FlagStopConfig(pattern=build_flag_pattern(None, r"[0-9a-f]{32}"))
    digest = "0123456789abcdef0123456789abcdef"

    scan = scan_events([tool_out(f"md5 = {digest}")], config=config)

    assert scan.sighting is not None and scan.sighting.token == digest


def test_build_flag_pattern_narrows_the_haystack_to_the_given_prefixes() -> None:
    """給得出 prefix 就給 —— 泛用形狀連 `sum_{i=0}` 都會撿起來再丟掉。"""
    pattern = build_flag_pattern(["SEKAI", "flag"])
    assert pattern.search(FLAG)
    assert pattern.search("flag{aaaa_bbbb}")
    assert pattern.search("OTHERCTF{aaaa_bbbb}") is None


def test_build_flag_pattern_lets_a_custom_regex_win() -> None:
    pattern = build_flag_pattern(["SEKAI"], r"ictf\[[a-z_]+\]")
    assert pattern.search("ictf[hello_there]")
    assert pattern.search(FLAG) is None


def test_build_flag_pattern_rejects_a_broken_regex() -> None:
    """壞掉的 regex 要在建構時就炸，不要等到第一圈跑完才發現煞車沒接上。"""
    with pytest.raises(ValueError, match="正規表示式"):
        build_flag_pattern(None, "SEKAI{[")


# ---- 第 1 層：頻道隔離 -------------------------------------------------


def test_a_new_flag_in_tool_output_is_a_sighting() -> None:
    scan = scan_events([tool_out(f"decrypted: {FLAG}")])

    assert scan.sighting is not None
    assert scan.sighting.token == FLAG
    assert scan.sighting.tool == "Bash"
    assert scan.sighting.stop_class is StopClass.SOLVED


def test_the_summarizer_junk_in_tool_output_is_still_not_a_sighting() -> None:
    """就算那條垃圾是被 `grep` 從白板檔案裡撈出來的，它也還是一句話。"""
    scan = scan_events([tool_out(f"grep -r flag: {SUMMARIZER_JUNK}")])

    assert scan.sighting is None
    assert [item.reason for item in scan.rejected] == [PROSE]


def test_assistant_prose_is_never_read() -> None:
    """Agent 說自己拿到 flag 不算證據 —— 它可以在沒跑任何東西的情況下這樣講。"""
    scan = scan_events([said(f"I solved it, the flag is {FLAG}")])
    assert scan.sighting is None
    assert scan.rejected == ()


@pytest.mark.parametrize(
    "tool",
    ["mcp__memory__memory_show", "mcp__memory__memory_query", "memory_guess"],
)
def test_memory_tool_output_is_never_read(tool: str) -> None:
    """記憶工具的 tool_result 內容**就是**白板，而白板正是我們拒絕閱讀的那個頻道。"""
    scan = scan_events([tool_out(SUMMARIZER_JUNK + " " + FLAG, name=tool)])
    assert scan.sighting is None


@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
def test_executed_tools_are_evidence(outcome: str) -> None:
    """跑了但 exit 非 0 照樣是觀察 —— 很多 solver 是 stderr 印出 flag 的。"""
    assert is_evidence(tool_out(FLAG, outcome=outcome))
    assert scan_events([tool_out(FLAG, outcome=outcome)]).sighting is not None


@pytest.mark.parametrize("outcome", ["denied", "interrupted", "unknown"])
def test_tools_that_did_not_run_are_not_evidence(outcome: str) -> None:
    """被沙箱擋下、被打斷、來歷不明 —— 都不能證明有人真的印出這個字串。"""
    assert not is_evidence(tool_out(FLAG, outcome=outcome))
    assert scan_events([tool_out(FLAG, outcome=outcome)]).sighting is None


def test_tool_call_arguments_are_not_evidence() -> None:
    """Agent 自己打的指令列裡有 flag 只代表它猜了一個，不代表跑出來是這個。"""
    echoed = AgentRunEvent(
        seq=1, role="assistant", kind="tool_call", name="Bash", content=f"echo {FLAG}"
    )
    assert scan_events([echoed]).sighting is None


def test_the_first_plausible_token_wins_and_the_rest_are_kept_for_review() -> None:
    """兩個候選同圈出現，正是人最需要兩個都看到的時候 —— 第二個不能安靜消失。"""
    scan = scan_events([tool_out(f"{SUMMARIZER_JUNK}\n{FLAG}\n{OTHER_FLAG}")])

    assert scan.sighting is not None and scan.sighting.token == FLAG
    assert [(item.token, item.reason) for item in scan.rejected] == [
        (SUMMARIZER_JUNK, PROSE),
        (OTHER_FLAG, "another candidate in the same turn"),
    ]


def test_the_same_token_is_only_judged_once() -> None:
    scan = scan_events([tool_out("\n".join([SUMMARIZER_JUNK] * 5))])
    assert len(scan.rejected) == 1


def test_a_custom_pattern_with_a_capture_group_yields_the_whole_token() -> None:
    """`--flag-regex` 可以帶捕捉群組；取 group(0) 以外的東西會回一段殘缺的字串。"""
    config = FlagStopConfig(pattern=build_flag_pattern(None, r"SEKAI\{(\w+)\}"))
    scan = scan_events([tool_out(FLAG)], config=config)

    assert scan.sighting is not None and scan.sighting.token == FLAG


# ---- 第 2 層：t=0 baseline ---------------------------------------------


def test_a_token_that_was_already_on_disk_is_not_a_discovery() -> None:
    """`test-challenge-set` 有 12/20 題的真 flag 就躺在題目目錄裡。"""
    scan = scan_events([tool_out(f"cat flag.txt -> {FLAG}")], baseline=frozenset({FLAG}))

    assert scan.sighting is None
    assert [item.reason for item in scan.rejected] == ["already in the workspace at t=0"]


def test_the_baseline_only_suppresses_the_exact_token() -> None:
    scan = scan_events([tool_out(OTHER_FLAG)], baseline=frozenset({FLAG}))
    assert scan.sighting is not None and scan.sighting.token == OTHER_FLAG


def test_baseline_scan_collects_tokens_that_are_already_on_disk(tmp_path: Path) -> None:
    (tmp_path / "challenge").mkdir()
    (tmp_path / "challenge" / "flag.txt").write_text(FLAG, encoding="utf-8")
    (tmp_path / "README.md").write_text(f"flag 長這樣：{SUMMARIZER_JUNK}", encoding="utf-8")

    baseline = scan_workspace(tmp_path)

    assert baseline.tokens == frozenset({FLAG, SUMMARIZER_JUNK})
    assert baseline.files == 2
    assert baseline.complete is True


def test_baseline_scan_skips_vcs_and_dependency_directories(tmp_path: Path) -> None:
    """`.git` 與 `node_modules` 沒有題目的答案，只有大量位元組。"""
    for skipped in (".git", "node_modules", ".venv", "__pycache__"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "objects").write_text(FLAG, encoding="utf-8")

    assert scan_workspace(tmp_path).tokens == frozenset()


@pytest.mark.parametrize("directory", ["runs", ".ctfmem"])
def test_the_board_and_the_run_log_are_scanned_not_skipped(
    tmp_path: Path, directory: str
) -> None:
    """第 1 層是靠工具名稱排除白板的，但 `cat runs/<id>/state.yaml` 是一筆 Bash 輸出。

    跳過這兩個目錄等於同時繞開第 1 層與第 2 層：上一輪從 README 折進白板的 flag、
    上一次 run 印進 `agent-run.log` 的答案，都會變成「這一輪的新發現」。
    """
    (tmp_path / directory).mkdir()
    (tmp_path / directory / "state.yaml").write_text(f"- {FLAG}", encoding="utf-8")

    assert scan_workspace(tmp_path).tokens == frozenset({FLAG})


def test_baseline_scan_sees_utf16_text_that_latin1_alone_would_miss(tmp_path: Path) -> None:
    """UTF-16 的答案檔 `cat` 得出來，卻打不中任何 pattern —— 那就是一次假的 SOLVED。"""
    (tmp_path / "flag.txt").write_bytes(FLAG.encode("utf-16-le"))

    assert scan_workspace(tmp_path).tokens == frozenset({FLAG})


def test_baseline_scan_covers_several_roots_with_one_budget(tmp_path: Path) -> None:
    """workspace 不等於 agent 摸得到的範圍：答案就放在 workspace 的兄弟目錄裡。"""
    (tmp_path / "challenge").mkdir()
    (tmp_path / "challenge" / "chall.bin").write_text("nothing here", encoding="utf-8")
    (tmp_path / "solution").mkdir()
    (tmp_path / "solution" / "README.md").write_text(FLAG, encoding="utf-8")

    assert scan_workspace(tmp_path / "challenge").tokens == frozenset()

    baseline = scan_roots([tmp_path / "challenge", tmp_path / "solution"])
    assert baseline.tokens == frozenset({FLAG})
    assert baseline.files == 2


def test_baseline_scan_folds_a_root_that_is_inside_another_one(tmp_path: Path) -> None:
    (tmp_path / "challenge").mkdir()
    (tmp_path / "challenge" / "flag.txt").write_text(FLAG, encoding="utf-8")

    assert scan_roots([tmp_path, tmp_path / "challenge"]).files == 1


def test_baseline_scan_admits_that_a_named_root_was_not_there(tmp_path: Path) -> None:
    assert scan_roots([tmp_path, tmp_path / "nope"]).complete is False


def test_baseline_scan_reads_binaries_like_strings(tmp_path: Path) -> None:
    """真 flag 常常是 `strings ./chall` 撈得到的，掃描不能只認 UTF-8 文字檔。"""
    (tmp_path / "chall").write_bytes(b"\x7fELF\x00\xff\xfe" + FLAG.encode() + b"\x00")

    assert scan_workspace(tmp_path).tokens == frozenset({FLAG})


def test_baseline_scan_uses_the_run_pattern(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text(f"{FLAG}\nOTHERCTF{{some_other_thing}}", encoding="utf-8")

    assert scan_workspace(tmp_path, SEKAI_ONLY).tokens == frozenset({FLAG})


def test_baseline_scan_admits_when_it_did_not_read_everything(tmp_path: Path) -> None:
    """撞到上限只會讓抑制名單變短，所以一定要說出來，不能安靜地放行。"""
    (tmp_path / "huge.bin").write_text(FLAG + "x" * 4096, encoding="utf-8")

    baseline = scan_workspace(tmp_path, limits=BaselineLimits(max_file_bytes=64))

    assert baseline.tokens == frozenset()
    assert baseline.complete is False


def test_baseline_scan_stops_at_the_total_budget(tmp_path: Path) -> None:
    for index in range(3):
        (tmp_path / f"f{index}.txt").write_text(FLAG, encoding="utf-8")

    baseline = scan_workspace(tmp_path, limits=BaselineLimits(max_total_bytes=8))

    assert baseline.complete is False


def test_baseline_scan_ignores_empty_files(tmp_path: Path) -> None:
    (tmp_path / "empty.txt").write_text("", encoding="utf-8")

    baseline = scan_workspace(tmp_path)

    assert baseline.files == 0
    assert baseline.complete is True


# ---- 第 4 層：ground truth ---------------------------------------------


def test_the_expected_flag_stops_the_run_as_solved() -> None:
    verdict = detector(expect_sha256=sha256_token(FLAG)).observe(turn(tool_out(FLAG)), turn=1)

    assert verdict is not None
    assert verdict.stop_class is StopClass.SOLVED
    assert verdict.verified is True
    assert verdict.flag == FLAG


def test_a_different_token_never_satisfies_the_expected_flag() -> None:
    """有 ground truth 時就只認 hash —— 形狀再像也不是那個答案。"""
    scan = scan_events(
        [tool_out(OTHER_FLAG)], config=FlagStopConfig(expect_sha256=sha256_token(FLAG))
    )

    assert scan.sighting is None
    assert [item.reason for item in scan.rejected] == ["not the expected flag"]


def test_the_expected_flag_read_off_disk_is_leaked_not_solved() -> None:
    """t=0 就在 workspace 裡 —— agent 是去 `cat` 答案的，不是解出來的。停，但不算解開。"""
    brake = detector(expect_sha256=sha256_token(FLAG), baseline=Baseline(tokens=frozenset({FLAG})))
    verdict = brake.observe(turn(tool_out(FLAG)), turn=1)

    assert verdict is not None
    assert verdict.stop_class is StopClass.LEAKED
    assert verdict.stop_class.counts_as_solved is False
    assert "read the answer" in verdict.reason


def test_ground_truth_overrides_the_plausibility_filter() -> None:
    """真答案本來就可能長得像 placeholder；比得出 hash 就不需要猜形狀。"""
    weird = "SEKAI{flag}"
    assert implausible(weird) is not None

    scan = scan_events([tool_out(weird)], config=FlagStopConfig(expect_sha256=sha256_token(weird)))
    assert scan.sighting is not None


def test_without_ground_truth_a_sighting_is_not_verified() -> None:
    """一次目擊證明的是「某個真的跑過的工具印出了這個字串」，不是這個 flag 是對的。"""
    verdict = detector().observe(turn(tool_out(FLAG)), turn=1)

    assert verdict is not None
    assert verdict.stop_class is StopClass.SOLVED
    assert verdict.verified is False


# ---- provider 失敗 -----------------------------------------------------


@pytest.mark.parametrize(
    "run_meta",
    [
        {"model": SYNTHETIC_MODEL, "is_error": True},
        {"model": SYNTHETIC_MODEL},
        {"model": "claude-x", "is_error": True},
    ],
)
def test_turns_where_the_model_never_ran_are_provider_failures(run_meta: dict[str, Any]) -> None:
    """實測連三圈 `API Error: 529 Overloaded`，`source.model` 是 `<synthetic>`。"""
    assert is_provider_failure(NormalizedRun(events=[], run_meta=run_meta)) is True


@pytest.mark.parametrize(
    "run_meta",
    [
        {"model": "claude-x"},
        {"model": "claude-x", "is_error": True, "subtype": "error_max_turns"},
        {"model": "claude-x", "is_error": True, "subtype": "error_max_budget_usd"},
        {"model": "claude-x", "is_error": True, "interrupted": True},
    ],
)
def test_capped_and_interrupted_turns_are_not_provider_failures(run_meta: dict[str, Any]) -> None:
    """撞到 SDK 自己的上限也會設 `is_error` —— 那是 agent 有在做事，只是被切斷。"""
    assert is_provider_failure(NormalizedRun(events=[], run_meta=run_meta)) is False


@pytest.mark.parametrize(
    "event",
    [
        tool_out("total 48\ndrwxr-xr-x 3 root root"),
        said("I disassembled it and the chart is 40 bytes long."),
        AgentRunEvent(seq=1, role="assistant", kind="tool_call", name="Bash", content="ls -la"),
    ],
)
def test_a_turn_that_did_work_before_erroring_is_not_a_provider_failure(
    event: AgentRunEvent,
) -> None:
    """`error_during_execution` 是 SDK 的萬用桶：跑了四十個工具才撞到 stream 錯誤的圈，
    設的旗標與模型從來沒啟動的那一圈一模一樣。列舉 subtype 列不完，所以要正面證據。"""
    run = NormalizedRun(
        events=[event],
        run_meta={"model": "claude-x", "is_error": True, "subtype": "error_during_execution"},
    )

    assert is_provider_failure(run) is False


def test_an_erroring_turn_that_produced_nothing_is_still_a_provider_failure() -> None:
    run = NormalizedRun(
        events=[],
        run_meta={"model": "claude-x", "is_error": True, "subtype": "error_during_execution"},
    )

    assert is_provider_failure(run) is True


def test_one_provider_failure_stops_nothing() -> None:
    """529 會偶發，一圈就停會把整個 run 賠給一次瞬斷。"""
    brake = detector()
    failure = turn(said("API Error: 529 Overloaded"), model=SYNTHETIC_MODEL, is_error=True)

    assert brake.observe(failure, turn=1) is None


def test_two_consecutive_provider_failures_stop_the_run() -> None:
    brake = detector()
    failure = turn(said("API Error: 529 Overloaded"), model=SYNTHETIC_MODEL, is_error=True)

    assert brake.observe(failure, turn=1) is None
    verdict = brake.observe(failure, turn=2)

    assert verdict is not None
    assert verdict.stop_class is StopClass.PROVIDER_ERROR
    assert verdict.stop_class.counts_as_solved is False
    assert verdict.flag is None


def test_a_working_turn_resets_the_provider_error_streak() -> None:
    brake = detector()
    failure = turn(said("API Error: 529 Overloaded"), model=SYNTHETIC_MODEL, is_error=True)

    assert brake.observe(failure, turn=1) is None
    assert brake.observe(turn(tool_out("nothing interesting")), turn=2) is None
    assert brake.observe(failure, turn=3) is None


def test_the_provider_error_brake_can_be_switched_off() -> None:
    brake = detector(provider_error_turns=0)
    failure = turn(said("API Error: 529 Overloaded"), model=SYNTHETIC_MODEL, is_error=True)

    assert [brake.observe(failure, turn=i) for i in range(1, 5)] == [None] * 4


def test_a_flag_printed_during_a_failing_turn_still_wins() -> None:
    """`is_error` 講的是這一圈怎麼結束的；工具印出來的東西是關於題目的事實。"""
    brake = detector()
    mixed = turn(tool_out(FLAG), model=SYNTHETIC_MODEL, is_error=True)

    verdict = brake.observe(mixed, turn=1)
    assert verdict is not None and verdict.stop_class is StopClass.SOLVED


# ---- 偵測器的狀態與輸出 ------------------------------------------------


def test_the_verdict_is_sticky() -> None:
    """判定下了就不再改 —— loop 會 break，但誰再問一次都該拿到同一個答案。"""
    brake = detector()
    first = brake.observe(turn(tool_out(FLAG)), turn=1)
    second = brake.observe(turn(tool_out(OTHER_FLAG)), turn=2)

    assert first is second
    assert brake.verdict is first


def test_stopped_because_is_prefixed_so_it_never_looks_like_a_limit() -> None:
    verdict = detector().observe(turn(tool_out(FLAG)), turn=3)

    assert verdict is not None
    assert verdict.stopped_because.startswith("flagstop/solved: ")
    assert verdict.evidence == "turn 3, tool Bash"


def test_only_solved_counts_as_solved() -> None:
    solved = {cls for cls in StopClass if cls.counts_as_solved}
    assert solved == {StopClass.SOLVED}


def test_masking_hides_the_body_but_stays_comparable_across_runs() -> None:
    masked = mask_flag(FLAG)

    assert FLAG not in masked
    assert masked.startswith("SEKAI{n")
    assert masked == mask_flag(FLAG)
    assert masked != mask_flag(OTHER_FLAG)


def test_the_detector_masks_the_flag_when_asked() -> None:
    verdict = detector(mask_flags=True).observe(turn(tool_out(FLAG)), turn=1)

    assert verdict is not None and verdict.flag is not None
    assert verdict.flag != FLAG
    # 偵測本身永遠看原文，遮罩只是呈現層
    assert detector(mask_flags=True).mask(FLAG) == verdict.flag
