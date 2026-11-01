"""Resumen lean compartido por el CLI y el MCP."""

from __future__ import annotations

from typing import Any

SUMMARY_FIELDS = (
    "code",
    "title",
    "status",
    "priority",
    "kind",
    "project",
    "assigned_to",
    "parent_id",
    "updated_at",
)

SUMMARY_OPTIONAL = ("paths", "tags", "progress")


def summary(ticket: dict[str, Any]) -> dict[str, Any]:
    """Campos mínimos para elegir trabajo, sin body ni historial."""
    data = {key: ticket[key] for key in SUMMARY_FIELDS if key in ticket}
    for key in SUMMARY_OPTIONAL:
        value = ticket.get(key)
        if not value:
            continue
        if key == "progress" and not (value or {}).get("total"):
            continue
        data[key] = value
    return data


__all__ = ["SUMMARY_FIELDS", "SUMMARY_OPTIONAL", "summary"]
