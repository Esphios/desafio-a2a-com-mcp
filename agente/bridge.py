from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from typing import Any


RESERVATION_RE = re.compile(
    r"^reservar sala=(?P<sala>\S+) inicio=(?P<inicio>\S+) fim=(?P<fim>\S+) responsavel=(?P<responsavel>\S+)$"
)
CONTINUATION_RE = re.compile(r"^escolha=(?P<choice>\S+)$")
TERMINAL = {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED"}


def parse_reservation(text: str) -> dict[str, str] | None:
    match = RESERVATION_RE.fullmatch(text.strip())
    return match.groupdict() if match else None


def parse_continuation(text: str) -> str | None:
    match = CONTINUATION_RE.fullmatch(text.strip())
    return match.group("choice") if match else None


@dataclass
class PausedCall:
    request_state: str
    input_key: str
    alternatives: list[str]
    arguments: dict[str, str]
    traceparent: str | None


def text_message(text: str, role: str = "ROLE_AGENT") -> dict[str, Any]:
    return {
        "messageId": f"msg-{secrets.token_hex(8)}",
        "role": role,
        "parts": [{"text": text}],
    }


class Bridge:
    def __init__(self, mcp: Any) -> None:
        self.mcp = mcp
        self.tasks: dict[str, dict[str, Any]] = {}
        self.paused: dict[str, PausedCall] = {}
        self._initialized = False

    async def initialize(self, traceparent: str | None = None) -> None:
        if not self._initialized:
            await self.mcp.initialize(traceparent)
            self._initialized = True

    def _new_task(self, user_message: dict[str, Any]) -> dict[str, Any]:
        task_id = f"task-{secrets.token_hex(12)}"
        task = {
            "id": task_id,
            "contextId": f"ctx-{secrets.token_hex(12)}",
            "status": {"state": "TASK_STATE_SUBMITTED"},
            "history": [user_message],
            "artifacts": [],
        }
        self.tasks[task_id] = task
        return task

    @staticmethod
    def _set_status(task: dict[str, Any], state: str, message: str) -> None:
        agent_message = text_message(message)
        task["status"] = {"state": state, "message": agent_message}
        task["history"].append(agent_message)

    @staticmethod
    def _result_text(result: dict[str, Any]) -> str:
        return " ".join(part.get("text", "") for part in result.get("content", []))

    def _complete(self, task: dict[str, Any], result: dict[str, Any]) -> None:
        data = dict(result.get("structuredContent") or {})
        policy_version = getattr(self.mcp, "policy_version", None)
        if policy_version:
            data["politica"] = policy_version
        task["artifacts"] = [{"artifactId": f"artifact-{secrets.token_hex(8)}", "name": "reserva", "parts": [{"text": json.dumps(data, ensure_ascii=False)}]}]
        self._set_status(task, "TASK_STATE_COMPLETED", "reserva concluida")

    def _pause(self, task: dict[str, Any], result: dict[str, Any], arguments: dict[str, str], traceparent: str | None) -> None:
        input_key = next(iter(result["inputRequests"]))
        schema = result["inputRequests"][input_key]["params"]["requestedSchema"]
        room = schema["properties"]["sala"]
        alternatives = room.get("enum") or [room["const"]]
        self.paused[task["id"]] = PausedCall(result["requestState"], input_key, alternatives, arguments, traceparent)
        self._set_status(task, "TASK_STATE_INPUT_REQUIRED", f"alternativas: {', '.join(alternatives)}")

    async def send_message(self, message: dict[str, Any], traceparent: str | None) -> dict[str, Any]:
        await self.initialize(traceparent)
        text = " ".join(part.get("text", "") for part in message.get("parts", []))
        task_id = message.get("taskId")
        if task_id:
            return await self._continue(task_id, text, message, traceparent)
        task = self._new_task(message)
        arguments = parse_reservation(text)
        if arguments is None:
            self._set_status(task, "TASK_STATE_FAILED", "Formato invalido")
            return task
        task["status"] = {"state": "TASK_STATE_WORKING"}
        try:
            result = await self.mcp.call_tool("reservar_sala", arguments, traceparent)
        except Exception as exc:
            self._set_status(task, "TASK_STATE_FAILED", str(exc))
            return task
        if result.get("isError"):
            self._set_status(task, "TASK_STATE_FAILED", self._result_text(result))
        elif result.get("resultType") == "input_required":
            self._pause(task, result, arguments, traceparent)
        else:
            self._complete(task, result)
        return task

    async def _continue(
        self, task_id: str, text: str, user_message: dict[str, Any], traceparent: str | None
    ) -> dict[str, Any]:
        task = self.tasks.get(task_id)
        if task is None:
            raise ValueError("Task inexistente")
        if task["status"]["state"] in TERMINAL:
            raise RuntimeError("Task terminal nao pode ser retomada")
        paused = self.paused.get(task_id)
        if paused is None:
            raise RuntimeError("Task nao aguarda entrada")
        task["history"].append(user_message)
        choice = parse_continuation(text)
        if choice is None or (choice != "recusar" and choice not in paused.alternatives):
            self._set_status(task, "TASK_STATE_INPUT_REQUIRED", f"alternativas: {', '.join(paused.alternatives)}")
            return task
        effective_trace = paused.traceparent or traceparent
        try:
            result = await self.mcp.call_tool(
                "reservar_sala",
                paused.arguments,
                effective_trace,
                request_state=paused.request_state,
                input_key=paused.input_key,
                choice=None if choice == "recusar" else choice,
                decline=choice == "recusar",
            )
        except Exception as exc:
            self._set_status(task, "TASK_STATE_FAILED", str(exc))
            self.paused.pop(task_id, None)
            return task
        self.paused.pop(task_id, None)
        if choice == "recusar":
            self._set_status(task, "TASK_STATE_CANCELED", "reserva recusada")
        elif result.get("isError"):
            self._set_status(task, "TASK_STATE_FAILED", self._result_text(result))
        else:
            self._complete(task, result)
        return task
