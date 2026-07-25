"""Memory Core 的 client。介面在 `agent_bridge.protocols.MemoryClient`。"""

from memory.client.fake import FakeMemoryClient, demo_snapshot

__all__ = ["FakeMemoryClient", "demo_snapshot", "HttpMemoryClient"]


def __getattr__(name: str):
    # httpx 只有正式打 Memory Core 才需要，離線測試不必因為它沒裝就爆掉
    if name == "HttpMemoryClient":
        from memory.client.http import HttpMemoryClient

        return HttpMemoryClient
    raise AttributeError(name)
