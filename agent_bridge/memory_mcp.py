"""STDIO MCP server for read-only Memory Core tools.

Claude uses the Claude SDK's in-process MCP helper.  Codex expects MCP servers to be
configured as stdio or HTTP endpoints, so this module provides the same read-only tool
surface over newline-delimited JSON-RPC on stdin/stdout.

Only read methods are exposed:

- ``memory_show``
- ``memory_query``
- ``memory_guess``

No mutation, template switching, YAML editing, journal appending, or Summarizer access is
available through this server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from agent_bridge.inject import render_board
from agent_bridge.memory.http import HttpMemoryClient
from agent_bridge.policy import BY_NAME, FULL, MemoryPolicy
from agent_bridge.protocols import MemoryClient

JsonDict = dict[str, Any]

READ_ONLY = (
    "唯讀。你無法寫入白板；這一輪的執行紀錄會自動送去 Memory Core ingest，"
    "由 Memory Core 決定哪些成為 Fact、哪些是 Assumption。"
)


@dataclass
class MemoryMCPServer:
    client: MemoryClient
    challenge_id: str
    policy: MemoryPolicy = FULL

    async def handle(self, request: JsonDict) -> JsonDict | None:
        method = request.get("method")
        request_id = request.get("id")

        if method == "notifications/initialized":
            return None
        if method == "ping":
            return _result(request_id, {})
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ctfmem-memory", "version": "1.0.0"},
                    "instructions": (
                        "Read-only CTF shared memory tools. Use memory_show to refresh the "
                        "whiteboard, memory_query for grounded questions, and memory_guess "
                        "for template-owned guessing candidates. No write tools are exposed."
                    ),
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": _tools()})
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict):
                return _error(request_id, -32602, "params must be an object")
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _error(request_id, -32602, "arguments must be an object")
            return await self._call_tool(request_id, name, arguments)

        return _error(request_id, -32601, f"unknown method: {method}")

    async def _call_tool(self, request_id: object, name: str, arguments: JsonDict) -> JsonDict:
        handlers: dict[str, Callable[[JsonDict], Awaitable[str]]] = {
            "memory_show": self._memory_show,
            "memory_query": self._memory_query,
            "memory_guess": self._memory_guess,
        }
        handler = handlers.get(name)
        if handler is None:
            return _error(request_id, -32602, f"unknown tool: {name}")
        try:
            text = await handler(arguments)
        except Exception as exc:  # noqa: BLE001 - MCP errors must stay on the protocol channel.
            return _error(request_id, -32000, str(exc)[:800])
        return _result(request_id, {"content": [{"type": "text", "text": text}]})

    async def _memory_show(self, _arguments: JsonDict) -> str:
        snapshot = await self.client.get_memory(self.challenge_id)
        candidates = None
        if self.policy.includes("guessing"):
            result = await self.client.guess(
                self.challenge_id,
                count=self.policy.guess_count,
                seed=self.policy.guess_seed,
            )
            candidates = result.get("candidates")
        return render_board(snapshot, self.policy, guess_candidates=candidates)

    async def _memory_query(self, arguments: JsonDict) -> str:
        question = str(arguments.get("question") or "").strip()
        if not question:
            return "要問什麼？question 不能是空的。"
        result = await self.client.query(
            self.challenge_id,
            question,
            layers=[layer for layer in self.policy.layers if layer != "guessing"],
        )
        answer = str(result.get("answer") or "")
        references = result.get("references") or []
        if references:
            answer += f"\n\n（依據：{', '.join(str(ref) for ref in references)}）"
        return answer

    async def _memory_guess(self, arguments: JsonDict) -> str:
        count = int(arguments.get("count") or self.policy.guess_count)
        result = await self.client.guess(
            self.challenge_id,
            count=max(1, min(count, 10)),
            seed=self.policy.guess_seed,
        )
        candidates = result.get("candidates") or []
        if not candidates:
            return "這個題型的清單裡已經沒有沒試過的方向了。"
        lines = [f"從 {result.get('catalog')} 抽出的方向（尚未驗證）："]
        lines += [f"  {c.get('id')}  {c.get('title', '')}".rstrip() for c in candidates]
        return "\n".join(lines)


async def serve_stdio(server: MemoryMCPServer) -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _write(_error(None, -32700, f"invalid JSON: {exc}"))
            continue
        if not isinstance(request, dict):
            _write(_error(None, -32600, "request must be an object"))
            continue
        response = await server.handle(request)
        if response is not None:
            _write(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ctfmem-memory-mcp")
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--challenge", required=True)
    parser.add_argument("--policy", default="full", choices=sorted(BY_NAME))
    parser.add_argument("--guess-seed", type=int)
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args(argv)

    policy = BY_NAME.get(args.policy, FULL)
    if args.guess_seed is not None:
        policy = policy.with_(guess_seed=args.guess_seed)

    async def run() -> None:
        client = HttpMemoryClient(args.api_url, timeout=args.timeout)
        try:
            await serve_stdio(MemoryMCPServer(client, args.challenge, policy))
        finally:
            await client.close()

    asyncio.run(run())
    return 0


def _tools() -> list[JsonDict]:
    return [
        {
            "name": "memory_show",
            "description": "顯示共享白板目前的完整內容。" + READ_ONLY,
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "memory_query",
            "description": "用自然語言問白板上已經確認了什麼。" + READ_ONLY,
            "inputSchema": {
                "type": "object",
                "properties": {"question": {"type": "string"}},
                "required": ["question"],
                "additionalProperties": False,
            },
        },
        {
            "name": "memory_guess",
            "description": "從目前題型的技術清單中取得尚未驗證的候選方向。" + READ_ONLY,
            "inputSchema": {
                "type": "object",
                "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 10}},
                "additionalProperties": False,
            },
        },
    ]


def _result(request_id: object, result: JsonDict) -> JsonDict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: object, code: int, message: str) -> JsonDict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _write(response: JsonDict) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
