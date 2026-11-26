from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("relative_path", ["servidor-mcp/main.py", "agente/main.py"])
def test_entrypoint_import_does_not_start_service(relative_path: str) -> None:
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(path.parent.name.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(module)

    assert callable(module.create_app)
