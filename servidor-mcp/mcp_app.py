from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.request_state import RequestStateSecurity
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp.types import ElicitRequest, ElicitRequestFormParams, InputRequiredResult
from pydantic import BaseModel
from starlette.responses import JSONResponse

from room_domain import DomainError, RoomStore


PROTOCOL_VERSION = "2026-07-28"
MISSING_CAPABILITY = -32021
HEADER_MISMATCH = -32020


class SalaOut(BaseModel):
    id: str
    nome: str
    capacidade: int
    recursos: list[str]


class ListaDeSalas(BaseModel):
    salas: list[SalaOut]


class ConflitoOut(BaseModel):
    id: str
    inicio: str
    fim: str
    responsavel: str


class DisponibilidadeOut(BaseModel):
    sala: str
    livre: bool
    conflitos: list[ConflitoOut]


class ReservaOut(BaseModel):
    reserva: str | None = None
    reservado: bool = True
    sala: str | None = None
    inicio: str | None = None
    fim: str | None = None
    responsavel: str | None = None
    politica: str | None = None
    motivo: str | None = None


def _secret_bytes(value: str) -> bytes:
    try:
        secret = bytes.fromhex(value)
    except ValueError:
        secret = value.encode("utf-8")
    if len(secret) < 32:
        raise RuntimeError("REQUEST_STATE_SECRET deve conter no minimo 32 bytes")
    return secret


def build_server(root: Path, store: RoomStore) -> MCPServer:
    secret_value = os.environ.get("REQUEST_STATE_SECRET")
    if not secret_value:
        raise RuntimeError("REQUEST_STATE_SECRET e obrigatoria")
    security = RequestStateSecurity(
        keys=[_secret_bytes(secret_value)], ttl=600, audience="central-de-salas", bind_principal=None
    )
    server = MCPServer("central-de-salas", version="1.0.0", request_state_security=security)

    @server.tool(description="Lista todas as salas com capacidade e recursos.", structured_output=True)
    def listar_salas() -> ListaDeSalas:
        return ListaDeSalas(salas=[SalaOut.model_validate(room) for room in store.rooms])

    @server.tool(description="Diz se uma sala esta livre no intervalo, e quais reservas conflitam.", structured_output=True)
    def consultar_disponibilidade(sala: str, inicio: str, fim: str) -> DisponibilidadeOut:
        try:
            return DisponibilidadeOut.model_validate(store.availability(sala, inicio, fim))
        except DomainError as exc:
            raise ToolError(str(exc)) from exc

    @server.tool(
        description="Reserva uma sala. Se o intervalo estiver ocupado, pergunta qual alternativa usar.",
        structured_output=True,
    )
    def reservar_sala(
        sala: str, inicio: str, fim: str, responsavel: str, ctx: Context
    ) -> ReservaOut | InputRequiredResult:
        try:
            if ctx.request_state:
                sealed = json.loads(ctx.request_state)
                response = (ctx.input_responses or {}).get(sealed["input_key"])
                if response is None:
                    raise ToolError("Resposta da elicitation ausente")
                if response.action in {"decline", "cancel"}:
                    return ReservaOut(reservado=False, motivo="recusado")
                selected = (response.content or {}).get("sala")
                if selected not in sealed["alternatives"]:
                    raise ToolError("Sala alternativa invalida")
                original = sealed["arguments"]
                return ReservaOut.model_validate(
                    store.reserve(selected, original["inicio"], original["fim"], original["responsavel"])
                )

            availability = store.availability(sala, inicio, fim)
            if availability["livre"]:
                return ReservaOut.model_validate(store.reserve(sala, inicio, fim, responsavel))
            alternatives = store.alternatives(sala, inicio, fim)
            if not alternatives:
                raise DomainError("Sem alternativas disponiveis no intervalo")
            capabilities = ctx.client_capabilities
            elicitation = capabilities.elicitation if capabilities is not None else None
            if elicitation is None or elicitation.form is None:
                raise MCPError(
                    code=MISSING_CAPABILITY,
                    message="Client did not declare required form elicitation capability",
                    data={"requiredCapabilities": {"elicitation": {"form": {}}}},
                )
            input_key = "reserva:escolha_de_sala"
            room_schema: dict[str, Any] = {"type": "string"}
            room_schema["const" if len(alternatives) == 1 else "enum"] = (
                alternatives[0] if len(alternatives) == 1 else alternatives
            )
            state = json.dumps(
                {
                    "tool": "reservar_sala",
                    "arguments": {"sala": sala, "inicio": inicio, "fim": fim, "responsavel": responsavel},
                    "alternatives": alternatives,
                    "input_key": input_key,
                },
                separators=(",", ":"),
            )
            return InputRequiredResult(
                inputRequests={
                    input_key: ElicitRequest(
                        params=ElicitRequestFormParams(
                            message="A sala pedida esta ocupada nesse intervalo. Escolha uma alternativa.",
                            requestedSchema={
                                "type": "object",
                                "properties": {"sala": room_schema},
                                "required": ["sala"],
                            },
                        )
                    )
                },
                requestState=state,
            )
        except DomainError as exc:
            raise ToolError(str(exc)) from exc

    @server.resource(
        "politica://uso",
        name="politica-de-uso",
        description="Politica de uso das salas.",
        mime_type="text/markdown",
    )
    def politica_de_uso() -> str:
        return store.policy_text

    return server


class ProtocolBoundary:
    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http" or scope.get("path") != "/mcp" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._error(scope, receive, send, None, -32700, "Parse error", 400)
            return
        request_id = payload.get("id")
        method = payload.get("method")
        params = payload.get("params") or {}
        meta = params.get("_meta") or {}
        print(
            f"method={method} id={request_id} traceparent={meta.get('traceparent') or ''}",
            file=sys.stderr,
            flush=True,
        )
        if "io.modelcontextprotocol/protocolVersion" not in meta or "io.modelcontextprotocol/clientCapabilities" not in meta:
            await self._error(scope, receive, send, request_id, -32602, "Missing required request metadata", 400)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        expected_name = params.get("name") if method == "tools/call" else params.get("uri") if method == "resources/read" else None
        mismatch = (
            headers.get("mcp-protocol-version") != meta.get("io.modelcontextprotocol/protocolVersion")
            or headers.get("mcp-method") != method
            or (expected_name is not None and headers.get("mcp-name") != expected_name)
        )
        if mismatch:
            await self._error(scope, receive, send, request_id, HEADER_MISMATCH, "Mirrored MCP headers do not match request body", 400)
            return
        if method == "resources/read" and params.get("uri") != "politica://uso":
            await self._error(scope, receive, send, request_id, -32602, "Unknown resource URI", 200)
            return

        replayed = False

        async def replay() -> dict:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        messages: list[dict] = []

        async def capture(message: dict) -> None:
            messages.append(message)

        await self.app(scope, replay, capture)
        response_body = b"".join(item.get("body", b"") for item in messages if item["type"] == "http.response.body")
        status_override = None
        if response_body:
            try:
                if json.loads(response_body).get("error", {}).get("code") == MISSING_CAPABILITY:
                    status_override = 400
            except json.JSONDecodeError:
                pass
        for message in messages:
            if status_override is not None and message["type"] == "http.response.start":
                message = {**message, "status": status_override}
            await send(message)

    @staticmethod
    async def _error(scope: dict, receive: Any, send: Any, request_id: Any, code: int, message: str, status: int) -> None:
        await JSONResponse(
            {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, status_code=status
        )(scope, receive, send)


def create_app(root: Path | None = None):
    project_root = root or Path(__file__).resolve().parents[1]
    store = RoomStore(project_root / "dados")
    server = build_server(project_root, store)
    return ProtocolBoundary(
        server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
    )
