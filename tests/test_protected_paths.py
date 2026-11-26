from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_starter_contract_paths_match_base_revision() -> None:
    result = subprocess.run(
        ["git", "diff", "--exit-code", "263f17e", "--", "dados", "validador", "exemplos"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
