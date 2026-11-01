"""Heurística de comandos y rutas: ¿esto escribe en el HOME?"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

HOME = Path.home()
EXEMPT_ROOTS = tuple(
    Path(item)
    for item in (
        "/tmp",
        "/var/tmp",
        "/dev/shm",
        "/run",
        "/proc",
        "/sys",
        "/usr",
        "/opt",
        "/var/cache",
    )
)

# Comandos que modifican el sistema de ficheros o el estado de la máquina.
_MUTATING = re.compile(
    r"""
    (?: ^ | [;&|()] | \bsudo\s+ )
    (?:
        rm | rmdir | mv | cp | mkdir | touch | chmod | chown | ln |
        install | tee | truncate | dd | rsync | scp | unlink | shred
    ) \b
    | \bsed\s+(?:-\w+\s+)*-\w*i
    | \bfind\b[^;&|]*\s-(?:delete|exec)\b
    | \bxargs\b[^;&|]*\b(?:rm|rmdir|mv|cp|touch|mkdir|sed|tee|shred|unlink)\b
    | \bgit\s+(?:apply|am|rebase|merge|reset|checkout|switch|stash|clean|
                  cherry-pick|revert|init)\b
    | \bsystemctl\s+(?:--user\s+)?(?:start|stop|restart|enable|disable|reload|
                                   daemon-reload|mask|unmask)\b
    | \bhyprctl\s+(?:keyword|reload|dispatch|setprop|output)\b
    | \bnoctalia\s+msg\b
    | \b(?:pacman|yay|paru|pip|pipx|npm|pnpm|yarn|cargo|uv)\s+
        (?:install|add|remove|uninstall|-S|-Syu|-R)\b
    """,
    re.VERBOSE,
)

# Comandos de lectura o que no tocan el árbol de trabajo: nunca piden ticket.
_SAFE_COMMANDS = {
    "taiki",
    "ps",
    "grep",
    "rg",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "sort",
    "uniq",
    "jq",
    "echo",
    "printf",
    "date",
    "pwd",
    "whoami",
    "id",
    "uname",
    "which",
    "type",
    "tmux",
    "diff",
    "cmp",
    "basename",
    "dirname",
    "realpath",
    "readlink",
    "stat",
    "file",
    "cut",
    "tr",
    "nl",
    "true",
    "false",
    "test",
    "[",
    "sleep",
    "cd",
}

# Subcomandos de git que publican o consultan sin escribir en el árbol.
_SAFE_GIT = {
    "add",
    "commit",
    "push",
    "tag",
    "fetch",
    "status",
    "diff",
    "log",
    "show",
    "rev-parse",
    "branch",
    "remote",
    "ls-files",
    "blame",
    "describe",
    "reflog",
    "shortlog",
    "config",
    "merge-base",
}

# Prefijos que se descartan para encontrar el comando real.
_PREFIX_COMMANDS = {"sudo", "env", "time", "timeout", "command", "nohup", "exec", "builtin"}

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Redirección a un fichero (ignora 2>&1, >/dev/null y comparaciones).
_REDIRECT = re.compile(r"(?<![-=<>0-9])>>?\s*(?!/dev/null)\S+")

# Escrituras dentro de scripts (python/heredocs y code mode).
_SCRIPT_WRITE = re.compile(
    r"write_text\s*\(|write_bytes\s*\(|open\s*\([^)]*['\"][wa]\+?['\"]"
    r"|os\.remove\s*\(|shutil\.(?:copy|move|rmtree)\s*\("
    r"|tools\.(?:write|edit|shell|bash)\b|\bfs\.(?:write|unlink|mkdir|rename)"
    r"|\bwriteFileSync\s*\(|\bunlinkSync\s*\("
)

_ABSOLUTE = re.compile(r"(?<![\w~])(?:/[\w.@%+,:=-]+)+")


def path_guarded(path: str | Path | None) -> bool:
    """Solo se vigilan escrituras dentro del HOME y fuera de rutas efímeras."""
    if not path:
        return True
    try:
        target = Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        target = Path(path).expanduser().absolute()
    if target != HOME and HOME not in target.parents:
        return False
    return not any(target == root or root in target.parents for root in EXEMPT_ROOTS)


def command_touches_home(command: str, cwd: str | Path | None = None) -> bool:
    """Distingue un comando de scratch (/tmp) de uno que toca el HOME.

    Sin rutas absolutas se asume que escribe en el directorio del proyecto.
    """
    paths = [item for item in _ABSOLUTE.findall(command) if len(item) > 1]
    if not paths:
        return True
    home = str(HOME)
    inside = [item for item in paths if item == home or item.startswith(home + "/")]
    if inside:
        return True
    exempt = tuple(str(root) for root in EXEMPT_ROOTS)
    return not all(item.startswith(exempt) for item in paths)


def risky_git(command: str) -> str | None:
    """Operación Git de alcance global que puede arrastrar cambios ajenos.

    Con varias sesiones sobre el mismo checkout, ``git add -A``, ``git stash``
    o ``git reset`` mezclan el trabajo de todas. Devuelve una etiqueta corta
    (``"git add -A"``) o ``None`` si el comando está acotado a rutas.
    """
    for segment in split_commands(command or ""):
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()
        if "git" not in tokens:
            continue
        rest = tokens[tokens.index("git") + 1 :]
        while rest and rest[0].startswith("-"):
            rest = rest[1:]
        if not rest:
            continue
        sub, args = rest[0], rest[1:]
        if sub == "add":
            if not args or any(
                arg in {"-A", "--all", "-a", "-u", "--update", "."}
                for arg in args
            ):
                return "git add -A"
        elif sub == "commit":
            if any(
                arg in {"-a", "--all"}
                or (
                    arg.startswith("-")
                    and not arg.startswith("--")
                    and "a" in arg[1:]
                )
                for arg in args
            ):
                return "git commit -a"
        elif sub in {"stash", "reset", "clean"}:
            return f"git {sub}"
        elif sub == "checkout" and "--" in args:
            tail = args[args.index("--") + 1 :]
            if not tail or "." in tail:
                return "git checkout -- ."
        elif sub == "restore" and (not args or "." in args):
            return "git restore ."
    return None


def _strip_heredocs(text: str) -> str:
    """Elimina el cuerpo de los heredocs (puede contener ``>`` o ``;``)."""
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        out.append(line)
        index += 1
        match = _HEREDOC.search(line)
        if not match:
            continue
        tag = match.group(2)
        while index < len(lines) and lines[index].strip() != tag:
            index += 1
        if index < len(lines):
            index += 1  # salta el terminador
    return "\n".join(out)


def _mask_quotes(text: str) -> str:
    """Sustituye el contenido entre comillas por ``Q``.

    Así un ``>`` dentro de una cadena de Python no se confunde con una
    redirección, pero ``> "fichero"`` sigue detectándose por el ``>``.
    """
    out: list[str] = []
    quote: str | None = None
    for char in text:
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"":
            quote = char
            out.append("Q")
            continue
        out.append(char)
    return "".join(out)


def split_commands(command: str) -> list[str]:
    """Trocea un comando por ``;``, ``&&``, ``||``, ``|`` y saltos de línea."""
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    text = command or ""
    while index < len(text):
        char = text[index]
        if escaped:
            current.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\":
            current.append(char)
            escaped = True
            index += 1
            continue
        if quote:
            current.append(char)
            if char == quote:
                quote = None
            index += 1
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue
        if text.startswith("&&", index) or text.startswith("||", index):
            segments.append("".join(current))
            current = []
            index += 2
            continue
        if char in ";|\n":
            segments.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    segments.append("".join(current))
    return [item for item in (part.strip() for part in segments) if item]


def _tokens(segment: str) -> list[str]:
    try:
        return shlex.split(segment, comments=False, posix=True)
    except ValueError:
        return segment.split()


def _base_command(segment: str) -> tuple[str, list[str]]:
    """Comando real y argumentos, saltando prefijos y asignaciones de entorno."""
    tokens = _tokens(segment)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _PREFIX_COMMANDS:
            index += 1
            if token in ("time", "timeout") and index < len(tokens):
                index += 1  # duración de timeout
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            index += 1
            continue
        break
    if index >= len(tokens):
        return "", []
    return tokens[index], tokens[index + 1 :]


def _is_safe(segment: str) -> bool:
    """``True`` si el segmento solo consulta o publica."""
    base, rest = _base_command(segment)
    if not base:
        return True
    if base == "git":
        args = list(rest)
        while args and args[0].startswith("-"):
            option = args.pop(0)
            if option in ("-C", "--git-dir", "--work-tree") and args:
                args.pop(0)
        subcommand = args[0] if args else ""
        if subcommand == "stash":
            action = args[1] if len(args) > 1 else ""
            return action in ("list", "show")
        return subcommand in _SAFE_GIT
    return base in _SAFE_COMMANDS


def command_is_mutating(command: str) -> bool:
    """Decide si un comando escribe de verdad (consultar y publicar pasan).

    Se analiza por segmentos: ``cd X && git push 2>&1 | tail`` no escribe nada,
    mientras que ``echo hola > nota.txt`` o ``git checkout -- .`` sí.
    """
    text = (command or "").strip()
    if not text:
        return False
    # Escrituras dentro de scripts (python/heredocs y code mode).
    if _SCRIPT_WRITE.search(text):
        return True
    for segment in split_commands(_strip_heredocs(text)):
        masked = _mask_quotes(segment)
        if _REDIRECT.search(masked):
            return True
        if _is_safe(masked):
            continue
        # Normaliza el segmento (sin sudo/env/asignaciones) para que "env X=1 rm"
        # se reconozca igual que "rm".
        base, rest = _base_command(masked)
        normalized = " ".join([base, *rest]) if base else masked
        if _MUTATING.search(normalized):
            return True
    return False
