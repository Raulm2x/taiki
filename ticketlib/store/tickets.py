"""Fachada de retrocompatibilidad para ticketlib.store.tickets.

Re-exporta las funciones de almacenamiento divididas en queries,
mutations y analytics.
"""

from __future__ import annotations

from .analytics import export_all, stats
from .mutations import create_ticket, update_ticket
from .queries import get_ticket, list_tickets, next_ticket

__all__ = [
    "create_ticket",
    "export_all",
    "get_ticket",
    "list_tickets",
    "next_ticket",
    "stats",
    "update_ticket",
]
