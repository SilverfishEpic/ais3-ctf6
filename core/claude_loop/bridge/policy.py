"""記憶注入策略 —— 消融實驗的開關住在這裡（決定 3）。

為什麼開關在 bridge 而不在 API
------------------------------
規劃書 §19.2 要求能切「完全沒記憶 / 只有 Fact / Fact+Assumption / 全部」四檔。

如果靠 Agent 自己呼叫 `memory_show` 去拿白板，每一組實驗數字都會被
「這一輪它想不想查」污染 —— 你永遠不知道它到底看到了什麼。

所以白板由 bridge 在 turn 開始時強制注入，工具只是「跑到一半想再查」的補救。
`no_memory` 那一檔既不注入、也不註冊工具，Agent 完全沒有管道拿到白板。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Literal

InjectMode = Literal["turn_start", "never"]


@dataclass(frozen=True)
class MemoryPolicy:
    """一次實驗跑的記憶設定。"""

    layers: tuple[str, ...] = ("fact", "assumption", "guessing")
    inject: InjectMode = "turn_start"
    skill_enabled: bool = True

    #: 第一輪給整份白板，之後只給 diff（決定 4）。
    #: same-session 才有意義；new-session 每輪都是新的 context，一律給整份。
    diff_after_first_turn: bool = True

    #: 注入 guessing 時要抽幾個、用什麼種子。種子讓 ablation 可重現。
    guess_count: int = 3
    guess_seed: int | None = None

    name: str = "full"

    @property
    def enabled(self) -> bool:
        return self.inject != "never" or self.skill_enabled

    @property
    def wants(self) -> frozenset[str]:
        return frozenset(self.layers)

    def includes(self, layer: str) -> bool:
        return layer in self.wants

    def with_(self, **changes: object) -> "MemoryPolicy":
        return replace(self, **changes)  # type: ignore[arg-type]


# 規劃書 §19.2 的四檔。用同一個種子跑，差異才只來自記憶層數。
NO_MEMORY = MemoryPolicy(
    layers=(),
    inject="never",
    skill_enabled=False,
    diff_after_first_turn=False,
    name="no_memory",
)
FACT_ONLY = MemoryPolicy(layers=("fact",), name="fact_only")
FACT_AND_ASSUMPTION = MemoryPolicy(
    layers=("fact", "assumption"), name="fact_and_assumption"
)
FULL = MemoryPolicy(layers=("fact", "assumption", "guessing"), name="full")

ABLATION_LEVELS: tuple[MemoryPolicy, ...] = (
    NO_MEMORY,
    FACT_ONLY,
    FACT_AND_ASSUMPTION,
    FULL,
)

BY_NAME = {p.name: p for p in ABLATION_LEVELS}


def get(name: str) -> MemoryPolicy:
    if name not in BY_NAME:
        raise KeyError(f"沒有這一檔：{name}。可用的：{', '.join(BY_NAME)}")
    return BY_NAME[name]


@dataclass
class SessionMode:
    """規劃書 §15 的第二個實驗變因。

    same-session：每輪延續同一個 Agent session，白板用 diff 增量給。
    new-session ：每輪重開，沒有對話歷史，白板每次給整份。
    """

    kind: Literal["same", "new"] = "same"

    @property
    def resets_each_turn(self) -> bool:
        return self.kind == "new"

    def effective(self, policy: MemoryPolicy) -> MemoryPolicy:
        """new-session 沒有舊快照留在 context 裡，給 diff 沒有意義。"""
        if self.resets_each_turn:
            return policy.with_(diff_after_first_turn=False)
        return policy
