"""把白板與 diff 渲染成要塞進 prompt 的文字（決定 3、4）。

第一輪給整份白板，之後每輪只給 diff。理由：same-session 模式下，第一輪注入的
快照還躺在 context 裡，到了第五輪它已經過期，會跟現況打架。給 diff 就沒有這個問題，
而且便宜、對 prompt cache 友善。

diff 只帶 ID 不帶內容，所以渲染 diff 需要新的 snapshot 來反查文字。

**不加來源標註**（決定 5）：摘要由 Summarizer 撰寫，不是 Agent 的自述，
所以沒有「Agent 讀到自己講的話並當成權威」的回流問題。`source_actor` 這個欄位
在 snapshot 裡是有的，日後要加只是這個檔案多帶一欄。
"""

from __future__ import annotations

from typing import Any

from memory.board import index_by_id, iter_items, revision
from bridge.policy import MemoryPolicy

NOTHING_YET = "白板目前是空的。"

READ_ONLY_NOTE = (
    "以上是共享白板的現況。你**不能**直接修改它 —— 你的執行紀錄會自動送去整理，"
    "由記憶服務決定什麼進白板。需要查更新的內容時再用 memory_show / memory_query。"
)

STALE_NOTE = (
    "注意：你 context 裡較早的白板快照已經過期，以上面這份異動為準。"
    "需要完整現況請呼叫 memory_show。"
)


def render_board(
    snapshot: dict[str, Any],
    policy: MemoryPolicy,
    *,
    guess_candidates: list[dict[str, Any]] | None = None,
) -> str:
    """整份白板。第一輪用這個。"""
    if not policy.wants:
        return ""

    challenge = snapshot.get("challenge") or {}
    lines: list[str] = [f"# 共享白板　rev {revision(snapshot)}"]

    title = challenge.get("title") or challenge.get("id")
    if title:
        lines.append(f"題目：{title}（{challenge.get('template', 'generic')}）")
    if challenge.get("summary"):
        lines.append(f"目前狀況：{challenge['summary']}")

    body_written = False

    for layer, heading in (("fact", "FACT　已確認"), ("assumption", "ASSUMPTION　待驗證")):
        if not policy.includes(layer):
            continue
        section_lines = _render_layer(snapshot, layer)
        if section_lines:
            body_written = True
            lines.append("")
            lines.append(f"## {heading}")
            lines.extend(section_lines)

    if policy.includes("guessing"):
        guess_lines = _render_guessing(snapshot, guess_candidates)
        if guess_lines:
            body_written = True
            lines.append("")
            lines.append("## GUESSING　這個題型可以試的方向")
            lines.extend(guess_lines)

    if not body_written:
        lines.append("")
        lines.append(NOTHING_YET)

    lines.append("")
    lines.append(READ_ONLY_NOTE)
    return "\n".join(lines)


def render_diff(
    diff: dict[str, Any],
    snapshot: dict[str, Any],
    policy: MemoryPolicy,
    *,
    revision_before: int | None = None,
) -> str:
    """自上一輪以來的異動。same-session 的第二輪起用這個。"""
    if not policy.wants:
        return ""

    index = index_by_id(snapshot)
    after = revision(snapshot)
    before = revision_before if revision_before is not None else after

    rows: list[str] = []

    def add(marker: str, ids: list[str], layer: str) -> None:
        if not policy.includes(layer):
            return
        for item_id in ids or []:
            entry = index.get(item_id)
            text = entry[2].get("text", "") if entry else "（已不在白板上）"
            rows.append(f" {marker} {item_id}  {text}")

    add("+", diff.get("fact_added", []), "fact")
    add("~", diff.get("fact_updated", []), "fact")
    add("-", diff.get("fact_removed", []), "fact")
    add("+", diff.get("assumption_added", []), "assumption")
    add("~", diff.get("assumption_updated", []), "assumption")
    add("-", diff.get("assumption_removed", []), "assumption")

    # promoted/demoted 在 diff 裡報的是**舊** ID，新 ID 出現在 *_added 裡
    # （protocol.md §7），所以這裡只說「它變了」，內容由上面那幾行負責。
    for item_id in diff.get("promoted", []) or []:
        rows.append(f" ↑ {item_id}  已升格為 Fact")
    for item_id in diff.get("demoted", []) or []:
        rows.append(f" ↓ {item_id}  已降回 Assumption")

    changed = diff.get("template_changed")
    if changed:
        rows.append(f" ⇄ 題型改為 {changed.get('to')}（原本 {changed.get('from')}）")

    if not rows:
        return ""

    lines = [f"# 白板異動　rev {before} → {after}", *rows, "", STALE_NOTE]
    return "\n".join(lines)


# ---------------------------------------------------------------------------


def _render_layer(snapshot: dict[str, Any], layer: str) -> list[str]:
    by_section: dict[str, list[str]] = {}
    for section, item in iter_items(snapshot, layer):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        by_section.setdefault(section, []).append(f"  {item.get('id', '?')}  {text}")
    lines: list[str] = []
    for section in sorted(by_section):
        lines.append(f"[{section}]")
        lines.extend(by_section[section])
    return lines


def _render_guessing(
    snapshot: dict[str, Any],
    candidates: list[dict[str, Any]] | None,
) -> list[str]:
    guessing = snapshot.get("guessing") or {}
    catalog = guessing.get("catalog")
    state = guessing.get("state") or {}

    lines: list[str] = []
    if catalog:
        lines.append(f"（catalog: {catalog}）")

    if candidates:
        # /guess 抽出來的建議帶標題，比 snapshot 裡的裸 ID 有用得多
        for candidate in candidates:
            lines.append(f"  {candidate.get('id')}  {candidate.get('title', '')}".rstrip())
    else:
        # 沒抽的話至少把試過什麼講清楚，免得它重複勞動
        tried = sorted(tid for tid, s in state.items() if s.get("tried"))
        if tried:
            lines.append(f"  已試過：{', '.join(tried)}")

    return lines if len(lines) > 1 or candidates else []
