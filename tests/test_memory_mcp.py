from __future__ import annotations

import asyncio

from agent_bridge.memory.fake import FakeMemoryClient, demo_snapshot
from agent_bridge.memory_mcp import MemoryMCPServer
from agent_bridge.policy import FULL


def test_memory_mcp_lists_read_only_tools() -> None:
    async def run() -> None:
        client = FakeMemoryClient(demo_snapshot("demo-web"))
        await client.create_challenge("demo-web")
        server = MemoryMCPServer(client, "demo-web", FULL)

        response = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

        assert response is not None
        tools = response["result"]["tools"]
        assert {tool["name"] for tool in tools} == {
            "memory_show",
            "memory_query",
            "memory_guess",
        }
        assert all("mutate" not in tool["name"] for tool in tools)

    asyncio.run(run())


def test_memory_mcp_show_query_and_guess_are_read_only() -> None:
    async def run() -> None:
        client = FakeMemoryClient(demo_snapshot("demo-web"))
        await client.create_challenge("demo-web")
        server = MemoryMCPServer(client, "demo-web", FULL)

        show = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "memory_show", "arguments": {}},
            }
        )
        query = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "memory_query",
                    "arguments": {"question": "what is open?"},
                },
            }
        )
        guess = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "memory_guess", "arguments": {"count": 1}},
            }
        )

        assert show is not None and "TCP/8080" in show["result"]["content"][0]["text"]
        assert query is not None and "what is open?" in query["result"]["content"][0]["text"]
        assert guess is not None and "WEB-SSRF" in guess["result"]["content"][0]["text"]
        assert [name for name, _ in client.calls] == [
            "create_challenge",
            "get_memory",
            "guess",
            "query",
            "guess",
        ]

    asyncio.run(run())


def test_memory_mcp_rejects_unknown_tool() -> None:
    async def run() -> None:
        client = FakeMemoryClient(demo_snapshot("demo-web"))
        await client.create_challenge("demo-web")
        server = MemoryMCPServer(client, "demo-web", FULL)

        response = await server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "memory_mutate", "arguments": {}},
            }
        )

        assert response is not None
        assert response["error"]["code"] == -32602

    asyncio.run(run())
