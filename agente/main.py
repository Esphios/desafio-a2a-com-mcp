from __future__ import annotations

import os
from typing import Any

from a2a.types import AgentCard, AgentCapabilities, AgentInterface, AgentSkill
from google.protobuf.json_format import MessageToDict
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from bridge import Bridge
from mcp_client import MCPClient


def _card() -> dict[str, Any]:
    # Validate the public shape with official A2A SDK v1 models, then serialize
    # with the field names expected on the wire.
    card = AgentCard(
        name="Agente de Reserva de Salas",
        description="Faz a ponte A2A para o servidor MCP de salas.",
        supported_interfaces=[
            AgentInterface(url="http://127.0.0.1:7300/a2a", protocol_binding="JSONRPC", protocol_version="1.0")
        ],
        version="1.0.0",
        capabilities=AgentCapabilities(),
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(
                id="reservar-sala",
                name="Reservar sala",
                description="Reserva uma sala ou pede uma alternativa.",
                tags=["salas", "reserva"],
                examples=["reservar sala=sala-porao inicio=... fim=... responsavel=Doc"],
            )
        ],
    )
    return MessageToDict(card, preserving_proto_field_name=False)


def create_app(mcp_client: Any | None = None) -> Starlette:
    bridge = Bridge(mcp_client or MCPClient(os.environ.get("MCP_URL", "http://127.0.0.1:7301/mcp")))

    async def card(_: Request) -> JSONResponse:
        return JSONResponse(_card())

    async def rpc(request: Request) -> JSONResponse:
        payload = await request.json()
        request_id = payload.get("id")
        try:
            method = payload.get("method")
            params = payload.get("params") or {}
            if method == "SendMessage":
                result = await bridge.send_message(params["message"], request.headers.get("traceparent"))
            elif method == "GetTask":
                result = bridge.tasks.get(params.get("id"))
                if result is None:
                    raise ValueError("Task inexistente")
            else:
                return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}})
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
        except (KeyError, ValueError) as exc:
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}})
        except RuntimeError as exc:
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32001, "message": str(exc)}})

    app = Starlette(routes=[Route("/.well-known/agent-card.json", card), Route("/a2a", rpc, methods=["POST"])])
    app.state.bridge = bridge
    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host="127.0.0.1", port=int(os.environ.get("A2A_PORT", "7300")))


if __name__ == "__main__":
    main()
