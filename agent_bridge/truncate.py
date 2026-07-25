"""工具輸出的截斷策略（決定 9）。

為什麼截斷歸 bridge：只有這一層知道原始大小。`nmap -A`、`hexdump` 一個
binary、`strings` 一整包，隨便就是幾百 KB，整包送進 `/ingest` 會把
Summarizer 的 context 撐爆。

為什麼一定要標記：`journal.jsonl` 的定位是「原始證據紀錄」。砍了卻不說，
之後拿 journal 重跑實驗會得到錯的結論。所以每一筆被砍過的內容都帶
`truncated` 與 `original_bytes`。

這兩個欄位需要 P1 在 `AgentRunEvent` 上開位置 —— contract 第 2 題。
"""

from __future__ import annotations

from dataclasses import dataclass

MARKER = "\n…[中間截斷 {dropped} bytes]…\n"


@dataclass(frozen=True)
class TruncationPolicy:
    """單筆留頭尾，整輪再設一個總量上限。

    `run_total_chars` 是**盡力而為**，不是硬上限。當
    「事件數 × `min_event_chars`」已經超過總預算時，寧可超出預算，
    也不要把每一筆證據都砍成沒有用的碎片 —— 十筆 50 字的殘骸
    對 Summarizer 的價值是零，還不如三筆完整的。

    `min_event_chars` 只在「整輪超標、要繼續往下砍」時生效。
    單筆本身能留多少，由 `head_chars + tail_chars` 決定；
    把那兩個設得比下限還小，就是明確選擇了要小事件。
    """

    head_chars: int = 2000
    tail_chars: int = 1000
    run_total_chars: int = 60_000
    min_event_chars: int = 200

    @property
    def per_event_chars(self) -> int:
        return self.head_chars + self.tail_chars


@dataclass
class Truncated:
    text: str
    truncated: bool
    original_bytes: int


def truncate_text(text: str, policy: TruncationPolicy) -> Truncated:
    """留頭尾、中間標明砍了多少。"""
    original_bytes = len(text.encode("utf-8"))
    if len(text) <= policy.per_event_chars:
        return Truncated(text=text, truncated=False, original_bytes=original_bytes)

    head = text[: policy.head_chars]
    tail = text[-policy.tail_chars :] if policy.tail_chars else ""
    dropped_bytes = original_bytes - len(head.encode("utf-8")) - len(tail.encode("utf-8"))
    return Truncated(
        text=head + MARKER.format(dropped=dropped_bytes) + tail,
        truncated=True,
        original_bytes=original_bytes,
    )


def apply_run_budget(
    texts: list[str],
    policy: TruncationPolicy,
) -> list[Truncated]:
    """先逐筆截斷，總量還是超過的話，再從最大的開始往下砍。

    回傳與輸入同順序、同長度。

    砍到 `policy.min_event_chars` 就停 —— 見 `TruncationPolicy` 的說明，
    這種情況下總量會超出預算，那是刻意的取捨。
    """
    results = [truncate_text(t, policy) for t in texts]

    def total() -> int:
        return sum(len(r.text) for r in results)

    # 由大到小逐一縮，直到進得了預算、或每一筆都已經到下限。
    # 留頭不留尾，因為指令輸出的開頭通常資訊密度最高。
    order = sorted(range(len(results)), key=lambda i: len(results[i].text), reverse=True)
    for idx in order:
        if total() <= policy.run_total_chars:
            break
        current = results[idx]
        overflow = total() - policy.run_total_chars
        keep = max(policy.min_event_chars, len(current.text) - overflow - len(MARKER) - 16)
        if keep >= len(current.text):
            continue  # 已經在下限或更小，不再往下砍
        head = current.text[:keep]
        dropped = current.original_bytes - len(head.encode("utf-8"))
        results[idx] = Truncated(
            text=head + MARKER.format(dropped=dropped),
            truncated=True,
            original_bytes=current.original_bytes,
        )
    return results
