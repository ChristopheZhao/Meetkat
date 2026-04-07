from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def dotenv_autoload_enabled() -> bool:
    flag = os.getenv("DOTENV_DISABLE_AUTOLOAD", "").strip().lower()
    return flag not in {"1", "true", "yes", "on"}


def parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        raise RuntimeError(f"invalid .env line: {line.rstrip()}")

    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not key or any(char.isspace() for char in key):
        raise RuntimeError(f"invalid .env key: {line.rstrip()}")

    value = raw_value.strip()
    if value and value[0] not in {'"', "'"}:
        value = value.split(" #", 1)[0].rstrip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        value = value[1:-1]
    return key, value


def load_local_dotenv(dotenv_path: Path) -> None:
    if not dotenv_autoload_enabled() or not dotenv_path.is_file():
        return

    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        parsed = parse_dotenv_line(line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)
