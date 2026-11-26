from __future__ import annotations

import asyncio
import itertools
from typing import Any

import httpx


PROTOCOL_VERSION = "2026-07-28"


class MCPClient:
    def __init__(self, url: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = url
        self.transport = transport
        self._ids = itertools.count(1)
        self._id_lock = asyncio.Lock()
        self.tools: set[str] = set()
        self.policy_version: str | None = None

    async def _request(
        self, method: str, params: dict[str, Any], *, name: str | None = None, traceparent: str | None = None
    ) -> dict[str, Any]:
        async with self._id_lock:
            request_id = next(self._ids)
        meta: dict[str, Any] = {
            "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
            "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
        }
        if traceparent:
            meta["traceparent"] = traceparent
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
            "params": {**params, "_meta": meta},
        }
        async with httpx.AsyncClient(timeout=30, transport=self.transport) as client:
            response = await client.post(self.url, headers=headers, json=payload)
        data = response.json()
        if "error" in data:
            error = data["error"]
            raise RuntimeError(f"MCP {error.get('code')}: {error.get('message')}")
        return data["result"]

    async def initialize(self, traceparent: str | None = None) -> None:
        listed = await self._request("tools/list", {}, traceparent=traceparent)
        self.tools = {tool["name"] for tool in listed.get("tools", [])}
        resource = await self._request(
            "resources/read", {"uri": "politica://uso"}, name="politica://uso", traceparent=traceparent
        )
        text = resource.get("contents", [{}])[0].get("text", "")
        for line in text.splitlines():
            if line.strip().startswith("versao:"):
                self.policy_version = line.split(":", 1)[1].strip()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        traceparent: str | None = None,
        request_state: str | None = None,
        input_key: str | None = None,
        choice: str | None = None,
        decline: bool = False,
    ) -> dict[str, Any]:
        if name not in self.tools:
            raise RuntimeError(f"Tool MCP nao descoberta: {name}")
        params: dict[str, Any] = {"name": name, "arguments": arguments}
        if request_state is not None and input_key is not None:
            response: dict[str, Any] = {"action": "decline"} if decline else {
                "action": "accept",
                "content": {"sala": choice},
            }
            params.update({"requestState": request_state, "inputResponses": {input_key: response}})
        return await self._request("tools/call", params, name=name, traceparent=traceparent)
