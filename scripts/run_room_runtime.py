from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import uvicorn
from decision_room.dev_env import (
    dotenv_autoload_enabled,
    load_local_dotenv,
    parse_dotenv_line,
)

_dotenv_autoload_enabled = dotenv_autoload_enabled
_parse_dotenv_line = parse_dotenv_line
_load_local_dotenv = load_local_dotenv


def main() -> None:
    # Development convenience only. Production should still inject env vars externally.
    _load_local_dotenv(ROOT / ".env")

    host = os.getenv("ROOM_RUNTIME_HOST", "127.0.0.1")
    port = int(os.getenv("ROOM_RUNTIME_PORT", "8000"))
    uvicorn.run(
        "decision_room.runtime.http_api:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
