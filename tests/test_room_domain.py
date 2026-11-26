from __future__ import annotations

from pathlib import Path

import pytest

from room_domain import DomainError, RoomStore


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def store() -> RoomStore:
    return RoomStore(ROOT / "dados")


def test_loads_supplied_rooms_reservations_and_policy(store: RoomStore) -> None:
    assert [room["id"] for room in store.rooms] == [
        "sala-aquario",
        "sala-porao",
        "sala-garagem",
        "sala-fusca",
        "sala-mirante",
    ]
    assert [reservation["id"] for reservation in store.reservations] == ["res-0001", "res-0002"]
    assert store.policy_version == "2026-11-01"


@pytest.mark.parametrize(
    ("room", "start", "end", "message"),
    [
        ("sala-delorean", "2026-11-03T09:00:00-03:00", "2026-11-03T10:00:00-03:00", "Sala inexistente: sala-delorean"),
        ("sala-aquario", "2026-11-03T07:00:00-03:00", "2026-11-03T08:00:00-03:00", "Fora da janela de uso: a politica permite reservas entre 08:00 e 20:00"),
        ("sala-aquario", "2026-11-03T09:00:00-03:00", "2026-11-03T12:00:00-03:00", "Duracao acima do limite: a politica permite no maximo 2 horas"),
        ("sala-aquario", "2026-11-03T10:00:00-03:00", "2026-11-03T09:00:00-03:00", "Intervalo invalido: fim deve ser posterior a inicio"),
    ],
)
def test_rejects_invalid_reservation_with_exact_message(
    store: RoomStore, room: str, start: str, end: str, message: str
) -> None:
    with pytest.raises(DomainError, match=f"^{message}$"):
        store.availability(room, start, end)


def test_reports_conflict_and_new_reservations_are_visible(store: RoomStore) -> None:
    conflict = store.availability(
        "sala-garagem", "2026-11-03T14:00:00-03:00", "2026-11-03T15:00:00-03:00"
    )
    assert conflict == {
        "sala": "sala-garagem",
        "livre": False,
        "conflitos": [
            {
                "id": "res-0001",
                "inicio": "2026-11-03T14:00:00-03:00",
                "fim": "2026-11-03T15:00:00-03:00",
                "responsavel": "Marty",
            }
        ],
    }

    created = store.reserve(
        "sala-aquario",
        "2026-11-03T09:00:00-03:00",
        "2026-11-03T10:00:00-03:00",
        "Doc",
    )
    assert created == {
        "reserva": "res-0003",
        "reservado": True,
        "sala": "sala-aquario",
        "inicio": "2026-11-03T09:00:00-03:00",
        "fim": "2026-11-03T10:00:00-03:00",
        "responsavel": "Doc",
        "politica": "2026-11-01",
        "motivo": None,
    }
    assert store.availability(
        "sala-aquario", "2026-11-03T09:00:00-03:00", "2026-11-03T10:00:00-03:00"
    )["livre"] is False


def test_alternatives_follow_capacity_id_and_limit_rules(store: RoomStore) -> None:
    assert store.alternatives(
        "sala-garagem", "2026-11-03T14:00:00-03:00", "2026-11-03T15:00:00-03:00"
    ) == ["sala-fusca", "sala-mirante"]


def test_reserve_rejects_conflict_instead_of_choosing_domain_alternative(store: RoomStore) -> None:
    with pytest.raises(DomainError, match="^Sala ocupada$"):
        store.reserve(
            "sala-garagem",
            "2026-11-03T14:00:00-03:00",
            "2026-11-03T15:00:00-03:00",
            "Marty",
        )
