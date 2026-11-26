from __future__ import annotations

import json

import httpx
import pytest

from mcp_client import MCPClient, PROTOCOL_VERSION


@pytest.mark.asyncio
async def test_client_discovers_before_calls_and_emits_wire_contract() -> None:
    recorded: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        payload = json.loads(request.content)
        method = payload["method"]
        if method == "tools/list":
            result = {"tools": [{"name": "reservar_sala"}]}
        elif method == "resources/read":
            result = {"contents": [{"text": "versao: 2026-11-01\n"}]}
        elif "requestState" not in payload["params"]:
            result = {
                "resultType": "input_required",
                "requestState": "opaque",
                "inputRequests": {"key": {"params": {}}},
            }
        else:
            result = {"resultType": "complete", "structuredContent": {"sala": "sala-fusca"}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    client = MCPClient("http://mcp.test/mcp", httpx.MockTransport(handler))
    trace = "00-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-bbbbbbbbbbbbbbbb-01"
    await client.initialize(trace)
    arguments = {"sala": "sala-garagem", "inicio": "i", "fim": "f", "responsavel": "Marty"}
    await client.call_tool("reservar_sala", arguments, trace)
    await client.call_tool(
        "reservar_sala", arguments, trace, request_state="opaque", input_key="key", choice="sala-fusca"
    )

    bodies = [json.loads(request.content) for request in recorded]
    assert [body["method"] for body in bodies] == ["tools/list", "resources/read", "tools/call", "tools/call"]
    assert len({body["id"] for body in bodies}) == 4
    assert all(
        body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"] == {"elicitation": {"form": {}}}
        for body in bodies
    )
    assert all(body["params"]["_meta"]["traceparent"] == trace for body in bodies)
    assert recorded[2].headers["MCP-Protocol-Version"] == PROTOCOL_VERSION
    assert recorded[2].headers["Mcp-Method"] == "tools/call"
    assert recorded[2].headers["Mcp-Name"] == "reservar_sala"
    assert client.policy_version == "2026-11-01"
