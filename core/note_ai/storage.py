import json
import os
import sys
from pathlib import Path


class Storage:
    """處理所有檔案與 I/O 操作"""

    @staticmethod
    def load_dotenv(path: Path) -> None:
        if not path.is_file():
            return

        for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            if line.startswith("export "):
                line = line[len("export ") :].strip()

            if "=" not in line:
                print(f"[env] Ignored .env line {line_number}: missing '='", file=sys.stderr)
                continue

            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip()

            if not key:
                print(f"[env] Ignored .env line {line_number}: empty key", file=sys.stderr)
                continue

            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]

            os.environ.setdefault(key, value)

    @staticmethod
    def resolve_path(path_value: str | Path, base_dir: Path) -> Path:
        path = Path(path_value)
        return path if path.is_absolute() else base_dir / path

    @staticmethod
    def append_jsonl(path: Path, item: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")

    @staticmethod
    def load_recent_jsonl(path: Path, limit: int) -> list[dict]:
        if limit <= 0 or not path.is_file():
            return []

        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
        items: list[dict] = []
        for line in lines:
            if not line.strip():
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return items

    @staticmethod
    def read_text_if_exists(path: Path) -> str:
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    @staticmethod
    def write_text(path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n", encoding="utf-8")

    @staticmethod
    def save_json(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
