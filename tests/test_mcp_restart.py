from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "2026-07-28"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def start_server(port: int, secret: str) -> subprocess.Popen[bytes]:
    env = {**os.environ, "MCP_PORT": str(port), "REQUEST_STATE_SECRET": secret}
    process = subprocess.Popen(
        [sys.executable, "servidor-mcp/main.py"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("MCP subprocess terminou durante startup")
        try:
            request(port, "tools/list", {}, request_id=0)
            return process
        except (httpx.HTTPError, ConnectionError):
            time.sleep(0.05)
    process.terminate()
    raise TimeoutError("MCP subprocess nao iniciou")


def stop_server(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def request(port: int, method: str, params: dict, *, request_id: int) -> dict:
    name = params.get("name") or params.get("uri")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if name:
        headers["Mcp-Name"] = name
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            **params,
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
            },
        },
    }
    return httpx.post(f"http://127.0.0.1:{port}/mcp", headers=headers, json=payload, timeout=5).json()


def test_request_state_survives_restart_with_same_secret_and_fails_with_other_secret() -> None:
    port = free_port()
    secret = "11" * 32
    arguments = {
        "sala": "sala-garagem",
        "inicio": "2026-11-03T14:00:00-03:00",
        "fim": "2026-11-03T15:00:00-03:00",
        "responsavel": "Marty",
    }
    server = start_server(port, secret)
    first = request(port, "tools/call", {"name": "reservar_sala", "arguments": arguments}, request_id=1)
    stop_server(server)
    result = first["result"]
    key = next(iter(result["inputRequests"]))
    retry_params = {
        "name": "reservar_sala",
        "arguments": arguments,
        "requestState": result["requestState"],
        "inputResponses": {key: {"action": "accept", "content": {"sala": "sala-fusca"}}},
    }

    server = start_server(port, secret)
    completed = request(port, "tools/call", retry_params, request_id=2)
    stop_server(server)
    assert completed["result"]["structuredContent"]["sala"] == "sala-fusca"

    server = start_server(port, "22" * 32)
    rejected = request(port, "tools/call", retry_params, request_id=3)
    stop_server(server)
    assert rejected["error"]["code"] == -32602
