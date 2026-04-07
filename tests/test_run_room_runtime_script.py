import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "run_room_runtime.py"
SPEC = importlib.util.spec_from_file_location("run_room_runtime_script", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("failed to load run_room_runtime.py")
MODULE = importlib.util.module_from_spec(SPEC)
_original_uvicorn = sys.modules.get("uvicorn")
sys.modules["uvicorn"] = types.SimpleNamespace(run=lambda *args, **kwargs: None)
try:
    SPEC.loader.exec_module(MODULE)
finally:
    if _original_uvicorn is None:
        sys.modules.pop("uvicorn", None)
    else:
        sys.modules["uvicorn"] = _original_uvicorn


class RunRoomRuntimeScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = {
            "MODEL_DEFAULT_SUPPLIER": os.environ.get("MODEL_DEFAULT_SUPPLIER"),
            "MODEL_DEFAULT_MODEL": os.environ.get("MODEL_DEFAULT_MODEL"),
            "DOTENV_DISABLE_AUTOLOAD": os.environ.get("DOTENV_DISABLE_AUTOLOAD"),
        }

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_load_local_dotenv_sets_missing_values_only(self) -> None:
        os.environ.pop("MODEL_DEFAULT_SUPPLIER", None)
        os.environ.pop("MODEL_DEFAULT_MODEL", None)
        os.environ.pop("DOTENV_DISABLE_AUTOLOAD", None)

        with TemporaryDirectory() as tmpdir:
            dotenv_path = Path(tmpdir) / ".env"
            dotenv_path.write_text(
                'MODEL_DEFAULT_SUPPLIER=qwen\nexport MODEL_DEFAULT_MODEL="qwen-plus"\n',
                encoding="utf-8",
            )

            MODULE._load_local_dotenv(dotenv_path)

        self.assertEqual(os.environ["MODEL_DEFAULT_SUPPLIER"], "qwen")
        self.assertEqual(os.environ["MODEL_DEFAULT_MODEL"], "qwen-plus")

    def test_load_local_dotenv_does_not_override_existing_env(self) -> None:
        os.environ["MODEL_DEFAULT_SUPPLIER"] = "existing"
        os.environ.pop("DOTENV_DISABLE_AUTOLOAD", None)

        with TemporaryDirectory() as tmpdir:
            dotenv_path = Path(tmpdir) / ".env"
            dotenv_path.write_text("MODEL_DEFAULT_SUPPLIER=qwen\n", encoding="utf-8")

            MODULE._load_local_dotenv(dotenv_path)

        self.assertEqual(os.environ["MODEL_DEFAULT_SUPPLIER"], "existing")

    def test_load_local_dotenv_can_be_disabled(self) -> None:
        os.environ.pop("MODEL_DEFAULT_SUPPLIER", None)
        os.environ["DOTENV_DISABLE_AUTOLOAD"] = "1"

        with TemporaryDirectory() as tmpdir:
            dotenv_path = Path(tmpdir) / ".env"
            dotenv_path.write_text("MODEL_DEFAULT_SUPPLIER=qwen\n", encoding="utf-8")

            MODULE._load_local_dotenv(dotenv_path)

        self.assertNotIn("MODEL_DEFAULT_SUPPLIER", os.environ)


if __name__ == "__main__":
    unittest.main()
