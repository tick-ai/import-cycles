#!/usr/bin/env python3
"""import-cycles: find the import cycles in a Python project, without running it.

It reads the ``.py`` files of a project, builds the graph of imports among the
project's own modules and prints every cycle with the file and the line of each
import on it. Nothing is ever imported or run: files are only parsed.
"""

import ast
import json
import os
import sys
import warnings
from dataclasses import dataclass

TOOL = "import-cycles"
VERSION = "0.1.0"

SKIPPED_DIRS = frozenset(
    {".venv", "venv", "__pycache__", ".git", "node_modules", "build", "dist", "site-packages"}
)

USAGE = (
    "usage: import_cycles.py [folder] [--src <folder>] [--exclude <path>]... "
    "[--runtime-only] [--json | --dot]"
)


class UsageError(Exception):
    """A bad command line: reported on stderr, exit code 2."""


@dataclass
class Options:
    folder: str
    src: str | None
    excludes: list[tuple[str, ...]]
    runtime_only: bool
    as_json: bool
    as_dot: bool


@dataclass
class Module:
    name: str
    path: str  # absolute path used to read the file
    file: str  # path relative to the project folder, parts joined by "/"
    is_package: bool


@dataclass
class Import:
    """One import found in a module, already resolved to a project module."""

    source: str
    target: str
    file: str
    line: int
    column: int
    position: int
    form: str
    deferred: bool
    typing: bool

    def key(self) -> tuple[bool, bool, int, int, int]:
        return (self.typing, self.deferred, self.line, self.column, self.position)


@dataclass
class Cycle:
    modules: list[str]
    path: list[str]
    imports: list[Import]


# --------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------


def parse_args(argv: list[str]) -> Options:
    folder: str | None = None
    src: str | None = None
    excludes: list[tuple[str, ...]] = []
    runtime_only = False
    as_json = False
    as_dot = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--src", "--exclude"):
            if i + 1 >= len(argv):
                raise UsageError(f"option {arg} needs a value")
            value = argv[i + 1]
            i += 1
            if arg == "--src":
                if src is not None:
                    raise UsageError("--src given twice")
                src = value
            else:
                parts = tuple(p for p in value.split("/") if p not in ("", "."))
                if not parts:
                    raise UsageError(f"empty --exclude path: {value!r}")
                excludes.append(parts)
        elif arg == "--runtime-only":
            runtime_only = True
        elif arg == "--json":
            as_json = True
        elif arg == "--dot":
            as_dot = True
        elif arg.startswith("-"):
            raise UsageError(f"unknown option: {arg}")
        elif folder is not None:
            raise UsageError(f"more than one folder: {folder!r} and {arg!r}")
        else:
            folder = arg
        i += 1
    if as_json and as_dot:
        raise UsageError("--json and --dot cannot be used together")
    if folder is None:
        folder = "."
    if not os.path.isdir(folder):
        raise UsageError(f"not a folder: {folder}")
    if src is not None and not os.path.isdir(os.path.join(folder, src)):
        raise UsageError(f"--src is not a folder: {src}")
    return Options(folder, src, excludes, runtime_only, as_json, as_dot)


# --------------------------------------------------------------------------
# Finding the modules
# --------------------------------------------------------------------------


def is_excluded(parts: tuple[str, ...], excludes: list[tuple[str, ...]]) -> bool:
    for ex in excludes:
        if parts[: len(ex)] == ex:
            return True
    return False


def find_modules(options: Options) -> dict[str, Module]:
    """Walk the base folder and return the project's modules by name."""
    folder_abs = os.path.abspath(options.folder)
    base = options.folder if options.src is None else os.path.join(options.folder, options.src)
    rel_base = os.path.relpath(os.path.abspath(base), folder_abs)
    prefix: tuple[str, ...] = () if rel_base == "." else tuple(rel_base.split(os.sep))

    found: dict[str, Module] = {}
    inits: dict[str, Module] = {}
    # Each stack item: (directory path, parts relative to the base folder).
    stack: list[tuple[str, tuple[str, ...]]] = [(base, ())]
    while stack:
        directory, rel = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            parts = rel + (name,)
            try:
                if entry.is_symlink():
                    continue
                is_dir = entry.is_dir(follow_symlinks=False)
                is_file = entry.is_file(follow_symlinks=False)
            except OSError:
                continue
            if is_excluded(prefix + parts, options.excludes):
                continue
            if is_dir:
                if name in SKIPPED_DIRS or name.startswith(".") or not name.isidentifier():
                    continue
                stack.append((entry.path, parts))
            elif is_file and name.endswith(".py"):
                stem = name[:-3]
                if stem == "__init__":
                    if not rel:
                        continue  # an __init__.py in the base folder is not a module
                    dotted = rel
                    package = True
                else:
                    dotted = rel + (stem,)
                    package = False
                if not all(p.isidentifier() for p in dotted):
                    continue
                module = Module(
                    ".".join(dotted), entry.path, "/".join(prefix + parts), package
                )
                (inits if package else found)[module.name] = module
    found.update(inits)  # a package beats a module of the same name
    return found


# --------------------------------------------------------------------------
# Finding the imports of one module
# --------------------------------------------------------------------------


def is_type_checking(test: ast.expr) -> bool:
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    if isinstance(test, ast.Attribute):
        return test.attr == "TYPE_CHECKING"
    return False


def call_import_name(node: ast.Call) -> str | None:
    """The constant string an import call names, or None if it is not one."""
    func = node.func
    if isinstance(func, ast.Name):
        if func.id not in ("__import__", "import_module"):
            return None
    elif isinstance(func, ast.Attribute):
        if not (
            func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        ):
            return None
    else:
        return None
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def resolve(dotted: str, names: dict[str, Module]) -> str | None:
    """The longest prefix of a dotted name that is a project module."""
    parts = dotted.split(".")
    if any(not p for p in parts):
        return None
    while parts:
        candidate = ".".join(parts)
        if candidate in names:
            return candidate
        parts.pop()
    return None


def from_targets(
    module: Module, node: ast.ImportFrom, names: dict[str, Module]
) -> list[tuple[int, str]]:
    """(position of the name, target module) for each name of a from-import."""
    if node.level == 0:
        base = node.module or ""
    else:
        package = module.name if module.is_package else module.name.rpartition(".")[0]
        package_parts = package.split(".") if package else []
        if node.level > len(package_parts):
            return []
        kept = package_parts[: len(package_parts) - (node.level - 1)]
        if node.module:
            kept = kept + node.module.split(".")
        base = ".".join(kept)
    if not base:
        return []
    fallback = resolve(base, names)
    result: list[tuple[int, str]] = []
    for position, alias in enumerate(node.names):
        sub = f"{base}.{alias.name}"
        if alias.name != "*" and sub in names:
            result.append((position, sub))
        elif fallback is not None:
            result.append((position, fallback))
    return result


def module_imports(module: Module, tree: ast.AST, names: dict[str, Module]) -> list[Import]:
    found: list[Import] = []

    def add(
        node: ast.AST, position: int, target: str, form: str, deferred: bool, typing: bool
    ) -> None:
        found.append(
            Import(
                module.name,
                target,
                module.file,
                getattr(node, "lineno", 1),
                getattr(node, "col_offset", 0),
                position,
                form,
                deferred,
                typing,
            )
        )

    # Iterative walk: (node, inside a function, under TYPE_CHECKING).
    stack: list[tuple[ast.AST, bool, bool]] = [(tree, False, False)]
    while stack:
        node, deferred, typing = stack.pop()
        if isinstance(node, ast.Import):
            for position, alias in enumerate(node.names):
                target = resolve(alias.name, names)
                if target is not None:
                    add(node, position, target, "import", deferred, typing)
            continue
        if isinstance(node, ast.ImportFrom):
            for position, target in from_targets(module, node, names):
                add(node, position, target, "from", deferred, typing)
            continue
        if isinstance(node, ast.Call):
            dotted = call_import_name(node)
            if dotted is not None:
                target = resolve(dotted, names)
                if target is not None:
                    add(node, 0, target, "call", deferred, typing)
        has_body = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
        guarded = isinstance(node, ast.If) and is_type_checking(node.test)
        for field, value in ast.iter_fields(node):
            child_deferred = deferred or (has_body and field == "body")
            child_typing = typing or (guarded and field == "body")
            children = value if isinstance(value, list) else [value]
            for child in children:
                if isinstance(child, ast.AST):
                    stack.append((child, child_deferred, child_typing))
    return found


def parse_module(module: Module) -> tuple[ast.AST | None, tuple[int, int, str] | None]:
    """Parse a module's file: (tree, None) or (None, (line, column, message))."""
    try:
        with open(module.path, "rb") as handle:
            data = handle.read()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return ast.parse(data, module.file), None
    except SyntaxError as error:
        line = error.lineno if isinstance(error.lineno, int) else 1
        column = error.offset if isinstance(error.offset, int) else 1
        return None, (line, column, error.msg or "invalid syntax")
    except Exception as error:  # unreadable file, null bytes, too deep, ...
        message = str(error) or type(error).__name__
        return None, (1, 1, message)


# --------------------------------------------------------------------------
# Graph and cycles
# --------------------------------------------------------------------------


def strongly_connected(nodes: list[str], adj: dict[str, list[str]]) -> list[list[str]]:
    """Tarjan's algorithm, without recursion."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    components: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(adj[root]))]
        while work:
            v, successors = work[-1]
            descended = False
            for w in successors:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on_stack.add(w)
                    work.append((w, iter(adj[w])))
                    descended = True
                    break
                if w in on_stack:
                    low[v] = min(low[v], index[w])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[v])
            if low[v] == index[v]:
                component: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    component.append(w)
                    if w == v:
                        break
                components.append(component)
    return components


def shortest_cycle(
    start: str, component: set[str], adj: dict[str, list[str]], radj: dict[str, list[str]]
) -> list[str]:
    """The shortest cycle through start; the smallest module list among equals."""
    # Distance of each module of the component to start, by a reverse BFS.
    dist = {start: 0}
    queue = [start]
    for node in queue:
        for before in radj[node]:
            if before in component and before not in dist:
                dist[before] = dist[node] + 1
                queue.append(before)
    remaining = min(dist[s] for s in adj[start] if s in dist) + 1
    path = [start]
    current = start
    while remaining > 0:
        current = min(s for s in adj[current] if dist.get(s) == remaining - 1)
        path.append(current)
        remaining -= 1
    return path


def build_cycles(
    nodes: list[str], edges: dict[tuple[str, str], Import]
) -> list[Cycle]:
    adj: dict[str, list[str]] = {n: [] for n in nodes}
    radj: dict[str, list[str]] = {n: [] for n in nodes}
    for (a, b) in sorted(edges):
        adj[a].append(b)
        radj[b].append(a)
    cycles: list[Cycle] = []
    for component in strongly_connected(nodes, adj):
        members = sorted(component)
        if len(members) == 1 and members[0] not in adj[members[0]]:
            continue
        path = shortest_cycle(members[0], set(members), adj, radj)
        imports = [edges[(path[i], path[i + 1])] for i in range(len(path) - 1)]
        cycles.append(Cycle(members, path, imports))
    cycles.sort(key=first_module)
    return cycles


def first_module(cycle: Cycle) -> str:
    return cycle.modules[0]


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------


def marks(item: Import) -> str:
    parts = [label for label, on in (("deferred", item.deferred), ("typing", item.typing)) if on]
    return f" ({', '.join(parts)})" if parts else ""


def render_plain(cycles: list[Cycle], module_count: int) -> str:
    if not cycles:
        return f"no import cycles; modules: {module_count}\n"
    lines: list[str] = []
    for number, cycle in enumerate(cycles, 1):
        lines.append(f"cycle {number}: {' -> '.join(cycle.path)}")
        lines.append(f"  modules: {', '.join(cycle.modules)}")
        for item in cycle.imports:
            lines.append(f"  {item.file}:{item.line}: {item.source} -> {item.target}{marks(item)}")
    noun = "import cycle" if len(cycles) == 1 else "import cycles"
    lines.append(f"{len(cycles)} {noun}; modules: {module_count}")
    return "\n".join(lines) + "\n"


def render_json(cycles: list[Cycle], module_count: int) -> str:
    document = {
        "tool": TOOL,
        "version": VERSION,
        "modules": module_count,
        "cycles": [
            {
                "modules": cycle.modules,
                "path": cycle.path,
                "imports": [
                    {
                        "from": item.source,
                        "to": item.target,
                        "file": item.file,
                        "line": item.line,
                        "form": item.form,
                        "deferred": item.deferred,
                        "typing": item.typing,
                    }
                    for item in cycle.imports
                ],
            }
            for cycle in cycles
        ],
    }
    return json.dumps(document, ensure_ascii=False) + "\n"


def render_dot(nodes: list[str], edges: dict[tuple[str, str], Import], cycles: list[Cycle]) -> str:
    cycle_of: dict[str, int] = {}
    for number, cycle in enumerate(cycles):
        for name in cycle.modules:
            cycle_of[name] = number
    lines = ["digraph imports {"]
    for name in nodes:
        lines.append(f'  "{name}";')
    for (a, b) in sorted(edges):
        item = edges[(a, b)]
        attributes: list[str] = []
        if a in cycle_of and cycle_of.get(b) == cycle_of[a]:
            attributes.append("color=red")
        if item.typing:
            attributes.append("style=dotted")
        elif item.deferred:
            attributes.append("style=dashed")
        suffix = f" [{', '.join(attributes)}]" if attributes else ""
        lines.append(f'  "{a}" -> "{b}"{suffix};')
    lines.append("}")
    return "\n".join(lines) + "\n"


def write_stream(stream: object, text: str) -> None:
    data = text.encode("utf-8", errors="replace")
    buffer = getattr(stream, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        buffer.flush()
    else:
        stream.write(data.decode("utf-8"))  # type: ignore[attr-defined]


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        options = parse_args(args)
    except UsageError as error:
        write_stream(sys.stderr, f"{TOOL}: {error}\n{USAGE}\n")
        return 2

    modules = find_modules(options)
    names = dict(sorted(modules.items()))

    trees: dict[str, ast.AST] = {}
    failures: list[tuple[str, int, int, str]] = []
    for module in names.values():
        tree, failure = parse_module(module)
        if tree is not None:
            trees[module.name] = tree
        elif failure is not None:
            line, column, message = failure
            failures.append((module.file, line, column, " ".join(message.split())))
    if failures:
        failures.sort(key=lambda f: f[0])
        text = "".join(f"{file}:{line}:{column}: {message}\n" for file, line, column, message in failures)
        write_stream(sys.stderr, text)
        return 2

    edges: dict[tuple[str, str], Import] = {}
    for name, tree in trees.items():
        for item in module_imports(names[name], tree, names):
            if options.runtime_only and item.typing:
                continue
            edge = (item.source, item.target)
            best = edges.get(edge)
            if best is None or item.key() < best.key():
                edges[edge] = item

    nodes = list(names)
    cycles = build_cycles(nodes, edges)
    if options.as_json:
        text = render_json(cycles, len(nodes))
    elif options.as_dot:
        text = render_dot(nodes, edges, cycles)
    else:
        text = render_plain(cycles, len(nodes))
    write_stream(sys.stdout, text)
    return 1 if cycles else 0


if __name__ == "__main__":
    raise SystemExit(main())
