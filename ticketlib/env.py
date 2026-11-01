"""Variables de entorno de Taiki y sus alias históricos ``TICKETS_*``.

El proyecto pasó de llamarse ``ticket`` a ``taiki``; para no romper scripts ni
hooks ya instalados, cada variable se lee primero con el prefijo nuevo y, si no
existe, con el antiguo. El prefijo nuevo siempre gana.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

PREFIX = "TAIKI_"
LEGACY_PREFIX = "TICKETS_"


def get(
    name: str,
    default: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Devuelve ``TAIKI_<name>`` o, en su defecto, ``TICKETS_<name>``."""
    env = os.environ if environ is None else environ
    for prefix in (PREFIX, LEGACY_PREFIX):
        value = env.get(prefix + name)
        if value:
            return value
    return default
