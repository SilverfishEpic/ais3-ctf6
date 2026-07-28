from __future__ import annotations

import asyncio
from typing import Any

import httpx
from conftest import ASGITestClient
from fastapi import FastAPI

from human_client.client import MemoryCoreClient
from human_client.config import ClientConfig
from human_client.web.app import create_web_app
from memory_core.service import MemoryService

PORT = 8787
ORIGIN = f"http://127.0.0.1:{PORT}"


class StaticTemplateHTTPClient:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = body

    def get(self, url: str, **kwargs: Any) -> httpx.Response:
        assert url == "/v1/templates"
        return httpx.Response(200, json=self.body)

    def post(self, url: str, **kwargs: Any) -> httpx.Response:
        raise AssertionError(f"unexpected POST {url}")


def call(app: FastAPI, method: str, url: str, **kwargs: Any) -> httpx.Response:
    async def send() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as client:
            return await client.request(method, url, **kwargs)

    return asyncio.run(send())


def test_memory_core_publishes_template_metadata(service: MemoryService) -> None:
    client = MemoryCoreClient("http://testserver", http_client=ASGITestClient(service))

    body = client.list_templates()

    templates = body["templates"]
    assert {template["id"] for template in templates} >= {"generic", "web"}
    web = next(template for template in templates if template["id"] == "web")
    assert web["catalog"] == f"web@{web['version']}"
    assert "general" in web["fact_sections"]
    assert "general" in web["assumption_sections"]
    assert any(technique["title"] for technique in web["guessing"])


def test_web_ui_gets_template_metadata_through_memory_core(service: MemoryService) -> None:
    template_body = MemoryCoreClient(
        "http://testserver", http_client=ASGITestClient(service)
    ).list_templates()
    core = MemoryCoreClient(
        "http://testserver", http_client=StaticTemplateHTTPClient(template_body)
    )
    app = create_web_app(
        client=core,
        summarizing_client=core,
        config=ClientConfig(
            api_url="http://testserver",
            challenge=None,
            actor="operator",
            timeout=30.0,
            json_output=False,
            verbose=False,
            color=False,
        ),
        port=PORT,
    )

    local = call(app, "GET", "/api/local/templates").json()
    proxied = call(app, "GET", "/api/v1/templates").json()

    assert local == proxied
    assert any(template["id"] == "web" for template in local["templates"])
