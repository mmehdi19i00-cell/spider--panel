"""pytest configuration for Spider Panel tests."""
import sys
from pathlib import Path

# Ensure repo root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = ["pytest_asyncio"]


def pytest_configure(config):
    config.inicfg.setdefault("asyncio_mode", "auto")
