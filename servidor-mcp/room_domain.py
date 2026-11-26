from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any


class DomainError(ValueError):
    pass


class RoomStore:
    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self.rooms: list[dict[str, Any]] = json.loads((data_dir / "salas.json").read_text(encoding="utf-8"))
        self.reservations: list[dict[str, Any]] = json.loads(
            (data_dir / "reservas.json").read_text(encoding="utf-8")
        )
        self.policy_text = (data_dir / "politica-de-uso.md").read_text(encoding="utf-8")
        self.policy_version = self.policy_text.splitlines()[0].split(":", 1)[1].strip()
        self._rooms_by_id = {room["id"]: room for room in self.rooms}

    def availability(self, room: str, start: str, end: str) -> dict[str, Any]:
        start_at, end_at = self._validate(room, start, end)
        conflicts = []
        for reservation in self.reservations:
            if reservation["sala"] != room:
                continue
            existing_start = datetime.fromisoformat(reservation["inicio"])
            existing_end = datetime.fromisoformat(reservation["fim"])
            if start_at < existing_end and end_at > existing_start:
                conflicts.append(
                    {
                        "id": reservation["id"],
                        "inicio": reservation["inicio"],
                        "fim": reservation["fim"],
                        "responsavel": reservation["responsavel"],
                    }
                )
        return {"sala": room, "livre": not conflicts, "conflitos": conflicts}

    def alternatives(self, room: str, start: str, end: str) -> list[str]:
        self._validate(room, start, end)
        minimum_capacity = self._rooms_by_id[room]["capacidade"]
        candidates = [
            candidate
            for candidate in self.rooms
            if candidate["id"] != room
            and candidate["capacidade"] >= minimum_capacity
            and self.availability(candidate["id"], start, end)["livre"]
        ]
        candidates.sort(key=lambda candidate: (candidate["capacidade"], candidate["id"]))
        return [candidate["id"] for candidate in candidates[:3]]

    def reserve(self, room: str, start: str, end: str, responsible: str) -> dict[str, Any]:
        if not self.availability(room, start, end)["livre"]:
            raise DomainError("Sala ocupada")
        next_number = max(int(item["id"].split("-")[1]) for item in self.reservations) + 1
        reservation_id = f"res-{next_number:04d}"
        self.reservations.append(
            {
                "id": reservation_id,
                "sala": room,
                "inicio": start,
                "fim": end,
                "responsavel": responsible,
            }
        )
        return {
            "reserva": reservation_id,
            "reservado": True,
            "sala": room,
            "inicio": start,
            "fim": end,
            "responsavel": responsible,
            "politica": self.policy_version,
            "motivo": None,
        }

    def _validate(self, room: str, start: str, end: str) -> tuple[datetime, datetime]:
        if room not in self._rooms_by_id:
            raise DomainError(f"Sala inexistente: {room}")
        try:
            start_at = datetime.fromisoformat(start)
            end_at = datetime.fromisoformat(end)
        except ValueError as exc:
            raise DomainError("Intervalo invalido: fim deve ser posterior a inicio") from exc
        if end_at <= start_at:
            raise DomainError("Intervalo invalido: fim deve ser posterior a inicio")
        if end_at - start_at > timedelta(hours=2):
            raise DomainError("Duracao acima do limite: a politica permite no maximo 2 horas")
        if start_at.timetz().replace(tzinfo=None) < time(8) or end_at.timetz().replace(tzinfo=None) > time(20):
            raise DomainError("Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00")
        return start_at, end_at
