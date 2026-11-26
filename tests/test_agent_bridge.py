from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from bridge import parse_continuation, parse_reservation
from main import create_app


@dataclass
class FakeMCPClient:
    calls: list[tuple[str, dict, str | None]] = field(default_factory=list)
    initialized: int = 0

    async def initialize(self, traceparent: str | None = None) -> None:
        self.initialized += 1
        self.calls.append(("tools/list", {}, traceparent))
        self.calls.append(("resources/read", {"uri": "politica://uso"}, traceparent))

    async def call_tool(self, name: str, arguments: dict, traceparent: str | None = None, **extra) -> dict:
        self.calls.append((name, {"arguments": arguments, **extra}, traceparent))
        if arguments["sala"] == "sala-delorean":
            return {"resultType": "complete", "isError": True, "content": [{"text": "Sala inexistente: sala-delorean"}]}
        if arguments["sala"] == "sala-garagem" and "request_state" not in extra:
            return {
                "resultType": "input_required",
                "requestState": "PRIVATE-TOKEN",
                "inputRequests": {
                    "reserva:escolha_de_sala": {
                        "params": {"requestedSchema": {"properties": {"sala": {"enum": ["sala-fusca", "sala-mirante"]}}}}
                    }
                },
            }
        room = extra.get("choice", arguments["sala"])
        return {
            "resultType": "complete",
            "isError": False,
            "structuredContent": {
                "reserva": "res-teste",
                "sala": room,
                "inicio": arguments["inicio"],
                "fim": arguments["fim"],
                "responsavel": arguments["responsavel"],
                "politica": "2026-11-01",
            },
        }


def send(client: TestClient, text: str, task_id: str | None = None, trace: str | None = None) -> dict:
    message = {"messageId": "msg-1", "role": "ROLE_USER", "parts": [{"text": text}]}
    if task_id:
        message["taskId"] = task_id
    headers = {"traceparent": trace} if trace else {}
    return client.post(
        "/a2a",
        headers=headers,
        json={"jsonrpc": "2.0", "id": "rpc-1", "method": "SendMessage", "params": {"message": message}},
    ).json()


def test_fixed_parsers() -> None:
    parsed = parse_reservation(
        "reservar sala=sala-porao inicio=2026-11-03T09:00:00-03:00 fim=2026-11-03T10:00:00-03:00 responsavel=Doc"
    )
    assert parsed == {
        "sala": "sala-porao",
        "inicio": "2026-11-03T09:00:00-03:00",
        "fim": "2026-11-03T10:00:00-03:00",
        "responsavel": "Doc",
    }
    assert parse_reservation("reserve qualquer coisa") is None
    assert parse_continuation("escolha=sala-fusca") == "sala-fusca"
    assert parse_continuation("escolha=recusar") == "recusar"
    assert parse_continuation("talvez=sala-fusca") is None


def test_agent_card_uses_a2a_v1_shape() -> None:
    with TestClient(create_app(FakeMCPClient())) as client:
        card = client.get("/.well-known/agent-card.json").json()
    interface = card["supportedInterfaces"][0]
    assert interface["protocolBinding"] == "JSONRPC"
    assert interface["protocolVersion"].startswith("1.0")
    assert card["skills"][0]["id"] == "reservar-sala"


def test_complete_pause_invalid_resume_terminal_and_token_privacy() -> None:
    mcp = FakeMCPClient()
    trace = "00-11111111111111111111111111111111-2222222222222222-01"
    with TestClient(create_app(mcp)) as client:
        complete = send(
            client,
            "reservar sala=sala-porao inicio=2026-11-03T09:00:00-03:00 fim=2026-11-03T10:00:00-03:00 responsavel=Doc",
            trace=trace,
        )
        task = complete["result"]
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"
        assert json.loads(task["artifacts"][0]["parts"][0]["text"])["sala"] == "sala-porao"

        paused_response = send(
            client,
            "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Marty",
            trace=trace,
        )
        paused = paused_response["result"]
        assert paused["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert paused["status"]["message"]["parts"][0]["text"] == "alternativas: sala-fusca, sala-mirante"
        assert "PRIVATE-TOKEN" not in json.dumps(paused_response)
        calls_before = len(mcp.calls)
        invalid = send(client, "escolha=sala-aquario", paused["id"])
        assert invalid["result"]["status"]["state"] == "TASK_STATE_INPUT_REQUIRED"
        assert len(mcp.calls) == calls_before

        resumed = send(client, "escolha=sala-fusca", paused["id"])
        assert resumed["result"]["status"]["state"] == "TASK_STATE_COMPLETED"
        assert "PRIVATE-TOKEN" not in json.dumps(resumed)
        assert mcp.calls[-1][2] == trace
        terminal = send(client, "escolha=sala-mirante", paused["id"])
        assert terminal["error"]["code"] == -32001

        fetched = client.post(
            "/a2a", json={"jsonrpc": "2.0", "id": "rpc-2", "method": "GetTask", "params": {"id": task["id"]}}
        ).json()
        assert fetched["result"]["contextId"] == task["contextId"]

    assert mcp.initialized == 1
    assert [call[0] for call in mcp.calls[:2]] == ["tools/list", "resources/read"]


def test_decline_cancels_and_tool_error_fails() -> None:
    mcp = FakeMCPClient()
    with TestClient(create_app(mcp)) as client:
        paused = send(
            client,
            "reservar sala=sala-garagem inicio=2026-11-03T14:00:00-03:00 fim=2026-11-03T15:00:00-03:00 responsavel=Biff",
        )["result"]
        declined = send(client, "escolha=recusar", paused["id"])
        assert declined["result"]["status"]["state"] == "TASK_STATE_CANCELED"

        failed = send(
            client,
            "reservar sala=sala-delorean inicio=2026-11-03T09:00:00-03:00 fim=2026-11-03T10:00:00-03:00 responsavel=Doc",
        )["result"]
        assert failed["status"]["state"] == "TASK_STATE_FAILED"
        assert "Sala inexistente" in failed["status"]["message"]["parts"][0]["text"]
