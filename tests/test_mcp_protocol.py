from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from mcp.server.request_state import RequestStateSecurity

from mcp_app import create_app


ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_VERSION = "2026-07-28"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("REQUEST_STATE_SECRET", "01" * 32)
    with TestClient(create_app(ROOT), base_url="http://127.0.0.1") as test_client:
        yield test_client


def request(
    client: TestClient,
    method: str,
    params: dict,
    *,
    request_id: int = 1,
    meta: dict | None = None,
    header_name: str | None = None,
) -> tuple[int, dict]:
    actual_meta = meta if meta is not None else {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
    }
    body_params = dict(params)
    body_params["_meta"] = actual_meta
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    if header_name is not None:
        headers["Mcp-Name"] = header_name
    response = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": body_params},
    )
    return response.status_code, response.json()


def result_text(result: dict) -> str:
    return " ".join(part.get("text", "") for part in result.get("content", []))


def test_tools_list_declares_three_tools_and_object_schemas(client: TestClient) -> None:
    status, payload = request(client, "tools/list", {})
    assert status == 200
    tools = {tool["name"]: tool for tool in payload["result"]["tools"]}
    assert set(tools) == {"listar_salas", "consultar_disponibilidade", "reservar_sala"}
    assert all(tool["inputSchema"]["type"] == "object" for tool in tools.values())
    assert tools["listar_salas"]["outputSchema"]["type"] == "object"


def test_listar_salas_returns_matching_structured_and_text_content(client: TestClient) -> None:
    status, payload = request(
        client,
        "tools/call",
        {"name": "listar_salas", "arguments": {}},
        header_name="listar_salas",
    )
    assert status == 200
    result = payload["result"]
    assert json.loads(result_text(result)) == result["structuredContent"]
    assert len(result["structuredContent"]["salas"]) == 5


def test_resource_returns_policy_and_unknown_uri_is_protocol_error(client: TestClient) -> None:
    status, payload = request(
        client,
        "resources/read",
        {"uri": "politica://uso"},
        header_name="politica://uso",
    )
    assert status == 200
    assert payload["result"]["contents"][0]["mimeType"] == "text/markdown"
    assert "versao: 2026-11-01" in payload["result"]["contents"][0]["text"]

    status, payload = request(
        client,
        "resources/read",
        {"uri": "politica://inexistente"},
        header_name="politica://inexistente",
    )
    assert status == 200
    assert payload["error"]["code"] == -32602


@pytest.mark.parametrize("missing", ["protocolVersion", "clientCapabilities"])
def test_missing_required_meta_is_invalid_params_and_http_400(client: TestClient, missing: str) -> None:
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
    }
    meta.pop(f"io.modelcontextprotocol/{missing}")
    status, payload = request(
        client,
        "tools/call",
        {"name": "listar_salas", "arguments": {}},
        meta=meta,
        header_name="listar_salas",
    )
    assert status == 400
    assert payload["error"]["code"] == -32602


def test_mirrored_name_mismatch_is_protocol_error(client: TestClient) -> None:
    status, payload = request(
        client,
        "tools/call",
        {"name": "listar_salas", "arguments": {}},
        header_name="reservar_sala",
    )
    assert status == 400
    assert payload["error"]["code"] == -32020


def test_unknown_tool_is_refused(client: TestClient) -> None:
    status, payload = request(
        client,
        "tools/call",
        {"name": "voar_delorean", "arguments": {}},
        header_name="voar_delorean",
    )
    assert status == 200
    refused = payload.get("error", {}).get("code") == -32602 or payload.get("result", {}).get("isError") is True
    assert refused


@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("reservar_sala", {"sala": "sala-delorean", "inicio": "2026-11-03T09:00:00-03:00", "fim": "2026-11-03T10:00:00-03:00", "responsavel": "Doc"}, "Sala inexistente: sala-delorean"),
        ("consultar_disponibilidade", {"sala": "sala-aquario", "inicio": "2026-11-03T07:00:00-03:00", "fim": "2026-11-03T08:00:00-03:00"}, "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ("consultar_disponibilidade", {"sala": "sala-aquario", "inicio": "2026-11-03T09:00:00-03:00", "fim": "2026-11-03T12:00:00-03:00"}, "Duracao acima do limite: a politica permite no maximo 2 horas"),
        ("consultar_disponibilidade", {"sala": "sala-aquario", "inicio": "2026-11-03T10:00:00-03:00", "fim": "2026-11-03T09:00:00-03:00"}, "Intervalo invalido: fim deve ser posterior a inicio"),
    ],
)
def test_domain_failures_are_complete_tool_errors(
    client: TestClient, name: str, arguments: dict, message: str
) -> None:
    _, payload = request(client, "tools/call", {"name": name, "arguments": arguments}, header_name=name)
    result = payload["result"]
    assert result["resultType"] == "complete"
    assert result["isError"] is True
    assert message in result_text(result)


def conflict(client: TestClient, *, capabilities: dict | None = None, request_id: int = 10) -> tuple[int, dict]:
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": (
            {"elicitation": {"form": {}}} if capabilities is None else capabilities
        ),
    }
    return request(
        client,
        "tools/call",
        {
            "name": "reservar_sala",
            "arguments": {
                "sala": "sala-garagem",
                "inicio": "2026-11-03T14:00:00-03:00",
                "fim": "2026-11-03T15:00:00-03:00",
                "responsavel": "Marty",
            },
        },
        request_id=request_id,
        meta=meta,
        header_name="reservar_sala",
    )


def test_conflict_returns_form_input_required_with_ordered_alternatives(client: TestClient) -> None:
    status, payload = conflict(client)
    assert status == 200
    result = payload["result"]
    assert result["resultType"] == "input_required"
    assert result["requestState"]
    assert len(result["inputRequests"]) == 1
    embedded = next(iter(result["inputRequests"].values()))
    assert embedded["method"] == "elicitation/create"
    assert embedded["params"]["mode"] == "form"
    assert embedded["params"]["requestedSchema"]["properties"]["sala"]["enum"] == [
        "sala-fusca",
        "sala-mirante",
    ]


def test_conflict_without_form_capability_is_http_400_and_minus_32021(client: TestClient) -> None:
    status, payload = conflict(client, capabilities={})
    assert status == 400
    assert payload["error"]["code"] == -32021
    assert payload["error"]["data"]["requiredCapabilities"] == {"elicitation": {"form": {}}}


def retry(
    client: TestClient,
    first_result: dict,
    response: dict,
    *,
    arguments: dict | None = None,
    state: str | None = None,
    request_id: int = 11,
) -> tuple[int, dict]:
    input_key = next(iter(first_result["inputRequests"]))
    return request(
        client,
        "tools/call",
        {
            "name": "reservar_sala",
            "arguments": arguments
            or {
                "sala": "sala-garagem",
                "inicio": "2026-11-03T14:00:00-03:00",
                "fim": "2026-11-03T15:00:00-03:00",
                "responsavel": "Marty",
            },
            "inputResponses": {input_key: response},
            "requestState": state or first_result["requestState"],
        },
        request_id=request_id,
        header_name="reservar_sala",
    )


def test_retry_accepts_offered_room_and_decline_completes_without_reservation(client: TestClient) -> None:
    _, first_payload = conflict(client)
    status, accepted = retry(
        client,
        first_payload["result"],
        {"action": "accept", "content": {"sala": "sala-fusca"}},
    )
    assert status == 200
    assert accepted["result"]["resultType"] == "complete"
    assert accepted["result"]["structuredContent"]["sala"] == "sala-fusca"

    _, second_payload = conflict(client, request_id=12)
    _, declined = retry(client, second_payload["result"], {"action": "decline"}, request_id=13)
    assert declined["result"]["isError"] is False
    assert declined["result"]["structuredContent"]["reservado"] is False


def test_tampered_state_and_changed_arguments_cannot_take_effect(client: TestClient) -> None:
    _, first_payload = conflict(client)
    first = first_payload["result"]
    tampered = first["requestState"][:-6] + "AAAAAA"
    _, rejected = retry(
        client,
        first,
        {"action": "accept", "content": {"sala": "sala-fusca"}},
        state=tampered,
    )
    assert rejected["error"]["code"] == -32602

    changed = {
        "sala": "sala-mirante",
        "inicio": "2026-11-03T13:00:00-03:00",
        "fim": "2026-11-03T14:00:00-03:00",
        "responsavel": "Biff",
    }
    _, rejected = retry(
        client,
        first,
        {"action": "accept", "content": {"sala": "sala-fusca"}},
        arguments=changed,
        request_id=14,
    )
    assert rejected["error"]["code"] == -32602


def test_expired_but_authentic_state_is_rejected(client: TestClient) -> None:
    _, first_payload = conflict(client)
    first = first_payload["result"]
    security = RequestStateSecurity(keys=[bytes.fromhex("01" * 32)])
    claims = json.loads(security.codec.unseal(first["requestState"]))
    claims["exp"] = 0
    expired = security.codec.seal(json.dumps(claims, separators=(",", ":")).encode())
    _, rejected = retry(
        client,
        first,
        {"action": "accept", "content": {"sala": "sala-fusca"}},
        state=expired,
    )
    assert rejected["error"]["code"] == -32602

def test_short_secret_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REQUEST_STATE_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="32 bytes"):
        create_app(ROOT)


def test_stderr_records_method_id_and_traceparent(
    client: TestClient, capsys: pytest.CaptureFixture[str]
) -> None:
    traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    meta = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {"elicitation": {"form": {}}},
        "traceparent": traceparent,
    }
    request(client, "tools/list", {}, request_id=77, meta=meta)
    stderr = capsys.readouterr().err
    assert "method=tools/list" in stderr
    assert "id=77" in stderr
    assert traceparent in stderr
