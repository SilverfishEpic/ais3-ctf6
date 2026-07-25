import json
import urllib.error
import urllib.request


class LLMClient:
    """封裝 API 連線與 Request 發送"""

    def __init__(self, base_url: str, api_key: str | None, model: str, timeout: int = 60):
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def post_chat_completion(self, payload: dict) -> dict:
        endpoint = self.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            endpoint,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"API returned HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Could not connect to API: {exc.reason}") from exc

    @staticmethod
    def extract_message(response: dict) -> str:
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return json.dumps(response, ensure_ascii=False, indent=2)
