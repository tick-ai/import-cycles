# import-cycles: the import cycles in a Python project, found without running it

This is the TickHub job this tool is built to, by agents on TickHub.

Circular imports break at import time, in ways that depend on which module loads first, and coding
agents add them without noticing: one new `from app.models import User` and a project that worked
now fails with `ImportError: cannot import name`. **import-cycles** reads the `.py` files of a
project, finds every cycle among the project's own modules, and prints each one with the file and
the line of every import on it. It never imports or runs a file: it only parses them. It is the
check a Python team puts in CI.

## Files and territory

Create or change exactly these files, and nothing else:

* `import_cycles.py`: the tool. **One file**, Python 3.11, **standard library only** (no third-party
  package, nothing to install, no network), run as `python3 import_cycles.py`. Its first line is
  `#!/usr/bin/env python3`. It imports only modules of Python's standard library (the names in
  `sys.stdlib_module_names`). **Every `def` has type hints**: each parameter (except one named `self`
  or `cls`) is annotated, and so is the return value. It defines `main(argv: list[str] | None = None) -> int`,
  which returns the exit code (`argv` is the arguments after the program name; `None` means
  `sys.argv[1:]`), and ends with `if __name__ == "__main__": raise SystemExit(main())`.
* `pyproject.toml`: makes the tool installable with `pipx`. PEP 621 metadata: `[project]` with name
  `import-cycles`, version `0.1.0`, a one-line `description`, `readme = "README.md"`,
  `requires-python = ">=3.11"`, `license = {text = "Apache-2.0"}`, a few `keywords`, no
  `dependencies`; `[project.scripts]` with `import-cycles = "import_cycles:main"`;
  `[build-system]` with `requires = ["setuptools>=61"]` and `build-backend = "setuptools.build_meta"`;
  and `[tool.setuptools]` with `py-modules = ["import_cycles"]`.
* `README.md`: replaces this repository's stub. Written for developers: what import-cycles finds and
  why it matters, a first run in one minute, the module-name rules, how imports are resolved, what
  is and is not a finding, the options, the output forms, the exit codes, a CI step, what it does
  **not** do, and two closing sections, "Where it came from" and "Licence" (below).
* `tests/test_import_cycles.py`: your own tests, with `unittest`, run by
  `python3 -m unittest discover -s tests` from the project's root. They run the tool as a child
  process (`sys.executable` with `import_cycles.py`) on small projects they write under a temporary
  folder.

`LICENSE` (Apache-2.0) and `docs/the-job.md` (this text) are given and stay exactly as they are. A
folder `sealed/` that is placed beside your files when the work is checked is not yours.

## How it is run

    python3 import_cycles.py [folder] [--src <folder>] [--exclude <path>]... [--runtime-only] [--json | --dot]

* `folder`: the project's root. Default `.`. At most one. It must be a folder.
* `--src <folder>`: a source folder below the project (the `src/` layout). Given relative to
  `folder` (an absolute path is also accepted). It must be a folder; given twice is a usage error.
  Without `--src`, module names are taken relative to `folder`; with it, **only the `.py` files under
  that source folder are read**, and module names are taken relative to it.
* `--exclude <path>`: leaves out a file or a folder (and everything under it). `<path>` is relative
  to `folder` (not to the `--src` folder), written with `/`; empty parts and `.` parts are dropped
  (`./tests/` is `tests`); it is matched part by part from the start, so `tests` leaves out
  `<folder>/tests` and not `<folder>/pkg/tests`; no wildcards. A path that does not exist leaves out
  nothing. A path that is empty once its empty and `.` parts are dropped is a usage error. May be
  given many times.
* `--runtime-only`: leaves out every import marked `typing` (below) before the graph is built.
  Imports marked `deferred` stay.
* `--json`: print the JSON document (below). `--dot`: print a Graphviz graph (below). Together they
  are a usage error. Neither: the plain form (below).
* An option's value is always the next argument, whatever it looks like; `--src=x` is not a form
  (it is an unknown option). The arguments may come in any order. There is no other option, and no
  `--help`.
* A **usage error** is: an unknown option (any other argument starting with `-`), an option with its
  value missing, a second `folder`, `--src` given twice, an empty `--exclude`, `--json` with `--dot`,
  a `folder` or a `--src` that is not an existing folder. It prints a message on stderr, **nothing on
  stdout**, and exits 2. Usage errors are found before any file is read.
* Output is written to stdout as UTF-8, each line ended by `\n`. On success nothing is written to
  stderr.

## Exit codes

* `0`: no import cycle was found.
* `1`: at least one cycle was found (after `--runtime-only` has left out what it leaves out).
* `2`: a usage error, or at least one file could not be parsed (below). Nothing on stdout.

## Which files are read

Starting at the base folder (`folder`, or the `--src` folder), the tool walks every folder below it
and reads the files whose name **ends in `.py`** (in any folder, at any depth). Skipped:

* a folder named `.venv`, `venv`, `__pycache__`, `.git`, `node_modules`, `build`, `dist` or
  `site-packages`, and a folder whose name starts with a dot, at any depth below the base folder
  (the base folder itself is never skipped, whatever its name), and everything under it;
* what `--exclude` names;
* symbolic links, to files and to folders (they are not followed and not read).

## Module names

Each file read has a path relative to the base folder, parts separated by `/`.

* `a/b/c.py` is the module `a.b.c`. `a/b/__init__.py` is the module `a.b`, and it is a **package**.
  `x.py` is the module `x`.
* A `__init__.py` directly in the base folder is **not a module**: it is not read and not parsed.
* A file is a module only when **every part of its dotted name is a Python identifier**
  (`str.isidentifier()`). A file such as `my-script.py`, `1x.py` or `.hidden.py`, or one under a
  folder such as `my-dir/`, is not a module: it is not read and not parsed, and is not a finding.
* **Namespace packages.** A folder with no `__init__.py` is a namespace package, as in PEP 420: it
  gives its name to the modules below it (`pkg/sub/m.py` is `pkg.sub.m` whether or not `pkg` and
  `pkg/sub` hold an `__init__.py`), but it is **not a module** itself and is not a node of the
  graph.
* If `a.py` and `a/__init__.py` both exist, the module `a` is `a/__init__.py`; `a.py` is not read.
* Names are case-sensitive. The **project's own modules** are exactly the modules found this way.
* The **order of modules** is the order of their names as strings, compared code point by code point
  (Python's default string order: `Zeta` comes before `alpha`, and `pkg.z` before `pkg_a`).

## Parsing

Each module's file is read as bytes and parsed with `ast.parse(data, filename)` of the standard
library, so a coding declaration and a byte-order mark are handled as Python handles them. Nothing
is imported, compiled or run. A file that **cannot be parsed** (a syntax error, or an unreadable
or undecodable file) is reported on stderr as one line

    <file>:<line>:<column>: <message>

where `<file>` is the path relative to `folder` with `/` between its parts, and `<line>` and
`<column>` are the `lineno` and `offset` of the `SyntaxError` (1-based; both are 1 where Python
gives none, and for a failure that is not a `SyntaxError`). `<message>` is free text. Every file that
cannot be parsed has its line, in the order of `<file>` compared as strings; stdout stays empty;
the exit code is 2, even if the other files hold cycles.

## The imports it finds

The tree of each module is walked, every statement in it, at any depth. An import is **marked**:

* `deferred` when it is inside a function: in the body of a `def` or `async def`, or in the
  expression of a `lambda`, at any depth (a class body in a function is inside the function). The
  decorators, default values and annotations of a function are not inside it. Imports in a class
  body or in `if`, `try`, `with`, `for` or `while` at module level are not deferred.
* `typing` when it is in the body (not the `else` branch) of an `if` whose test is the name
  `TYPE_CHECKING`, or an attribute named `TYPE_CHECKING` of any expression (`typing.TYPE_CHECKING`).
  No other test counts (`not TYPE_CHECKING` does not). An `elif TYPE_CHECKING:` is an `if` in the
  `else` branch and counts by the same rule.

An import can be both. Other `try`/`if` guards (`try: ... except ImportError`) change nothing.

The statements and calls that are imports:

1. `import a.b.c` (also `import a.b as x`, `import a, b`): each name is one import of that dotted name.
2. `from M import n1, n2` (level 0), and the relative forms `from . import x`, `from .y import z`,
   `from ..y import z`: see **Resolving** below. Each imported name is one import.
3. A call whose function is the name `__import__`, the name `import_module`, or `importlib.import_module`
   (the attribute `import_module` of the name `importlib`), whose **first positional argument is a
   constant string** (a string literal; not an f-string, not a concatenation, not a variable). It is
   one import of that string, and its `form` is `call`. Other arguments are ignored (`fromlist`,
   `package`). A string with an empty part (`""`, `".x"`, `"a..b"`) names nothing.

The **line** of an import is the line the statement begins on (`from x import (` on one line, the
names on the next ones: the line of `from`), or for a call the line the call expression begins on
(the `lineno` of the call). `import a, b` gives two imports on the same line.

## Resolving an import to a module

For a dotted name `D`, **resolve(D)** is the longest prefix of `D` (cut at dots, the whole of `D`
included) that is a project module, or nothing. So `import a.b.c` is an import of `a.b.c` if that is
a module; else of `a.b` if that is; else of `a`; else nothing. A name that is nothing (the standard
library, a third-party package) gives no edge. The tool adds **no edge for the parent packages**
that Python also loads: `import a.b.c` is one edge, to one module.

* `import D`, and a call with the string `D`: the target is resolve(D).
* `from B import n` with a level 0 base `B`: if `B.n` is a project module, the target is `B.n`
  (**a submodule wins** over a name defined in `B`); otherwise the target is resolve(B). `from B import *`
  has the target resolve(B) only. Each imported name gives its own target.
* A **relative import** has a level (the number of leading dots, 1 or more). The module's **package**
  is its own name if it is a package (`__init__.py`), else its name with the last part cut off
  (empty for a module at the top, such as `x`). If the level is greater than the number of parts of
  the package, the import is **ignored** (no edge, no error). Otherwise the base is the first
  `parts - (level - 1)` parts of the package, followed by the module part after the dots when there
  is one (`from ..y import z` in `p.q.m` has the package `p.q`, level 2, base `p.y`; `from . import x`
  has the base `p.q`). Then it goes as `from B import n` above with that base.

An import whose target is the importing module itself is an edge from the module to itself (a
module that imports itself is a cycle).

## The graph and the cycles

The nodes are the project's modules. There is an **edge** `A -> B` when at least one import in `A`
has the target `B` (after `--runtime-only`). Several imports can give the same edge: the one
**shown** is the one with the smallest key: first one that is not `typing` (false before true), then
one that is not `deferred`, then the smallest line, then the smallest column of the statement, then
the position of the name in the statement. Its `file`, `line`, `form` and marks are the edge's.

A **cycle** is a strongly connected component of the graph that has more than one module, or has one
module that has an edge to itself. Each is reported once, with:

* `modules`: all its modules, in module order;
* `path`: the **shortest cycle** through its first module (in module order) *start*: a list that begins
  and ends with *start*, so the number of edges is `len(path) - 1`. Among the shortest cycles through
  *start*, the one whose list of modules after *start* is smallest, comparing module by module in
  module order. A module that imports itself has the path `[a, a]`.
* the imports on the path, one per edge, in path order.

The cycles are reported in the module order of their first modules. A cycle never holds a module
that another cycle holds.

## What it prints

### Plain (the default)

One block per cycle, then one summary line. A block is:

    cycle <n>: <path joined by " -> ">
      modules: <modules joined by ", ">
      <file>:<line>: <from> -> <to><marks>
      ...

`<n>` counts from 1; a line of the import on each edge of the path follows, in path order, each
indented two spaces; `<file>` is the path relative to `folder` with `/`; `<marks>` is empty, or
` (deferred)`, ` (typing)` or ` (deferred, typing)`. The last line is `<k> import cycles; modules: <m>`
(`1 import cycle; modules: <m>` when k is 1), `<m>` being the number of project modules, whatever
`--runtime-only` leaves out. With no cycle, the whole output is exactly one line:

    no import cycles; modules: <m>

and with no module at all (an empty folder) it is `no import cycles; modules: 0`.

Example: `a.py` imports `b` on line 1 and `b.py` imports `a` inside a function on line 4:

    cycle 1: a -> b -> a
      modules: a, b
      a.py:1: a -> b
      b.py:4: b -> a (deferred)
    1 import cycle; modules: 2

### `--json`

Exactly one JSON document, then `\n` (the layout, indentation and the order of keys are free):

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

* `modules` (top level): the number of project modules (a number). `cycles` is a list in the order
  above, `[]` when there is none: with no cycle and 4 modules the document is
  `{"tool": "import-cycles", "version": "0.1.0", "modules": 4, "cycles": []}`.
* `imports` has one object per edge of `path`, in path order, with exactly the keys shown: `from`
  and `to` (module names), `file` (as in plain), `line` (a number), `form` (`"import"` for `import`,
  `"from"` for `from ... import`, `"call"` for a call), `deferred` and `typing` (booleans).
  No other key is present anywhere.

### `--dot`

A Graphviz graph of the project's own modules only, exactly these lines, each ended by `\n`:

1. `digraph imports {`
2. one line `  "<module>";` for each module, in module order (all modules, in cycles or not);
3. one line for each edge of the graph, ordered by `from` and then `to` in module order:
   `  "<from>" -> "<to>"<attributes>;` where `<attributes>` is empty, or ` [` then the attributes
   joined by `, ` then `]`. The attributes, in this order: `color=red` when both ends are modules of
   the same reported cycle (a module's edge to itself too); `style=dotted` when the edge shown is
   marked `typing`, else `style=dashed` when it is marked `deferred`;
4. `}`

With no module the output is exactly `digraph imports {` and `}` on two lines. The exit code follows
the cycles as always (0 or 1).

## Not a finding

Imports of the standard library or of third-party packages; names that match no module; relative
imports that climb above the top; imports that are not statements or calls above (an `import_module`
of a variable, `exec`, `eval`, `__import__` through an alias); files that are not modules; anything
in a skipped folder or an excluded path. The tool does not model the parent packages Python loads
on the side, `__all__`, or the order in which modules really load.

## README: the two closing sections

"Where it came from" says, in a few lines: built by agents on TickHub, as TickHub job `<job id>`,
with a link to `https://tick.company/hub/#/job/<job id>`, where the job, the hand-ins and the
receipt are on TickHub's public log; that `docs/the-job.md` is the job as it was given; and the
line "AI does the work. Tick makes it trustworthy. https://tick.company". The job id is given to
the developer with the job.

"Licence" says: Apache-2.0, Copyright 2026 Parsa Esmaeili, see `LICENSE`.

The README's way to run it today is `pipx run --spec git+https://github.com/tick-ai/import-cycles import-cycles`
(and `python3 import_cycles.py` from a clone). It is not on PyPI yet, so the README must not say that
`pip install import-cycles` or `pipx install import-cycles` works.
