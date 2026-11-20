"""Presupuesto de caracteres por scope y prioridad de las memorias."""

from __future__ import annotations

from typing import Any, Callable, Iterable

from ..domain import ValidationError
from ..util import parse_datetime
from .models import BUDGET_STATUSES, PLAYBOOK_KIND


class BudgetError(ValidationError):
    """El batch de memorias supera el presupuesto del scope."""

    def __init__(
        self,
        scope: str,
        limit: int,
        entries: list[dict[str, Any]],
        *,
        used: int,
    ) -> None:
        self.scope = scope
        self.limit = limit
        self.used = used
        self.chars_to_free = max(0, used - limit)
        self.current_entries = [entry_summary(entry) for entry in entries]
        listing = ", ".join(
            f"{item['code']} ({item['chars']})" for item in self.current_entries
        )
        super().__init__(
            f"Presupuesto de memoria '{scope}' excedido: {used}/{limit} "
            f"caracteres. Libera al menos {self.chars_to_free} caracteres. "
            f"Entradas actuales: {listing or 'ninguna'}."
        )


def entry_chars(memory: dict[str, Any]) -> int:
    """Caracteres que consume una entrada (su contenido destilado)."""
    return len(str(memory.get("content") or ""))


def entry_summary(memory: dict[str, Any]) -> dict[str, Any]:
    """Resumen accionable de una entrada para errores y avisos."""
    return {
        "code": memory.get("code"),
        "title": memory.get("title"),
        "chars": entry_chars(memory),
        "status": memory.get("status"),
        "scope": memory.get("scope"),
        "pinned": bool(memory.get("pinned") or memory.get("locked")),
    }


def _timestamp(memory: dict[str, Any]) -> float:
    parsed = parse_datetime(memory.get("updated_at") or memory.get("created_at"))
    return parsed.timestamp() if parsed is not None else 0.0


def priority_key(memory: dict[str, Any]) -> tuple[Any, ...]:
    """Orden de relevancia: pinned/locked > user > confianza > recencia > uso."""
    return (
        0 if (memory.get("pinned") or memory.get("locked")) else 1,
        0 if memory.get("scope") == "user" else 1,
        -float(memory.get("confidence") or 0.0),
        -_timestamp(memory),
        -int(memory.get("use_count") or 0),
        str(memory.get("code") or ""),
    )


def budget_entries(memories: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Entradas que consumen presupuesto, ordenadas por prioridad.

    Los playbooks quedan fuera: se cargan de forma progresiva (índice en el
    brief, contenido bajo demanda) y su cuerpo no debe desplazar a las
    memorias destiladas ni romper el techo del scope.
    """
    entries = [
        memory
        for memory in memories
        if memory.get("status") in BUDGET_STATUSES
        and memory.get("kind") != PLAYBOOK_KIND
    ]
    return sorted(entries, key=priority_key)


def summary(
    memories: Iterable[dict[str, Any]],
    limit: int,
    scope: str,
) -> dict[str, Any]:
    """Resumen del presupuesto de un scope."""
    entries = budget_entries(memories)
    used = sum(entry_chars(memory) for memory in entries)
    return {
        "scope": scope,
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "chars_to_free": max(0, used - limit),
        "entries": len(entries),
    }


def enforce(
    memories: Iterable[dict[str, Any]],
    limit: int,
    scope: str,
) -> dict[str, Any]:
    """Comprueba el resultado de un batch completo, no una operación suelta."""
    entries = budget_entries(memories)
    used = sum(entry_chars(memory) for memory in entries)
    if used > limit:
        raise BudgetError(scope, limit, entries, used=used)
    return summary(entries, limit, scope)


def select_for_budget(
    memories: Iterable[dict[str, Any]],
    limit: int,
    size: Callable[[dict[str, Any]], int] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Selecciona por prioridad las entradas que caben en el brief.

    Devuelve ``(seleccionadas, descartadas, caracteres_usados)``. Si una
    entrada de alta prioridad no cabe, se prueba con las siguientes en vez de
    detenerse: el brief debe llenarse con lo que quepa. ``size`` permite medir
    lo que la entrada ocupará en el brief (por ejemplo la línea índice) en vez
    del contenido destilado.
    """
    measure = size or entry_chars
    selected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    used = 0
    for memory in budget_entries(memories):
        chars = measure(memory)
        if used + chars <= limit:
            selected.append(memory)
            used += chars
        else:
            dropped.append(memory)
    return selected, dropped, used


__all__ = [
    "BudgetError",
    "budget_entries",
    "enforce",
    "entry_chars",
    "entry_summary",
    "priority_key",
    "select_for_budget",
    "summary",
]
