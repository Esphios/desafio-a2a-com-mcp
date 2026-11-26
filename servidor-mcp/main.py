from __future__ import annotations

import os

from mcp_app import create_app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("MCP_PORT", "7301")), log_level="warning")


if __name__ == "__main__":
    main()
