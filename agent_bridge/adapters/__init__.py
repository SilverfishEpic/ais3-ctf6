"""外部 Agent 的 adapter。第一版只有 Claude 與重播執行紀錄的假貨。"""

from agent_bridge.adapters.fake import FakeAgentAdapter

__all__ = ["FakeAgentAdapter"]
