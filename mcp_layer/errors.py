"""Errores MCP estructurados: código estable, mensaje y pista accionable."""

from __future__ import annotations

from typing import Any

from ticketlib import domain


def payload(exc: BaseException, tool: str | None = None) -> dict[str, Any]:
    """Convierte una excepción de dominio en un error consumible por agentes."""
    message = str(exc)
    if isinstance(exc, domain.StaleServerError):
        code, hint, retryable = (
            "stale_server",
            "Recarga el servidor MCP (el plugin lo hace al quedar idle) o reinicia OpenCode.",
            True,
        )
    elif isinstance(exc, domain.NotFoundError):
        code, hint, retryable = (
            "not_found",
            "Comprueba el código con list o get.",
            False,
        )
    elif isinstance(exc, domain.PermissionDenied):
        code, hint, retryable = (
            "forbidden",
            "La sesión no es la dueña. Usa force=true si la operación lo permite.",
            False,
        )
    elif isinstance(exc, domain.TransitionError):
        code, hint, retryable = (
            "conflict",
            "Consulta el estado con get; si el lease caducó usa steal=true, "
            "o force=true si corresponde.",
            True,
        )
    elif isinstance(exc, domain.ValidationError):
        code, hint, retryable = (
            "invalid_arguments",
            "Revisa los campos, tipos y valores permitidos en el esquema.",
            False,
        )
    else:
        code, hint, retryable = (
            "ticket_error",
            None,
            False,
        )

    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": retryable,
    }
    if hint:
        error["hint"] = hint
    if tool:
        error["tool"] = tool
    return {"error": error}


def internal(exc: BaseException, tool: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": "internal_error",
        "message": f"Error interno: {exc}",
        "retryable": True,
    }
    if tool:
        error["tool"] = tool
    return {"error": error}


__all__ = ["payload", "internal"]
