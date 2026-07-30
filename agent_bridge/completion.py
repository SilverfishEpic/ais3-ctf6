"""完成訊號：這一輪的工具輸出裡，有沒有出現「新的、形狀合理的」flag。

為什麼需要這個模組
------------------
`LoopLimits` 只有三個上限（圈數、花費、連續空 diff），**沒有任何完成訊號**。
實測 `rev/SekaiCTF2022-matrix-lab-1` 第 1 圈就解掉了，迴圈照樣跑到花費上限才停：
$2.63 對上對照組（純 `claude -p`、同樣 tool grants）的 $0.52；
第 2 圈的結論是「The challenge is solved and closed - no work remains.」。
`idle_turns_before_stop` 救不了 —— 那兩圈白板都還有微幅變動，idle 計數一直被歸零
（`agent-status.json` 記到 turns=3、productive_turns=3）。

判斷的乾草堆只有一個
--------------------
**本輪 `NormalizedRun.events` 裡 `kind == "tool_result"` 且
`outcome ∈ {succeeded, failed}` 的內容。** 四層防線：

1. **頻道隔離**：不讀白板、不讀 assistant 講的話，並排除 memory MCP 工具的輸出
   —— 那個 tool_result 的內容**就是**白板。Summarizer 曾經把一句描述 flag *格式*
   的話變成白板上的 `SEKAI{''; Flag suffix: ''}`，任何讀白板的 oracle 都會在那誤觸。
2. **t=0 baseline**（`scan_roots`）：開跑前掃過 workspace 與其他指定目錄，把已經在
   磁碟上的 flag 形狀 token 記成抑制名單。`test-challenge-set` 有 12/20 題的真 flag
   就放在 `challenge/` 底下，只做 containment 會在超過一半的題目上第 1 圈誤報成功。
3. **合理性過濾**（`implausible`）：空 body、placeholder、太短、含空白引號分號
   （那是散文）、格式字串、regex 字元類 —— 一律丟掉。
4. **可選的 ground truth**（`FlagStopConfig.expect_sha256`）：只在記憶體裡留 sha256，
   命中才算 `SOLVED`；命中但 t=0 就在磁碟上 → `LEAKED`（那是去讀答案，不是解出來的），
   一樣停，但不能算解題成功。

除了 `scan_workspace()`（它就是那個 t=0 掃描）之外，這個模組**不做任何 I/O**。
偵測本身是純函式：餵 events、baseline、config 就有答案。

刻意不做的事
------------
- **不執行任何子程序去驗證。** `solve.py` 是 agent 自己寫的，exit code 與 stdout
  完全由它控制，而 baseline 對它零覆蓋（t=0 時檔案還不存在，probe 必過）。
- **不寫任何東西進 Memory Core。** `state.yaml` 依然沒有 flag 的概念；
  要有可 grade 的產物是 `ChallengeMeta.solution` 的合約變更，那需要 ADR。
- **不看自然語言。** `/solved|complete|no work remains/` 會同時命中
  "the challenge is **not** solved"。設計文件裡的兩個備援因此都沒有實作：
  `CLAIMED`（要 agent 自己喊 sentinel）得在每個 prompt 尾巴加指令，那是消融實驗的
  自變數污染；`CONVERGED`（資訊產出收斂）需要 ingest diff，超出「只看本輪 events」
  這個乾草堆，而且分不出「解完了」與「被外部擋住、每圈重跑同一個 exploit」。

已知還擋不住的
--------------
- **第 1 層是靠工具名稱做的。** 白板另外有一份在磁碟上（`runs/<id>/state.yaml`），
  `cat` 它會變成一筆 **Bash** 的 tool_result，名稱上看不出來。緩解手段是把
  `runs/` 與 `.ctfmem/` 留在 baseline 的掃描範圍內（見 `SKIP_DIRS`）；
  兩者都不在任何掃描 root 底下時，這條路仍然是通的。
- **泛用形狀（沒有 `--flag-prefix`）擋不掉所有程式碼。** prefix 規則要求大寫或數字，
  外加冒號/逗號過濾，殺掉了 `body{margin:0}`、`Point{X:1,Y:2}`、`vector{1,2,3}` 這類，
  但 `Foo{barbaz}` 這種形狀與真 flag 無法區分。給得出 prefix 就給。
- **baseline 只涵蓋開跑前就存在的檔案。** agent 自己 `echo` 一個形狀合理的字串
  照樣算目擊。只有 `--expect-flag-file` 或人看 evidence 那一行能消除這一項。

**一次目擊證明的是「某個真的跑過的工具印出了這個字串」，不是這個 flag 是對的。**
沒有 ground truth 時，`SOLVED` 要讀成「帶著一個候選答案停下來」。

完整設計與被否決的做法：`docs/experiments/2026-07-30-memory-ablation/flag-stop-design.md`。
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable, Sequence, Set
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from agent_bridge.normalize import AgentRunEvent, NormalizedRun


class StopClass(StrEnum):
    """穩定的停止分類。

    `LoopOutcome.stopped_because` 是自由格式中文字串，下游沒有東西解析得了；
    做統計、做消融比較要的是這個 enum。值本身寫進 `agent-status.json`。
    """

    #: 本輪工具輸出印出了新的、形狀合理的 flag（有 ground truth 時是 sha256 命中）
    SOLVED = "solved"
    #: 命中了期望的 flag，但它 t=0 就在 workspace 裡 —— agent 是去讀答案的
    LEAKED = "leaked"
    #: 連續數圈 provider 失敗（529 之類），再跑下去只是燒錢
    PROVIDER_ERROR = "provider_error"
    #: 花費上限
    BUDGET = "budget"
    #: 連續 N 圈白板沒有變化
    IDLE = "idle"
    #: 圈數上限
    TURN_CAP = "turn_cap"
    #: sentinel 檔或 Ctrl-C
    HUMAN_STOP = "human_stop"
    #: Agent 執行被中斷
    INTERRUPTED = "interrupted"
    OTHER = "other"

    @property
    def counts_as_solved(self) -> bool:
        """統計時只有這一類算解出來。`LEAKED` 是讀到答案，其餘都是煞車。"""
        return self is StopClass.SOLVED


# ---------------------------------------------------------------------------
# flag 的形狀與合理性
# ---------------------------------------------------------------------------

#: 泛用形狀的 prefix 規則：**必須帶一個大寫字母或數字**，或者就是 `flag` / `ctf`。
#:
#: 少了這一條，泛用形狀會把一般的程式碼當成 flag —— `body{margin:0}`、`div{display:flex}`、
#: `sqrt{x+y+z}`、`frac{numerator}`、`main{return0}` 全部通得過第 3 層（那一層是拿散文
#: 調出來的，不是拿程式碼）。任何 `curl` 一個 web 題、`cat` 一份原始碼的圈都會誤觸。
#: `SEKAI`、`Sekai`、`picoCTF`、`HTB`、`flag`、`ctf` 照樣命中；純小寫的自訂 prefix
#: （`sekai{...}`）在泛用模式下會漏掉 —— 那正是 `--flag-prefix` 存在的理由。
_GENERIC_PREFIX = (
    # 先斷言整個 prefix 的長度與開頭，再讓消費的部分去要求「有大寫或數字」
    r"(?=[A-Za-z][A-Za-z0-9_]{1,31}\{)"
    r"(?:[A-Za-z0-9_]*[A-Z0-9][A-Za-z0-9_]*|flag|ctf)"
)

#: 格式不可知，但結構上容不下散文：單層大括號、body 非空、不跨行。
#: 用 search 不是 fullmatch —— flag 是夾在工具輸出裡的。
GENERIC_FLAG_PATTERN = _GENERIC_PREFIX + r"\{[^{}\r\n]{1,256}\}"
GENERIC_FLAG_RE = re.compile(GENERIC_FLAG_PATTERN)

#: body 一看就不是答案的。這不是安全機制，是針對這套題目真的會出現的 placeholder。
PLACEHOLDER_BODIES: frozenset[str] = frozenset({
    "", "...", "…", "flag", "the_flag", "flag_here", "your_flag_here", "test_flag",
    "testflag", "dummy", "dummy_flag", "fake_flag", "example", "redacted", "xxx", "todo",
    "placeholder", "changeme",
})

#: body 只由這些字元組成就是裝飾，不是答案。
PLACEHOLDER_CHARS: frozenset[str] = frozenset(".…*?xX_- ")

#: 太短的 body 一律不信。
MIN_BODY_CHARS = 4

#: 唯讀記憶工具。它們的輸出**就是白板**，而白板正是我們拒絕閱讀的那個頻道。
#: 名稱來源：`agent_bridge.skill.TOOL_NAMES` 與 `agent_bridge.memory_mcp`。
#: 這裡刻意用字串比對而不 import，免得一個純函式模組去拉 SDK 相依。
MEMORY_TOOL_MARKERS: tuple[str, ...] = (
    "mcp__memory__", "memory_show", "memory_query", "memory_guess",
)

#: 「工具真的執行過，而且這是它印出來的」。`denied` / `interrupted` / `unknown`
#: 都不是關於題目的觀察證據（見 `normalize` 模組開頭的提案 3）。
EVIDENCE_OUTCOMES: frozenset[str] = frozenset({"succeeded", "failed"})

#: 這些 subtype 的意思是「agent 有在做事，只是撞到上限」，**不是** provider 掛掉。
#: `error_max_turns` 也會設 `is_error`，把它當 provider 失敗會殺掉最長、
#: 也最有產出的那些圈，而且標錯原因。
CAPPED_SUBTYPES: frozenset[str] = frozenset({
    "error_max_turns",
    "error_max_budget_usd",
    "error_max_structured_output_retries",
})

#: SDK 在「模型根本沒回話」時填的假 model（實測 529 Overloaded 三連發時看到的）。
SYNTHETIC_MODEL = "<synthetic>"

#: 「這一圈至少做了點什麼」的證據。任何一筆有內容的工具呼叫、工具輸出或 assistant
#: 發言，就足以證明模型有跑起來 —— 那麼 `is_error` 講的是它怎麼結束的，不是它沒開始。
WORK_EVENT_KINDS: frozenset[str] = frozenset({"tool_call", "tool_result", "message"})

#: baseline 掃描不進去的目錄。版本控制與虛擬環境沒有題目的答案，只有大量位元組。
#:
#: `runs/`（Memory Core 的 `state.yaml` / `journal.jsonl`）與 `.ctfmem/`（上一次 run 的
#: `agent-run.log`）**刻意不在名單上**。第 1 層是靠工具名稱排除白板的，可是
#: `cat runs/<id>/state.yaml` 是一筆 Bash 的 tool_result，名稱上看不出來；把這兩個目錄
#: 跳過等於同時繞開第 1 層與第 2 層。它們反而是抑制名單最有價值的來源 ——
#: 上一輪從 README 折進白板的 flag、上一次 run 印在 log 裡的答案，都在裡面。
SKIP_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "node_modules", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache",
})

#: 抑制名單的張數上限。泛用形狀掃一棵 400 MB 的樹，這個集合原本是無界的。
MAX_BASELINE_TOKENS = 100_000

#: `\Z` 錨定的尾段大括號。`.*` 是刻意的（不是 `[^{}]*`）—— `FLAG{a{b}}` 這種巢狀
#: body 也要取得出來，取不出來就會被判成「no brace body」而永遠停不下來。
_BODY_RE = re.compile(r"\{(?P<body>.*)\}\Z", re.DOTALL)
_FORMAT_SPEC_RE = re.compile(r"%[-+ #0-9.]*[sdioxXeEfgGcrb]|\{\d*\}|\$\{")
#: 空白與引號是散文；冒號與逗號是 struct literal 與 CSS 宣告
#: （`Point{X:1,Y:2}`、`body{margin:0}`）。有 ground truth 時這一層整層跳過，
#: 所以真的含這些字元的 flag 仍然可以用 `--expect-flag-file` 抓到。
_PROSE_RE = re.compile(r"""[\s'";:,]""")


def build_flag_pattern(
    prefixes: Sequence[str] | None = None,
    custom: str | None = None,
) -> re.Pattern[str]:
    """`--flag-regex` > `--flag-prefix` > 泛用形狀。

    給得出 prefix 就給 —— 把乾草堆縮到該場比賽的 flag 格式，誤報率遠低於泛用形狀。
    """
    if custom:
        try:
            return re.compile(custom)
        except re.error as exc:
            raise ValueError(f"--flag-regex 不是合法的正規表示式：{exc}") from exc
    alternatives = "|".join(re.escape(p) for p in (prefixes or ()) if p)
    if alternatives:
        return re.compile(rf"(?:{alternatives})\{{[^{{}}\r\n]{{1,256}}\}}")
    return GENERIC_FLAG_RE


def flag_body(token: str) -> str:
    """token 裡真正承載答案的那一段。

    沒有尾段大括號時就是 token 自己 —— `--flag-regex` 可以合法描述不帶大括號的格式
    （裸的 32 位 hex digest、`flag_...`）。舊版在這裡直接回「no brace body」，
    等於讓 `--flag-regex` 成為一個**永遠踩不下去**的煞車：它會武裝、會在 JSON 裡
    回報 `armed: true`、會把每一個真 flag 累積進 `rejected`，然後什麼都不做。
    """
    match = _BODY_RE.search(token)
    return match.group("body") if match is not None else token


def placeholder_reason(token: str) -> str | None:
    """只有「就算是真答案也還是假的」那幾條。回 `None` 代表這不是明顯的佔位字串。

    這是 ground truth 唯一可以套用的子集：`--expect-flag-file` 指到一份模板
    （`SEKAI{}`、`FLAG{placeholder}`）會讓整份 eval 的分數是假的，所以那個要擋；
    但 `implausible()` 其餘的條件是**形狀猜測**，真答案本來就可能長得很怪
    （`SEKAI{hello world}`、`SEKAI{it's_me}`），拿它們去擋答案檔會把好好的 run
    在啟動時就拒絕掉，而偵測端根本收得下這些字串。
    """
    body = flag_body(token).strip()
    if not body:
        return "empty body"
    if body.lower() in PLACEHOLDER_BODIES:
        return "placeholder body"
    if set(body) <= PLACEHOLDER_CHARS:
        return "body is decoration only"
    return None


def implausible(token: str) -> str | None:
    """回拒絕理由；回 `None` 代表這個 token 有可能是真的 flag。

    實測全部被殺：`SEKAI{''; Flag suffix: ''}`、`SEKAI{}`、`SekaiCTF{...}`、
    `sum_{i=0}`、`SEKAI{test_flag}`、`SEKAI{[a-z_]+}`、`SEKAI{%s}`、`SEKAI{____}`；
    形狀合理的真 flag 會通過；原始答案不保留在程式註解中。

    有 ground truth 時這一整層都不會被呼叫（見 `_reject`）。
    """
    reason = placeholder_reason(token)
    if reason is not None:
        return reason
    body = flag_body(token)
    if len(body.strip()) < MIN_BODY_CHARS:
        return f"body shorter than {MIN_BODY_CHARS} chars"
    if _PROSE_RE.search(body):
        # 有空白或引號就是「一句描述 flag 的話」，不是 flag。
        # 這一條就是殺掉 SEKAI{''; Flag suffix: ''} 的那一條。
        return "body contains whitespace, quoting or code punctuation"
    if _FORMAT_SPEC_RE.search(body):
        return "body looks like a format string"
    if ("[" in body and "]" in body) or "\\" in body:
        # SEKAI{[a-z_]+} —— README 在講 flag 的字母表，不是 flag 本身。
        return "body looks like a regex or character class"
    return None


def sha256_token(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def mask_flag(token: str, *, enabled: bool = True) -> str:
    """遮罩後仍然可以跨 run 比對是不是同一個字串。"""
    if not enabled:
        return token
    return f"{token[:8]}…<sha256:{sha256_token(token)[:12]}>"


# ---------------------------------------------------------------------------
# 設定與判定結果
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FlagStopConfig:
    """一次 run 的偵測設定。全部有預設值，`FlagStopConfig()` 就是泛用形狀 + 無 ground truth。"""

    pattern: re.Pattern[str] = GENERIC_FLAG_RE
    #: `--expect-flag-file` 的 sha256。設了之後**只有** hash 命中才會停，
    #: 而且 t=0 就存在的話會標成 `LEAKED`。明文絕不落地、絕不進 argv。
    expect_sha256: str | None = None
    #: 對外呈現時把 flag 換成前綴 + hash。偵測本身永遠看原文。
    mask_flags: bool = False
    #: 連續幾圈 provider 失敗就停。0 = 關掉。
    provider_error_turns: int = 2


@dataclass(frozen=True)
class FlagSighting:
    """一次通過四層防線的目擊。`token` 是原文，遮罩是呈現層的事。"""

    token: str
    tool: str
    #: 是否經過 ground truth 的 sha256 比對
    verified: bool = False
    #: t=0 的 workspace 裡就有這個字串
    pre_existing: bool = False

    @property
    def stop_class(self) -> StopClass:
        return StopClass.LEAKED if self.pre_existing else StopClass.SOLVED


@dataclass(frozen=True)
class RejectedToken:
    """看到了但被丟掉的 token。留著是為了讓人能檢查煞車有沒有誤殺。"""

    token: str
    reason: str


@dataclass(frozen=True)
class FlagScan:
    """一輪的掃描結果。`sighting is None` 就是這一輪沒有可信的目擊。"""

    sighting: FlagSighting | None = None
    rejected: tuple[RejectedToken, ...] = ()


@dataclass(frozen=True)
class StopVerdict:
    """偵測器決定要停下來的理由。`flag` 已經套用過 mask 設定。"""

    stop_class: StopClass
    reason: str
    turn: int
    flag: str | None = None
    evidence: str | None = None
    verified: bool = False

    @property
    def stopped_because(self) -> str:
        """寫進 `LoopOutcome.stopped_because` 的字串。

        前綴讓它跟 loop 原本那些中文煞車理由一眼分得開，
        而且與封存的消融實驗數據（wrapper 版）用同一個格式。
        """
        return f"flagstop/{self.stop_class.value}: {self.reason}"


# ---------------------------------------------------------------------------
# 純偵測
# ---------------------------------------------------------------------------


def is_evidence(event: AgentRunEvent) -> bool:
    """這一筆是不是「真的跑過、而且不是白板」的工具輸出。"""
    if event.kind != "tool_result" or event.outcome not in EVIDENCE_OUTCOMES:
        return False
    name = str(event.name or "")
    return not any(marker in name for marker in MEMORY_TOOL_MARKERS)


def scan_events(
    events: Iterable[AgentRunEvent],
    *,
    config: FlagStopConfig | None = None,
    baseline: Set[str] = frozenset(),
) -> FlagScan:
    """純函式：只讀本輪真的執行過的工具輸出，回一個判定。

    第一個通過四層防線的 token 就是答案；其餘（含被丟掉的）留在 `rejected` 裡，
    讓人能事後檢查煞車踩得對不對。同一個 token 只判一次。
    """
    config = config or FlagStopConfig()
    sighting: FlagSighting | None = None
    rejected: list[RejectedToken] = []
    seen: set[str] = set()

    for event in events:
        if not is_evidence(event):
            continue
        tool = str(event.name or "") or "?"
        for match in config.pattern.finditer(event.content or ""):
            # group(0)：使用者自訂的 regex 可能帶捕捉群組，findall 會回錯東西
            token = match.group(0)
            if token in seen:
                continue
            seen.add(token)
            reason = _reject(token, config=config, baseline=baseline)
            if reason is not None:
                rejected.append(RejectedToken(token=token, reason=reason))
                continue
            if sighting is None:
                sighting = FlagSighting(
                    token=token,
                    tool=tool,
                    verified=config.expect_sha256 is not None,
                    pre_existing=token in baseline,
                )
            else:
                # 同一圈出現第二個合格候選，正是人最需要兩個都看到的時候。
                # 安靜丟掉它會在 `rejected` 這條稽核線上剛好於曖昧處開一個洞。
                rejected.append(
                    RejectedToken(token=token, reason="another candidate in the same turn")
                )
    return FlagScan(sighting=sighting, rejected=tuple(rejected))


def _reject(token: str, *, config: FlagStopConfig, baseline: Set[str]) -> str | None:
    if config.expect_sha256 is not None:
        # 有 ground truth 時，形狀合不合理無關緊要（真答案本來就可能長得像 placeholder），
        # t=0 就存在也照樣停 —— 只是會被標成 LEAKED 而不是 SOLVED。
        if sha256_token(token) != config.expect_sha256:
            return "not the expected flag"
        return None
    if token in baseline:
        return "already in the workspace at t=0"
    return implausible(token)


def is_provider_failure(run: NormalizedRun) -> bool:
    """這一圈是不是「模型根本沒跑起來」。

    實測有過三圈連續 `API Error: 529 Overloaded`，`source.model` 是 `<synthetic>`。
    這種圈沒有工具輸出、也沒有產出，連兩圈就沒有再跑下去的理由。

    被中斷、以及撞到 SDK 自己上限的圈（`error_max_turns` 也會設 `is_error`）
    都**不算** —— 那些是 agent 有在做事，只是被切斷。

    但列舉 subtype 是列不完的：`error_during_execution` 是 SDK 的萬用桶，一圈跑了
    四十個工具之後才撞到 stream 錯誤，設的旗標與模型從來沒啟動的那一圈一模一樣。
    所以這裡不再靠「is_error 就是掛了」，而是要求**正面證據**：這一圈什麼都沒產出。
    """
    meta = run.run_meta
    if meta.get("interrupted"):
        return False
    if str(meta.get("subtype") or "") in CAPPED_SUBTYPES:
        return False
    if str(meta.get("model") or "") == SYNTHETIC_MODEL:
        return True
    if not meta.get("is_error"):
        return False
    return not any(
        event.kind in WORK_EVENT_KINDS and (event.content or "").strip() for event in run.events
    )


# ---------------------------------------------------------------------------
# t=0 workspace baseline（這個模組唯一碰 I/O 的地方）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineLimits:
    """掃描的上限。撞到上限只會讓抑制名單變短，`Baseline.complete` 會說出來。"""

    max_file_bytes: int = 4_000_000
    max_total_bytes: int = 400_000_000


@dataclass(frozen=True)
class Baseline:
    """t=0 時就在 workspace 裡的 flag 形狀 token。"""

    tokens: frozenset[str] = frozenset()
    files: int = 0
    #: False 代表有檔案沒掃到（太大、讀不到、或撞到總量上限），抑制名單可能不完整
    complete: bool = True


def scan_workspace(
    root: str | Path,
    pattern: re.Pattern[str] = GENERIC_FLAG_RE,
    *,
    limits: BaselineLimits | None = None,
) -> Baseline:
    """掃一棵樹。多棵請用 `scan_roots()`。"""
    return scan_roots([root], pattern, limits=limits)


def scan_roots(
    roots: Iterable[str | Path],
    pattern: re.Pattern[str] = GENERIC_FLAG_RE,
    *,
    limits: BaselineLimits | None = None,
) -> Baseline:
    """開跑前掃過這些目錄，把已經在磁碟上的 flag 形狀 token 收成抑制名單。

    實測 `test-challenge-set` 有 12/20 題的真 flag 就躺在 `challenge/` 底下，
    沒有這一層，超過一半的題目會在第 1 圈誤報成功。

    **為什麼要收多個 root**：workspace 不等於 agent 摸得到的範圍 —— `ToolPolicy`
    完全沒有路徑限制，而 eval 用的目錄配置正是把答案放在 workspace 的**兄弟目錄**
    （`.../challenge` 旁邊就是 `.../solution`）。只掃 workspace 的話，
    `cat ../solution/README.md` 會被判成 `SOLVED` 而不是 `LEAKED` ——
    那個分類唯一存在的理由就是抓這件事，卻剛好在該抓的時候永遠不會觸發。

    幾棵樹共用同一份位元組預算；巢狀的 root 會被摺掉，不會掃兩次。
    """
    limits = limits or BaselineLimits()
    tokens: set[str] = set()
    files = 0
    total = 0
    kept, complete = _distinct_roots(roots)

    for root in kept:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in SKIP_DIRS]
            for name in filenames:
                path = Path(dirpath) / name
                try:
                    size = path.stat().st_size
                except OSError:
                    complete = False
                    continue
                if size == 0:
                    continue
                if size > limits.max_file_bytes:
                    complete = False
                    continue
                if total + size > limits.max_total_bytes:
                    return Baseline(tokens=frozenset(tokens), files=files, complete=False)
                try:
                    blob = path.read_bytes()
                except OSError:
                    complete = False
                    continue
                total += len(blob)
                files += 1
                for text in _decoded_views(blob):
                    tokens.update(match.group(0) for match in pattern.finditer(text))
                if len(tokens) > MAX_BASELINE_TOKENS:
                    return Baseline(tokens=frozenset(tokens), files=files, complete=False)

    return Baseline(tokens=frozenset(tokens), files=files, complete=complete)


def _distinct_roots(roots: Iterable[str | Path]) -> tuple[list[Path], bool]:
    """解析成絕對路徑，去掉重複與被別人包住的。第二個回傳值 False = 有 root 不存在。"""
    resolved: list[Path] = []
    complete = True
    for raw in roots:
        try:
            resolved.append(Path(raw).resolve(strict=True))
        except OSError:
            complete = False
    kept: list[Path] = []
    for path in sorted(set(resolved), key=lambda item: len(item.parts)):
        if any(path == other or other in path.parents for other in kept):
            continue
        kept.append(path)
    return kept, complete


def _decoded_views(blob: bytes) -> tuple[str, ...]:
    """要掃的文字視角。latin-1 對單位元組內容等同 `strings`。

    UTF-16 是那個視角完全看不到的情況（`S\\x00E\\x00K\\x00…` 打不中任何 pattern），
    而一份存成 UTF-16 的答案檔 `cat` 得出來卻不在抑制名單上 —— 那就是一次假的 `SOLVED`。
    所以有 NUL 時再掃一次去掉 NUL 的視角。跨 NUL 縫合出來的多餘 token 只會讓抑制名單
    變長，而抑制名單變長的代價是多跑幾圈，不是誤報成功。
    """
    text = blob.decode("latin-1")
    if b"\x00" not in blob:
        return (text,)
    return (text, blob.replace(b"\x00", b"").decode("latin-1"))


# ---------------------------------------------------------------------------
# 跨圈的狀態
# ---------------------------------------------------------------------------


class CompletionDetector:
    """一個 run 一個實例：每圈跑完餵一次 `observe()`，回 verdict 就代表該停了。

    狀態只有兩樣：已經下的判定（下了就不再變），與連續 provider 失敗的計數。
    真正的判斷全在 `scan_events()` 那個純函式裡。
    """

    def __init__(
        self,
        config: FlagStopConfig | None = None,
        *,
        baseline: Baseline | None = None,
    ) -> None:
        self.config = config or FlagStopConfig()
        self.baseline = baseline or Baseline()
        self.verdict: StopVerdict | None = None
        self.sightings: list[FlagSighting] = []
        self.rejected: list[RejectedToken] = []
        self._provider_error_streak = 0

    def observe(self, run: NormalizedRun, *, turn: int) -> StopVerdict | None:
        """看完這一圈。回 `None` 代表沒理由停。"""
        if self.verdict is not None:
            return self.verdict

        scan = scan_events(run.events, config=self.config, baseline=self.baseline.tokens)
        self.rejected.extend(scan.rejected)
        if scan.sighting is not None:
            self.sightings.append(scan.sighting)
            self.verdict = self._flag_verdict(scan.sighting, turn=turn)
            return self.verdict

        if is_provider_failure(run):
            self._provider_error_streak += 1
        else:
            self._provider_error_streak = 0
        limit = self.config.provider_error_turns
        if limit > 0 and self._provider_error_streak >= limit:
            self.verdict = StopVerdict(
                stop_class=StopClass.PROVIDER_ERROR,
                reason=(
                    f"{self._provider_error_streak} consecutive turns came back as provider "
                    "errors; the model never ran"
                ),
                turn=turn,
            )
        return self.verdict

    def mask(self, token: str) -> str:
        return mask_flag(token, enabled=self.config.mask_flags)

    def _flag_verdict(self, sighting: FlagSighting, *, turn: int) -> StopVerdict:
        if sighting.pre_existing:
            reason = (
                "the expected flag was printed by a tool, but that same token was already in "
                "the workspace at t=0 — the agent read the answer, it did not derive it"
            )
        elif sighting.verified:
            reason = "the expected flag (sha256 match) was printed by a tool this run"
        else:
            reason = (
                "a new flag-shaped token was printed by a tool "
                "(not present in the workspace at t=0)"
            )
        return StopVerdict(
            stop_class=sighting.stop_class,
            reason=reason,
            turn=turn,
            flag=self.mask(sighting.token),
            evidence=f"turn {turn}, tool {sighting.tool}",
            verified=sighting.verified,
        )


__all__ = [
    "CAPPED_SUBTYPES",
    "EVIDENCE_OUTCOMES",
    "GENERIC_FLAG_PATTERN",
    "GENERIC_FLAG_RE",
    "MAX_BASELINE_TOKENS",
    "MEMORY_TOOL_MARKERS",
    "MIN_BODY_CHARS",
    "PLACEHOLDER_BODIES",
    "PLACEHOLDER_CHARS",
    "SKIP_DIRS",
    "SYNTHETIC_MODEL",
    "WORK_EVENT_KINDS",
    "Baseline",
    "BaselineLimits",
    "CompletionDetector",
    "FlagScan",
    "FlagSighting",
    "FlagStopConfig",
    "RejectedToken",
    "StopClass",
    "StopVerdict",
    "build_flag_pattern",
    "flag_body",
    "implausible",
    "is_evidence",
    "is_provider_failure",
    "mask_flag",
    "placeholder_reason",
    "scan_events",
    "scan_roots",
    "scan_workspace",
    "sha256_token",
]
