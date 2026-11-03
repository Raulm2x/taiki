"""Heurística de comandos y rutas: ¿esto escribe en el HOME?"""

from __future__ import annotations

import fnmatch
import re
import shlex
from pathlib import Path
from typing import Iterable, Sequence

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

HIGH_RISK_SYSTEM_PATHS = ("/etc", "/usr", "/boot", "/var/lib")

# Comandos que modifican el sistema de ficheros o el estado de la máquina.
_MUTATING = re.compile(
    r"""
    (?: ^ | [;&|()] | \bsudo\s+ )
    (?:
        rm | rmdir | mv | cp | mkdir | touch | chmod | chown | ln |
        install | tee | truncate | dd | rsync | scp | unlink | shred
    ) \b
    | \bsed\s+(?:-\w+\s+)*(?:--in-place(?:=\S+)?|-\w*i\w*)\b
    | \bfind\b[^;&|]*\s-(?:delete|exec)\b
    | \bxargs\b[^;&|]*\b(?:rm|rmdir|mv|cp|touch|mkdir|sed|tee|shred|unlink)\b
    | \bgit\s+(?:apply|am|rebase|merge|reset|checkout|restore|switch|stash|clean|
                  cherry-pick|revert|init)\b
    | \bsystemctl\s+(?:--user\s+)?(?:start|stop|restart|enable|disable|reload|
                                   daemon-reload|mask|unmask|edit)\b
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
# Opciones de esos prefijos que consumen el argumento siguiente.
_PREFIX_OPTIONS_WITH_ARG = {
    "sudo": {
        "-u", "--user", "-g", "--group", "-p", "--prompt", "-C", "--close-from",
        "-h", "--host", "-r", "--role", "-t", "--type", "-U", "--other-user",
        "-T", "--command-timeout", "-R", "--chroot",
    },
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "time": {"-o", "--output", "-f", "--format"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "exec": {"-a"},
}

_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# Redirección a un fichero (ignora duplicaciones de fd, /dev/null y comparaciones).
_REDIRECT = re.compile(r"(?<![-=<>])(?:[0-9]+|&)?>>?\s*(?!&)(?!/dev/null)(\S+)")

# Escrituras dentro de scripts (python/heredocs y code mode).
_SCRIPT_WRITE = re.compile(
    r"write_text\s*\(|write_bytes\s*\(|open\s*\([^)]*['\"][wa]\+?['\"]"
    r"|os\.remove\s*\(|shutil\.(?:copy|move|rmtree)\s*\("
    r"|tools\.(?:write|edit|shell|bash)\b|\bfs\.(?:write|unlink|mkdir|rename)"
    r"|\bwriteFileSync\s*\(|\bunlinkSync\s*\("
)

_ABSOLUTE = re.compile(r"(?<![\w~])(?:/[\w.@%+,:=-]+)+")


def _resolve(value: str | Path, base: Path) -> Path:
    """Ruta absoluta; las relativas se resuelven contra ``base``."""
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    try:
        return candidate.resolve()
    except (OSError, RuntimeError):
        return candidate.absolute()


def _under(target: Path, root: Path) -> bool:
    return target == root or root in target.parents


def resolve_roots(
    roots: Iterable[str | Path] | None = None, base: str | Path | None = None
) -> tuple[Path, ...]:
    """Raíces vigiladas: ``HOME`` por defecto; las relativas, contra ``base``.

    El modo scope añade las raíces configuradas del proyecto, de modo que
    ``/etc`` o un directorio de trabajo concreto dejen de considerarse
    "outside". Las rutas fuera de toda raíz siguen fuera.
    """
    base_path = Path(base).expanduser() if base else Path.cwd()
    try:
        base_path = base_path.resolve()
    except (OSError, RuntimeError):
        base_path = base_path.absolute()
    values: Sequence[str | Path] = (HOME,) if roots is None else tuple(roots)
    resolved: list[Path] = []
    for value in values:
        item = _resolve(value, base_path)
        if item not in resolved:
            resolved.append(item)
    return tuple(resolved)


def path_guarded(
    path: str | Path | None,
    roots: Iterable[str | Path] | None = None,
    base: str | Path | None = None,
) -> bool:
    """Solo se vigilan escrituras dentro de las raíces configuradas.

    Sin ``roots`` se conserva el comportamiento clásico (el HOME, salvo rutas
    efímeras). Con ``roots`` explícitas, una ruta fuera de todas ellas sigue
    siendo "outside"; las raíces declaradas ganan a ``EXEMPT_ROOTS``.
    """
    if not path:
        return True
    target = _resolve(path, Path(base).expanduser() if base else Path.cwd())
    if not any(_under(target, root) for root in resolve_roots(roots, base)):
        return False
    if roots is None:
        return not any(_under(target, root) for root in EXEMPT_ROOTS)
    return True


def command_touches_home(
    command: str,
    cwd: str | Path | None = None,
    roots: Iterable[str | Path] | None = None,
) -> bool:
    """Distingue un comando de scratch (/tmp) de uno que toca una raíz vigilada.

    Sin rutas absolutas se asume que escribe en el directorio del proyecto.
    Con ``roots`` se consideran también las raíces configuradas del proyecto.
    """
    paths = [item for item in _ABSOLUTE.findall(command) if len(item) > 1]
    if not paths:
        return True
    base = Path(cwd).expanduser() if cwd else Path.cwd()
    resolved_roots = resolve_roots(roots, base)
    for item in paths:
        target = _resolve(item, base)
        if any(_under(target, root) for root in resolved_roots):
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


def high_risk_command(command: str | None) -> str | None:
    """Etiqueta del comando que exige autorización explícita del usuario.

    Alto riesgo: ``sudo``, ``systemctl`` de sistema (sin ``--user``), gestores
    de paquetes y escrituras bajo ``/etc``, ``/usr``, ``/boot`` o ``/var/lib``.
    Devuelve ``"sudo"``, ``"systemctl"``, ``"paquetes"``, ``"sistema"`` o
    ``None`` si el comando no es de alto riesgo.
    """
    text = command or ""
    for segment in split_commands(text):
        tokens = _tokens(segment)
        if tokens and tokens[0] == "sudo":
            return "sudo"
        name, args = _base_command(segment)
        if name == "sudo":
            return "sudo"
        if name == "systemctl":
            if any(
                arg == "--user" or arg.startswith("--user=") for arg in args
            ):
                continue
            return "systemctl"
        if name in {"pacman", "paru", "yay"}:
            return "paquetes"
    for item in _ABSOLUTE.findall(text):
        if any(
            item == root or item.startswith(root + "/")
            for root in HIGH_RISK_SYSTEM_PATHS
        ):
            return "sistema"
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


def _consume_prefix(tokens: list[str], index: int) -> int:
    """Salta un prefijo (``sudo``/``env``/...) y sus propias opciones."""
    name = tokens[index]
    index += 1
    with_arg = _PREFIX_OPTIONS_WITH_ARG.get(name, set())
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-") or token == "-":
            break
        index += 1
        option = token.split("=", 1)[0]
        if option in with_arg and "=" not in token and index < len(tokens):
            index += 1
    if name == "timeout" and index < len(tokens):
        index += 1  # la duración va antes del comando
    return index


def _base_command(segment: str) -> tuple[str, list[str]]:
    """Comando real y argumentos, saltando prefijos y asignaciones de entorno."""
    tokens = _tokens(segment)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in _PREFIX_COMMANDS:
            index = _consume_prefix(tokens, index)
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


def command_runs_code(command: str) -> bool:
    """¿Algún segmento ejecuta código pegado (``python -c``, ``sh -c``)?"""
    for segment in split_commands(_strip_heredocs(command or "")):
        name, args = _base_command(segment)
        if _runs_code(name, args):
            return True
    return False


# ── Scope: ¿esta escritura cae dentro de los paths/recursos del ticket? ──

# Intérpretes que ejecutan código arbitrario: el destino no es deducible.
_SCRIPT_INTERPRETERS = {
    "python",
    "python3",
    "perl",
    "ruby",
    "node",
    "deno",
    "bash",
    "sh",
    "zsh",
    "fish",
    "lua",
    "php",
}
# Comandos cuyos argumentos posicionales son rutas que se escriben.
_WRITE_ALL_PATHS = {
    "rm",
    "rmdir",
    "mkdir",
    "touch",
    "chmod",
    "chown",
    "truncate",
    "dd",
    "unlink",
    "shred",
    "tee",
}
# Comandos en los que solo el último argumento es el destino escrito.
_WRITE_DEST_ONLY = {"cp", "install", "rsync", "scp", "ln"}
_AMBIGUOUS_TREES = {"find", "xargs"}
_PACKAGE_MANAGERS = {"pacman", "yay", "paru", "pip", "pipx", "npm", "pnpm", "yarn", "cargo", "uv"}
_PACKAGE_VERBS = {
    "install",
    "add",
    "remove",
    "uninstall",
    "update",
    "upgrade",
    "sync",
    "reinstall",
}


def _redirect_targets(segment: str) -> list[str]:
    return [
        match.group(1).strip("'\"")
        for match in _REDIRECT.finditer(segment)
        if match.group(1)
    ]


# Opciones que consumen el argumento siguiente en los comandos con destinos.
_OPTIONS_WITH_ARG: dict[str, set[str]] = {
    "cp": {"-t", "--target-directory", "-S", "--suffix", "-m", "--mode"},
    "mv": {"-t", "--target-directory", "-S", "--suffix"},
    "install": {
        "-t", "--target-directory", "-m", "--mode", "-o", "--owner",
        "-g", "--group", "-S", "--suffix",
    },
    "ln": {"-t", "--target-directory", "-S", "--suffix"},
    "sed": {"-e", "--expression", "-f", "--file", "-l", "--line-length"},
    "truncate": {"-s", "--size", "-r", "--reference"},
    "chmod": {"--reference"},
    "chown": {"--reference", "--from"},
}


def _split_options(
    args: list[str], with_arg: set[str]
) -> tuple[list[str], dict[str, str]]:
    """Separa posicionales y valores de opciones (``-t DIR`` y ``--opt=VAL``)."""
    positionals: list[str] = []
    values: dict[str, str] = {}
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            positionals.extend(args[index + 1 :])
            break
        if arg.startswith("-") and arg != "-":
            option, sep, value = arg.partition("=")
            if sep:
                values[option] = value
                index += 1
                continue
            if arg in with_arg and index + 1 < len(args):
                values[arg] = args[index + 1]
                index += 2
                continue
            index += 1
            continue
        positionals.append(arg)
        index += 1
    return positionals, values


def _target_directory(args: list[str]) -> str | None:
    """Directorio de ``-t``/``--target-directory`` (también ``--opt=DIR``)."""
    for index, arg in enumerate(args):
        if arg in ("-t", "--target-directory"):
            if index + 1 < len(args):
                return args[index + 1]
            return ""
        if arg.startswith("--target-directory="):
            return arg.split("=", 1)[1]
        if arg.startswith("-t") and not arg.startswith("--") and len(arg) > 2:
            return arg[2:]
    return None


def _git_subcommand(args: list[str]) -> str:
    rest = list(args)
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        if option in ("-C", "--git-dir", "--work-tree") and rest:
            rest.pop(0)
    return rest[0] if rest else ""


def _runs_code(name: str, args: list[str]) -> bool:
    """¿El intérprete ejecuta código pegado (``-c``) o por stdin (``-``)?"""
    if name not in _SCRIPT_INTERPRETERS:
        return False
    return any(
        arg == "-"
        or arg in {"-c", "--command", "-e", "--eval"}
        or (arg.startswith("-c") and len(arg) > 2)
        for arg in args
    )


def path_in_scope(
    target: str | Path,
    patterns: Iterable[str] | None,
    base: str | Path | None = None,
    cwd: str | Path | None = None,
) -> bool:
    """¿La ruta cae en algún ``paths`` del ticket (directorio, fichero o glob)?

    ``base`` resuelve los patrones (raíz del proyecto); ``cwd`` resuelve un
    destino relativo (el directorio real del comando, si se conoce).
    """
    scopes = [str(item) for item in patterns or () if str(item).strip()]
    if not scopes:
        return False
    base_path = Path(base).expanduser() if base else Path.cwd()
    target_base = Path(cwd).expanduser() if cwd else base_path
    resolved = _resolve(target, target_base)
    for raw in scopes:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = base_path / candidate
        text = str(candidate)
        if fnmatch.fnmatch(str(resolved), text):
            return True
        try:
            resolved.relative_to(candidate)
            return True
        except ValueError:
            continue
    return False


def _systemd_problem(args: list[str], allowed: set[str]) -> str | None:
    scope = "system"
    verb = ""
    units: list[str] = []
    for arg in args:
        if arg == "--user":
            scope = "user"
            continue
        if arg.startswith("-"):
            continue
        if not verb:
            verb = arg
            continue
        units.append(arg)
    for unit in units:
        resource = f"systemd:{scope}/{unit}"
        if resource not in allowed:
            return f"el recurso {resource!r}"
    return None


def _package_problem(args: list[str], allowed: set[str]) -> str | None:
    names = [
        arg
        for arg in args
        if not arg.startswith("-") and arg not in _PACKAGE_VERBS
    ]
    for name in names:
        resource = f"package:{name}"
        if resource not in allowed:
            return f"el recurso {resource!r}"
    return None


_SCOPE_GIT_BLOCKED = {"checkout", "restore", "apply", "clean", "reset", "stash"}


def _scope_targets(
    name: str, args: list[str], segment: str
) -> tuple[list[str], str | None]:
    """Destinos escritos por un comando y, si no se pueden probar, el motivo."""
    with_arg = _OPTIONS_WITH_ARG.get(name, set())
    if name == "dd":
        targets = [
            arg.split("=", 1)[1]
            for arg in args
            if arg.startswith("of=") and arg.split("=", 1)[1]
        ]
        if any(arg.startswith("of=") for arg in args) and not targets:
            return [], "`dd` con `of=` vacío"
        return targets, None
    positionals, values = _split_options(args, with_arg)
    if name == "sed":
        in_place = any(
            arg.startswith("--in-place") or (
                arg.startswith("-") and not arg.startswith("--") and "i" in arg
            )
            for arg in args
        )
        if not in_place:
            return [], None
        if not positionals:
            return [], "`sed -i` sin fichero"
        return positionals[-1:], None
    directory = _target_directory(args) or values.get("--target-directory")
    if name == "mv":
        if directory is not None:
            return [directory, *positionals], None
        return positionals, None
    if name in {"cp", "install", "ln"}:
        if directory is not None:
            return ([directory] if directory else []), (
                None if directory else "`--target-directory` vacío"
            )
        if not positionals:
            return [], f"no se puede probar el destino de `{name}`"
        return positionals[-1:], None
    if name in {"chmod", "chown"}:
        targets = positionals[1:]
        if not targets:
            return [], f"no se puede probar el destino de `{name}`"
        return targets, None
    if name == "truncate":
        if not positionals:
            return [], "no se puede probar el destino de `truncate`"
        return positionals, None
    if name in _WRITE_ALL_PATHS:
        if not positionals:
            return [], f"no se puede probar el destino de `{name}`"
        return positionals, None
    absolute = [item for item in _ABSOLUTE.findall(segment) if len(item) > 1]
    if not absolute:
        return [], f"no se puede probar el destino de `{name}`"
    return absolute, None


def command_scope_problem(
    command: str,
    patterns: Iterable[str] | None,
    resources: Iterable[str] | None = None,
    base: str | Path | None = None,
    cwd: str | Path | None = None,
) -> str | None:
    """Motivo por el que un comando no cabe en el scope del ticket.

    Devuelve ``None`` si todos los destinos son demostrables dentro del scope.
    Un comando ambiguo (``python -c``, ``sed -i``, ``tee``, ``cp``, ``mv``,
    ``install``, ``systemctl edit``) sin destino comprobable se bloquea: el
    guard no adivina.
    """
    base_path = Path(base).expanduser() if base else Path.cwd()
    cwd_path = Path(cwd).expanduser() if cwd else base_path
    scopes = list(patterns or ())
    allowed = {str(item) for item in resources or ()}
    text = _strip_heredocs(command or "")
    for segment in split_commands(text):
        name, args = _base_command(segment)
        if not name:
            continue
        if name == "git":
            subcommand = _git_subcommand(args)
            if subcommand in _SCOPE_GIT_BLOCKED:
                return (
                    f"`git {subcommand}` puede modificar el árbol fuera del scope"
                )
            # Staging y commit se validan en la capa de commits.
            continue
        if _runs_code(name, args):
            return f"`{name}` puede escribir en cualquier ruta"
        if not command_is_mutating(segment):
            continue
        for target in _redirect_targets(segment):
            if not path_in_scope(target, scopes, base_path, cwd_path):
                return f"la redirección a {target!r}"
        if name in _SAFE_COMMANDS:
            # P. ej. ``echo hola > f``: la redirección ya se validó arriba.
            continue
        if name in _SCRIPT_INTERPRETERS:
            return f"`{name}` puede escribir en cualquier ruta"
        if name == "systemctl":
            problem = _systemd_problem(args, allowed)
            if problem is not None:
                return problem
            continue
        if name in _PACKAGE_MANAGERS:
            problem = _package_problem(args, allowed)
            if problem is not None:
                return problem
            continue
        if name in _AMBIGUOUS_TREES:
            return f"`{name}` puede escribir fuera del scope"
        targets, reason = _scope_targets(name, args, segment)
        if reason is not None:
            return reason
        for target in targets:
            if not path_in_scope(target, scopes, base_path, cwd_path):
                return f"la ruta {target!r}"
    return None
