# import-cycles

import-cycles finds every import cycle among the modules of a Python project and prints each one with the file and the line of every import on it, without importing or running anything.

```
pipx run --spec git+https://github.com/tick-ai/import-cycles import-cycles
```

A real run, on a small app where `models` imports `services` at the top and `services` imports `models` inside a function:

```
$ import-cycles
cycle 1: app.models -> app.services -> app.models
  modules: app.models, app.services, app.views
  app/models.py:1: app.models -> app.services
  app/services.py:7: app.services -> app.models (deferred)
1 import cycle; modules: 4
$ echo $?
1
```

## Why it matters

Circular imports break at import time, in ways that depend on which module loads first. One new `from app.models import User` and a project that worked now fails with `ImportError: cannot import name`. Coding agents add such imports without noticing. import-cycles reads the `.py` files, never imports them (a file is only parsed, so nothing in it runs), and tells you which modules are in a cycle, the shortest cycle through the first of them, and the exact import to move or defer.

## First run in one minute

You need Python 3.11 or newer and nothing else (the tool uses the standard library only).

```
# from a clone of this repository
python3 import_cycles.py path/to/project

# or without a clone
pipx run --spec git+https://github.com/tick-ai/import-cycles import-cycles path/to/project
```

It is not on PyPI yet, so `pip install import-cycles` and `pipx install import-cycles` do not work.

```
python3 import_cycles.py [folder] [--src <folder>] [--exclude <path>]... [--runtime-only] [--json | --dot]
```

## Module names

Each `.py` file under the base folder (the folder you give, or the `--src` folder) has a path relative to it.

* `a/b/c.py` is the module `a.b.c`; `a/b/__init__.py` is the package `a.b`; `x.py` is `x`.
* A `__init__.py` directly in the base folder is not a module, and is not read.
* A file is a module only if every part of its dotted name is a Python identifier. `my-script.py`, `1x.py`, `.hidden.py` and anything under `my-dir/` are not modules: they are not parsed and never a finding.
* A folder without `__init__.py` is a namespace package (PEP 420): it gives its name to the modules below it, but is not a module and not a node of the graph.
* If `a.py` and `a/__init__.py` both exist, the module `a` is `a/__init__.py`.
* Names are case-sensitive. The project's own modules are exactly the modules found this way. They are ordered by their names as strings, code point by code point (`Zeta` before `alpha`, `pkg.z` before `pkg_a`).

### Which files are read

The tool walks every folder below the base folder and reads the files whose name ends in `.py`. It skips:

* folders named `.venv`, `venv`, `__pycache__`, `.git`, `node_modules`, `build`, `dist` or `site-packages`, and folders whose name starts with a dot, at any depth (the base folder itself is never skipped, whatever its name);
* what `--exclude` names;
* symbolic links, to files and to folders: they are not followed and not read.

## How imports are resolved

The tree of each module is walked, every statement at any depth. These are imports:

1. `import a.b.c`, `import a.b as x`, `import a, b`: each name is one import.
2. `from M import n1, n2` and the relative forms `from . import x`, `from .y import z`, `from ..y import z`: each imported name is one import.
3. A call to `__import__`, `import_module` or `importlib.import_module` whose first positional argument is a string literal. A name, a concatenation, an f-string or a keyword argument is not an import. A string with an empty part (`""`, `".x"`, `"a..b"`) names nothing.

The line of an import is the line its statement begins on (the line of `from` for a multi-line `from x import (...)`; the line where the call begins for a call).

**Resolving a dotted name.** `resolve(D)` is the longest prefix of `D` (cut at dots) that is a project module, or nothing. `import a.b.c` is an import of `a.b.c` if that is a module, else of `a.b`, else of `a`, else nothing. The standard library and third-party packages are never project modules, so they give no edge. No edge is added for the parent packages Python also loads.

**`from B import n`.** If `B.n` is a project module the target is `B.n` (a submodule wins over a name defined in `B`); otherwise it is `resolve(B)`. `from B import *` targets `resolve(B)`. Each imported name gives its own target.

**Relative imports.** A module's package is its own name if it is a package, else its name without the last part. A level greater than the number of parts of the package climbs above the top and is ignored. Otherwise the base is the first `parts - (level - 1)` parts of the package, followed by the module after the dots, and it goes on as `from B import n`. In `p.q.m`, `from ..y import z` has the base `p.y` and `from . import x` has the base `p.q`.

An import whose target is the module itself is an edge from the module to itself, and a cycle.

### Marks

* `deferred`: the import is inside a function (`def`, `async def`, or the expression of a `lambda`, at any depth). Decorators, default values and annotations of a function are not inside it. Class bodies and `if`/`try`/`with`/`for`/`while` at module level are not deferred.
* `typing`: the import is in the body (not the `else`) of an `if` whose test is the name `TYPE_CHECKING` or an attribute named `TYPE_CHECKING` (`typing.TYPE_CHECKING`). `not TYPE_CHECKING` does not count; `elif TYPE_CHECKING:` does.

An import can be both. Other guards such as `try: ... except ImportError` change nothing.

## What is and is not a finding

A cycle is a strongly connected component of the graph of the project's modules that has more than one module, or one module that imports itself. Every cycle is reported once; a module is in at most one cycle.

For each cycle the tool reports all its modules and the **shortest cycle through its first module**; among the shortest, the one whose modules after the first are smallest in module order. Cycles come in the order of their first modules. When several imports give the same edge, the one shown is the best by this key: not `typing` first, then not `deferred`, then the smallest line, then the smallest column, then the position of the name in the statement.

Not a finding: imports of the standard library or of third-party packages; names that match no module; relative imports that climb above the top; `import_module` of a variable, `exec`, `eval`, `__import__` through an alias; files that are not modules; anything in a skipped folder or an excluded path.

## Options

| Option | Meaning |
| --- | --- |
| `folder` | The project's root. Default `.`. At most one. It must be a folder. |
| `--src <folder>` | A source folder below the project (the `src/` layout), relative to `folder` (an absolute path is accepted). Only the `.py` files under it are read, and module names are taken relative to it. Given twice is a usage error. |
| `--exclude <path>` | Leaves out a file or a folder and everything under it. Relative to `folder` (not to `--src`), written with `/`; empty and `.` parts are dropped (`./tests/` is `tests`); matched part by part from the start, so `tests` leaves out `<folder>/tests` and not `<folder>/pkg/tests`. No wildcards. A path that does not exist leaves out nothing. May be repeated. |
| `--runtime-only` | Leaves out every import marked `typing` before the graph is built. `deferred` imports stay. |
| `--json` | Print a JSON document. |
| `--dot` | Print a Graphviz graph. Not together with `--json`. |

An option's value is always the next argument, whatever it looks like. `--src=x` is not a form. The arguments may come in any order. There is no `--help`.

## Output forms

### Plain (default)

One block per cycle, then one summary line. `<marks>` is empty, ` (deferred)`, ` (typing)` or ` (deferred, typing)`; `<file>` is relative to `folder`.

```
cycle <n>: <path joined by " -> ">
  modules: <modules joined by ", ">
  <file>:<line>: <from> -> <to><marks>
```

The last line is `<k> import cycles; modules: <m>` (`1 import cycle; modules: <m>` for one), where `<m>` is the number of project modules, whatever `--runtime-only` leaves out. With no cycle the whole output is `no import cycles; modules: <m>`.

### `--json`

One JSON document and a newline:

```json
{
  "tool": "import-cycles",
  "version": "0.1.0",
  "modules": 2,
  "cycles": [
    {
      "modules": ["a", "b"],
      "path": ["a", "b", "a"],
      "imports": [
        {"from": "a", "to": "b", "file": "a.py", "line": 1, "form": "import", "deferred": false, "typing": false},
        {"from": "b", "to": "a", "file": "b.py", "line": 4, "form": "import", "deferred": true, "typing": false}
      ]
    }
  ]
}
```

`form` is `"import"`, `"from"` or `"call"`. With no cycle, `cycles` is `[]`. (The tool prints the document on one line; the layout above is for reading.)

### `--dot`

A Graphviz graph of the project's own modules: `digraph imports {`, one `  "<module>";` per module, one `  "<from>" -> "<to>"<attributes>;` per edge, `}`. Attributes are, in this order, `color=red` when both ends are modules of the same reported cycle, then `style=dotted` when the edge shown is `typing`, else `style=dashed` when it is `deferred`.

```
python3 import_cycles.py --dot | dot -Tsvg -o cycles.svg
```

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | No import cycle was found. |
| `1` | At least one cycle was found (after `--runtime-only`). |
| `2` | A usage error, or at least one file could not be parsed. Nothing on stdout. |

A usage error is an unknown option, an option with its value missing, a second `folder`, `--src` twice, an empty `--exclude`, `--json` with `--dot`, or a `folder` or `--src` that is not a folder. The message goes to stderr, and usage errors are found before any file is read.

A file that cannot be parsed (a syntax error, or an unreadable or undecodable file) is reported on stderr as `<file>:<line>:<column>: <message>`, one line per file in the order of the paths, and the exit code is 2 even if other files hold cycles. A coding declaration and a byte-order mark are handled as Python handles them. Output is UTF-8; on success nothing is written to stderr.

## In CI

```yaml
- name: No import cycles
  run: pipx run --spec git+https://github.com/tick-ai/import-cycles import-cycles --src src --exclude tests
```

The step fails (exit 1) when there is a cycle, and also (exit 2) when a file does not parse. Add `--runtime-only` to ignore cycles that exist only for type checkers.

## What it does not do

* It never imports or runs your code, so it only sees imports written in the source as statements or as calls with a literal string.
* It does not model the parent packages Python loads on the side, `__all__`, or the order in which modules really load. It says there is a cycle; it does not say whether that cycle actually fails at import time.
* It does not follow symbolic links, does not read files outside the folder (or `--src` folder), and has no wildcards in `--exclude`.
* It does not look at the standard library or third-party packages, and does not read `.pyi` files.
* It is not on PyPI yet.

## Where it came from

Built by agents on TickHub, as TickHub job `08f45ce8-c22e-4704-9e09-7c0e751d3d6f`. The job, the hand-ins and the receipt are on TickHub's public log: https://tick.company/hub/#/job/08f45ce8-c22e-4704-9e09-7c0e751d3d6f. `docs/the-job.md` is the job as it was given.

AI does the work. Tick makes it trustworthy. https://tick.company

## Licence

Apache-2.0. Copyright 2026 Parsa Esmaeili. See `LICENSE`.
