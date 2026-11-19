#!/usr/bin/env python3
"""Servidor MCP del sistema de tickets (transporte stdio, JSON-RPC)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ticketlib import __version__, db, domain, render, runtime, servers, workspace
from mcp_layer import errors, handlers, schemas

SERVER_NAME = "taiki"


def _server_version() -> str:
    """Versión del código en disco (tag/commit) con la versión de paquete."""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(runtime.ROOT),
                "describe",
                "--tags",
                "--always",
                "--dirty",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except OSError:
        pass
    return __version__


SERVER_VERSION = _server_version()
MAX_REQUEST_BYTES = 4 * 1024 * 1024
LATEST_PROTOCOL = "2025-11-25"
SUPPORTED_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")

# Recarga por señal: SIGHUP solo marca una bandera y el re-exec ocurre entre
# peticiones, con la respuesta ya escrita. El testigo del entorno cuenta la
# cadena de re-execuciones heredada vía ``execv`` para cortar bucles si el
# disco no converge.
REEXEC_ENV = "TAIKI_MCP_REEXEC"
REEXEC_MAX = 3
_reload_requested = False


class RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


def _send(payload: dict) -> None:
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    sys.stdout.flush()


def _reply(rid, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _reply_error(rid, code: int, message: str) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}})


def _tool_result(data: dict) -> dict:
    # Una sola representación: JSON compacto en ``content``. ``structuredContent``
    # duplicaba todo el payload y los clientes MCP pueden parsear el texto.
    return {
        "content": [{"type": "text", "text": render.to_json(data, compact=True)}],
    }


def _tool_error(payload: dict) -> dict:
    error = payload.get("error") or {}
    text = str(error.get("message") or payload)
    hint = error.get("hint")
    if hint:
        text += f"\n{hint}"
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": True,
    }


def _register_runtime() -> None:
    """Deja constancia del arranque para detectar MCP desactualizados.

    La huella y el estado de la sesión viven en la DB del proyecto, pero el
    inventario de procesos es global: así varias instancias (una por proyecto)
    no se pisan ni comparten contadores.
    """
    try:
        info = workspace.resolve()
        conn = db.connect()
        try:
            runtime.register(conn, SERVER_VERSION)
        finally:
            conn.close()
        registry = db.connect(workspace.registry_path())
        try:
            servers.register(
                registry,
                "taiki",
                SERVER_VERSION,
                workspace=str(info["workspace"] or info["db"]),
            )
        finally:
            registry.close()
    except Exception:  # noqa: BLE001 - el handshake nunca debe romper el MCP
        pass


def _request_reload(_signum: int, _frame: object) -> None:
    """Manejador de SIGHUP: solo marca la bandera, nunca trabaja en la señal."""
    global _reload_requested
    _reload_requested = True


def install_reload_handler() -> bool:
    """Instala el manejador de SIGHUP (solo POSIX); ``False`` si no se puede."""
    if not hasattr(signal, "SIGHUP"):
        return False
    try:
        signal.signal(signal.SIGHUP, _request_reload)
    except (OSError, ValueError):  # hilo no principal o plataforma restrictiva
        return False
    return True


def _reexec_count() -> int:
    """Re-execuciones encadenadas que ya vivió este proceso (vía ``execv``)."""
    try:
        return int(os.environ.get(REEXEC_ENV, "") or "0")
    except ValueError:
        return 0


def maybe_reexec() -> bool:
    """Re-ejecuta el servidor con el código de disco; solo entre peticiones.

    El bucle principal lo llama justo después de escribir una respuesta: nunca
    a mitad de una petición. ``os.execv`` sustituye el proceso heredando los
    fds de stdio, así el cliente no ve el relevo. Solo se sustituye si el
    disco trae algo nuevo (una señal repetida para el mismo cambio no vuelve a
    re-ejecutar) y la cadena de re-execuciones no agotó el límite.
    """
    global _reload_requested
    if not _reload_requested:
        return False
    _reload_requested = False
    if not runtime.is_stale():
        print(
            "taiki: recarga pedida sin cambios en el código; se ignora",
            file=sys.stderr,
            flush=True,
        )
        return False
    count = _reexec_count()
    if count >= REEXEC_MAX:
        print(
            f"taiki: recarga cancelada: {REEXEC_MAX} re-exec encadenados sin "
            "converger; reinicia OpenCode si el MCP sigue desactualizado",
            file=sys.stderr,
            flush=True,
        )
        return False
    os.environ[REEXEC_ENV] = str(count + 1)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(sys.executable, [sys.executable, *sys.argv])
    except OSError as exc:
        os.environ.pop(REEXEC_ENV, None)
        print(
            f"taiki: no se pudo re-ejecutar ({exc}); "
            "se sigue con el código actual",
            file=sys.stderr,
            flush=True,
        )
        return False
    return True  # inalcanzable: execv no vuelve


def _startup() -> None:
    """Arranque o relevo por re-exec: señal, testigo y huella registrada."""
    install_reload_handler()
    # Una cadena de recargas que llegó a buen término dejó el código al día:
    # el testigo vuelve a cero para no agotar el límite en la vida del proceso.
    if not runtime.is_stale():
        os.environ.pop(REEXEC_ENV, None)
    # Tras un re-exec el cliente no repite `initialize`: la huella del proceso
    # nuevo debe constar aquí (runtime.register), no solo en el handshake.
    _register_runtime()


def _heartbeat(params: dict) -> None:
    """Marca actividad del MCP; informativo, nunca decide si es huérfano."""
    try:
        session = handlers.resolve_session(params.get("_meta"), params.get("arguments"))
        registry = db.connect(workspace.registry_path())
        try:
            servers.heartbeat(registry, connection=session)
        finally:
            registry.close()
    except Exception:  # noqa: BLE001 - el latido nunca debe romper la petición
        pass


def _initialize(params: dict) -> dict:
    _register_runtime()
    handlers.set_client_info(params.get("clientInfo"))
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
    return {
        "protocolVersion": version,
        "capabilities": {
            "tools": {"listChanged": False},
            "resources": {"subscribe": False, "listChanged": False},
            "prompts": {"listChanged": False},
        },
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": schemas.INSTRUCTIONS,
    }


def _dispatch(method: str, params: dict) -> dict:
    if method == "initialize":
        return _initialize(params)
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": schemas.TOOLS}
    if method == "tools/call":
        _heartbeat(params)
        tool = params.get("name", "")
        try:
            data = handlers.call_tool(
                tool,
                params.get("arguments") or {},
                params.get("_meta"),
            )
        except domain.TicketError as exc:
            return _tool_error(errors.payload(exc, tool=tool))
        except Exception as exc:  # noqa: BLE001 - se reporta al agente
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            return _tool_error(errors.internal(exc, tool=tool))
        return _tool_result(data)
    if method == "resources/list":
        return {"resources": schemas.RESOURCES}
    if method == "resources/templates/list":
        return {"resourceTemplates": schemas.RESOURCE_TEMPLATES}
    if method == "resources/read":
        try:
            return handlers.read_resource(params.get("uri", ""))
        except domain.TicketError as exc:
            raise RpcError(-32602, str(exc)) from exc
    if method == "prompts/list":
        return {"prompts": schemas.PROMPTS}
    if method == "prompts/get":
        try:
            return handlers.get_prompt(
                params.get("name", ""), params.get("arguments") or {}
            )
        except domain.TicketError as exc:
            raise RpcError(-32602, str(exc)) from exc
    if method == "logging/setLevel":
        return {}
    raise RpcError(-32601, f"Método no soportado: {method}")


def main() -> int:
    _startup()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if len(line.encode("utf-8")) > MAX_REQUEST_BYTES:
            _reply_error(None, -32600, "Solicitud demasiado grande")
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _reply_error(None, -32700, "JSON inválido")
            continue
        if not isinstance(message, dict):
            continue
        rid = message.get("id")
        if rid is None:
            continue  # notificación
        method = message.get("method")
        if not method:
            _reply_error(rid, -32600, "Solicitud inválida")
            continue
        try:
            result = _dispatch(method, message.get("params") or {})
        except RpcError as exc:
            _reply_error(rid, exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001 - el servidor no debe caerse
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            _reply_error(rid, -32603, f"Error interno: {exc}")
        else:
            _reply(rid, result)
        # Punto seguro entre peticiones: la respuesta ya está escrita y un
        # re-exec aquí no pierde nada en el pipe.
        maybe_reexec()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
