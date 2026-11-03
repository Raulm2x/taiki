"""Export/import versionable y rollback de la memoria.

El snapshot JSON es la fuente de verdad: incluye memorias, playbooks, ficheros,
evidencia, propuestas y relaciones, con versión y checksum determinista. El
Markdown es una vista humana que embebe el mismo JSON en un comentario, así que
también se puede reimportar sin pérdida. Ni la exportación ni la importación
borran filas: los conflictos y el rollback se resuelven con estados y ledger.

La implementación se reparte por dominio en ``export_document`` (documento y
checksum), ``export_import`` (estrategias de importación) y ``export_rollback``
(rollback por ledger o snapshot); este módulo conserva la API pública.
"""

from __future__ import annotations

from .export_document import VERSION, export_snapshot
from .export_import import import_snapshot
from .export_rollback import rollback

__all__ = ["VERSION", "export_snapshot", "import_snapshot", "rollback"]
