from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for source_dir in (ROOT / "servidor-mcp", ROOT / "agente"):
    sys.path.insert(0, str(source_dir))
