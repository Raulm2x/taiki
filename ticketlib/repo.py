"""Fachada pública del repositorio de tickets.

La implementación vive en `ticketlib.store`, dividida en consultas (queries),
mutaciones (mutations), analítica (analytics), relaciones (relations)
y flujo de trabajo (workflow). Este módulo mantiene un único punto de importación
preservando 100% de retrocompatibilidad.
"""

from .store import (
    activity_by_day,
    allocate_code,
    attach_tickets,
    auto_link_commit,
    claim_ticket,
    comment_ticket,
    complete_ticket,
    confirm_tickets,
    count_tickets,
    create_ticket,
    detach_tickets,
    fill_gaps,
    export_all,
    get_ticket,
    link_ticket,
    list_tickets,
    next_ticket,
    recent_events,
    renumber_children,
    renumber_plan,
    search_codes,
    stats,
    subtask_progress,
    transition,
    update_ticket,
)

__all__ = [
    "activity_by_day",
    "allocate_code",
    "attach_tickets",
    "auto_link_commit",
    "claim_ticket",
    "comment_ticket",
    "complete_ticket",
    "confirm_tickets",
    "count_tickets",
    "create_ticket",
    "detach_tickets",
    "fill_gaps",
    "export_all",
    "get_ticket",
    "link_ticket",
    "list_tickets",
    "next_ticket",
    "recent_events",
    "renumber_children",
    "renumber_plan",
    "search_codes",
    "stats",
    "subtask_progress",
    "transition",
    "update_ticket",
]
