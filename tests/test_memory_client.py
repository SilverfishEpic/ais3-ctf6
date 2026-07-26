"""`HttpMemoryClient` 送出去的請求要符合合約。

用 `httpx.MockTransport` 攔下請求檢查 body,完全離線,不需要起服務。
真的打一次服務是 `tools/integration_check.py` 的事。

重點在 `mutate` / `set_template` 這兩個新方法 —— 它們是人類 override 的路,
合約強制要帶 `expected_revision` 與 `reason`,少一個就是 422。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from jsonschema import Draft202012Validator

from agent_bridge.memory.http import HttpMemoryClient
from agent_bridge.protocols import MemoryClient

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((REPO_ROOT / "spec" / "openapi.yaml").read_text(encoding="utf-8"))
SCHEMAS = SPEC["components"]["schemas"]


def validator_for(name: str) -> Draft202012Validator:
    """把 schema 拉出來獨立驗 —— `$ref` 要能解到 components。"""
    schema = {**SCHEMAS[name], "$defs": SCHEMAS}
    resolved = json.loads(
        json.dumps(schema).replace('"#/components/schemas/', '"#/$defs/')
    )
    return Draft202012Validator(resolved)


def capture(status: int = 200, payload: dict[str, Any] | None = None) -> tuple[list, Any]:
    """回 (收到的請求清單, transport)。"""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=payload if payload is not None else {})

    return seen, httpx.MockTransport(handler)


def call(coro_factory, *, status: int = 200, payload: dict[str, Any] | None = None):
    """跑一次呼叫,回 (請求, 回傳值)。"""
    seen, transport = capture(status, payload)

    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://core.test") as raw:
            client = HttpMemoryClient("http://core.test", client=raw)
            return await coro_factory(client)

    result = asyncio.run(run())
    return seen[0], result


def body_of(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


# ---- Protocol 的邊界 ---------------------------------------------------


def test_loop_facing_protocol_cannot_mutate() -> None:
    """`AgentLoop` 只認 `MemoryClient`,那個介面裡沒有寫入白板的方法。

    這是結構上的保證(規劃書 §10.1:Agent Bridge 禁止直接寫 state),
    比註解可靠。`HttpMemoryClient` 自己有 mutate,但 loop 拿不到。
    """
    assert not hasattr(MemoryClient, "mutate")
    assert not hasattr(MemoryClient, "set_template")
    assert hasattr(HttpMemoryClient, "mutate")
    assert hasattr(HttpMemoryClient, "set_template")


# ---- mutate ------------------------------------------------------------


def test_mutate_sends_a_contract_valid_body() -> None:
    request, _ = call(
        lambda c: c.mutate(
            "demo",
            expected_revision=2,
            actor="ronny",
            reason="在 GDB 裡人工確認過",
            operations=[{"op": "promote", "id": "A001", "to": "fact"}],
        )
    )

    assert request.method == "POST"
    assert request.url.path == "/v1/challenges/demo/mutate"

    body = body_of(request)
    errors = list(validator_for("MemoryMutation").iter_errors(body))
    assert not errors, [e.message for e in errors]
    assert body["expected_revision"] == 2
    assert body["reason"]


def test_mutate_surfaces_a_revision_conflict() -> None:
    """§10.2:revision 不符要回 409,client 要能認出來重讀後重試。"""
    from agent_bridge.errors import MemoryCoreError

    payload = {
        "error": {
            "code": "revision_conflict",
            "message": "stale revision",
            "details": {"current_revision": 7},
        }
    }
    seen, transport = capture(409, payload)

    async def run():
        async with httpx.AsyncClient(transport=transport, base_url="http://core.test") as raw:
            client = HttpMemoryClient("http://core.test", client=raw)
            await client.mutate(
                "demo", expected_revision=2, actor="ronny", reason="x", operations=[]
            )

    with pytest.raises(MemoryCoreError) as caught:
        asyncio.run(run())

    assert caught.value.is_revision_conflict
    assert caught.value.current_revision == 7


def test_mark_tried_is_expressible() -> None:
    """規劃書 §4.3 的 tried 狀態目前**只有這條路**能改。

    自動迴圈裡沒有任何人呼叫它 —— 誰負責更新是 Spec 要裁的。
    這支測試確認管道本身是通的,不是在主張 bridge 該去呼叫它。
    """
    request, _ = call(
        lambda c: c.mutate(
            "demo",
            expected_revision=3,
            actor="ronny",
            reason="試過 SQLi 了",
            operations=[{"op": "mark_tried", "technique": "WEB-SQLI", "tried": True}],
        )
    )
    errors = list(validator_for("MemoryMutation").iter_errors(body_of(request)))
    assert not errors, [e.message for e in errors]


# ---- set_template ------------------------------------------------------


def test_set_template_sends_a_contract_valid_body() -> None:
    request, _ = call(
        lambda c: c.set_template(
            "demo", "pwn", expected_revision=5, actor="ronny", reason="這題是 binary exploitation"
        )
    )

    assert request.url.path == "/v1/challenges/demo/template"
    body = body_of(request)
    errors = list(validator_for("TemplateChangeRequest").iter_errors(body))
    assert not errors, [e.message for e in errors]
    assert body["template"] == "pwn"


# ---- 既有端點沒被改壞 --------------------------------------------------


def test_create_challenge_can_pin_a_template() -> None:
    """§19.3 的模板消融:建立時就指定,比事後 reclassify 乾淨。"""
    request, _ = call(
        lambda c: c.create_challenge("demo", title="Demo", initial_input="…", template="generic")
    )
    assert body_of(request)["template"] == "generic"


def test_guess_forwards_the_seed() -> None:
    """§8.5:seed 讓消融可重現。"""
    request, _ = call(lambda c: c.guess("demo", count=3, seed=42))
    body = body_of(request)
    assert body["seed"] == 42
    assert body["only_untried"] is True


def test_guess_omits_the_seed_when_unset() -> None:
    """沒給 seed 就不要送 `null` 過去,讓服務自己決定。"""
    request, _ = call(lambda c: c.guess("demo"))
    assert "seed" not in body_of(request)
