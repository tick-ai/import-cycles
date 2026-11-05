"""Tests of import_cycles.py: it is always run as a child process, in a folder made here."""

import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

TOOL = pathlib.Path(__file__).resolve().parent.parent / "import_cycles.py"


class Result:
    def __init__(self, code: int, out: str, err: str) -> None:
        self.code = code
        self.out = out
        self.err = err

    def json(self) -> dict:
        return json.loads(self.out)

    def lines(self) -> list[str]:
        return self.out.splitlines()


class ToolCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name) / "proj"
        self.root.mkdir()

    def write(self, rel: str, text: str | bytes = "") -> pathlib.Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, str):
            text = text.encode("utf-8")
        path.write_bytes(text)
        return path

    def files(self, mapping: dict[str, str]) -> None:
        for rel, text in mapping.items():
            self.write(rel, text)

    def run_tool(self, *args: str, cwd: pathlib.Path | None = None) -> Result:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        proc = subprocess.run(
            [sys.executable, str(TOOL), *args],
            cwd=str(cwd or self.root),
            capture_output=True,
            env=env,
        )
        return Result(proc.returncode, proc.stdout.decode("utf-8"), proc.stderr.decode("utf-8"))

    def run_json(self, *args: str) -> dict:
        res = self.run_tool(*args, "--json")
        self.assertIn(res.code, (0, 1), res.err)
        return res.json()

    def cycles(self, *args: str) -> list[list[str]]:
        return [c["path"] for c in self.run_json(*args)["cycles"]]

    def usage_error(self, *args: str) -> None:
        res = self.run_tool(*args)
        self.assertEqual(res.code, 2, res.err)
        self.assertEqual(res.out, "")
        self.assertNotEqual(res.err, "")


class SourceRules(unittest.TestCase):
    def setUp(self) -> None:
        self.text = TOOL.read_text(encoding="utf-8")
        self.tree = ast.parse(self.text)

    def test_first_line(self) -> None:
        self.assertEqual(self.text.split("\n", 1)[0], "#!/usr/bin/env python3")

    def test_stdlib_only(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertIn(alias.name.split(".")[0], sys.stdlib_module_names)
            elif isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                self.assertIn((node.module or "").split(".")[0], sys.stdlib_module_names)

    def test_every_def_is_annotated(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = node.args
                params = a.posonlyargs + a.args + a.kwonlyargs
                params += [p for p in (a.vararg, a.kwarg) if p is not None]
                for p in params:
                    if p.arg in ("self", "cls"):
                        continue
                    self.assertIsNotNone(p.annotation, f"{node.name}({p.arg})")
                self.assertIsNotNone(node.returns, node.name)

    def test_main_and_ending(self) -> None:
        self.assertIn("def main(argv: list[str] | None = None) -> int:", self.text)
        self.assertTrue(
            self.text.rstrip().endswith('if __name__ == "__main__":\n    raise SystemExit(main())')
        )


class ModuleNames(ToolCase):
    def test_empty_folder(self) -> None:
        res = self.run_tool()
        self.assertEqual((res.code, res.out, res.err), (0, "no import cycles; modules: 0\n", ""))

    def test_empty_folder_json_and_dot(self) -> None:
        self.assertEqual(
            self.run_tool("--json").json(),
            {"tool": "import-cycles", "version": "0.1.0", "modules": 0, "cycles": []},
        )
        res = self.run_tool("--dot")
        self.assertEqual((res.code, res.out), (0, "digraph imports {\n}\n"))

    def test_module_counts(self) -> None:
        self.files(
            {
                "x.py": "",
                "a/__init__.py": "",
                "a/b/c.py": "",  # a.b is a namespace package: not a module
                "__init__.py": "this is not python (",  # root __init__ is not a module, not parsed
                "my-script.py": "this is not python (",
                "1x.py": "(",
                ".hidden.py": "(",
                "my-dir/m.py": "(",
                "notes.txt": "(",
                "mod.pyi": "(",
            }
        )
        self.assertEqual(self.run_tool().out, "no import cycles; modules: 3\n")

    def test_namespace_package_is_not_a_node(self) -> None:
        self.files({"ns/sub/m.py": "import ns.sub.m\n"})
        doc = self.run_json()
        self.assertEqual(doc["modules"], 1)
        self.assertEqual(doc["cycles"][0]["modules"], ["ns.sub.m"])

    def test_package_beats_module(self) -> None:
        self.files({"a.py": "import b\n(", "a/__init__.py": "", "b.py": "import a\n"})
        res = self.run_tool("--dot")
        self.assertEqual(res.code, 0, res.err)
        self.assertIn('  "b" -> "a";', res.out)
        self.assertEqual(self.run_tool().out, "no import cycles; modules: 2\n")

    def test_module_order_is_code_point_order(self) -> None:
        self.files(
            {
                "alpha.py": "import Zeta\n",
                "Zeta.py": "import alpha\n",
                "pkg/z.py": "",
                "pkg_a.py": "",
            }
        )
        doc = self.run_json()
        self.assertEqual(doc["cycles"][0]["modules"], ["Zeta", "alpha"])
        lines = self.run_tool("--dot").lines()
        names = [l.strip().strip(";").strip('"') for l in lines if l.endswith(";") and "->" not in l]
        self.assertEqual(names, ["Zeta", "alpha", "pkg.z", "pkg_a"])

    def test_case_sensitive(self) -> None:
        self.files({"A.py": "import a\n", "a.py": "import A\n"})
        self.assertEqual(self.cycles(), [["A", "a", "A"]])

    def test_non_ascii_names(self) -> None:
        self.files({"é.py": "import ü\n", "ü.py": "import é\n"})
        res = self.run_tool()
        self.assertEqual(res.code, 1)
        self.assertIn("cycle 1: é -> ü -> é\n", res.out)


class FilesRead(ToolCase):
    CYCLE = {"a.py": "import b\n", "b.py": "import a\n"}

    def prefixed(self, folder: str) -> None:
        """A two-module cycle in a folder, imports written with the full dotted names."""
        dotted = folder.replace("/", ".")
        self.files({f"{folder}/a.py": f"import {dotted}.b\n", f"{folder}/b.py": f"import {dotted}.a\n"})

    def test_named_folders_are_skipped(self) -> None:
        for name in [".venv", "venv", "__pycache__", ".git", "node_modules", "build", "dist",
                     "site-packages", ".anything", "deep/.x"]:
            self.prefixed(name)
        self.prefixed("deep/venv")
        self.assertEqual(self.run_tool().out, "no import cycles; modules: 0\n")

    def test_similar_names_are_not_skipped(self) -> None:
        self.prefixed("builds")
        self.assertEqual(self.cycles(), [["builds.a", "builds.b", "builds.a"]])

    def test_given_folder_is_never_skipped(self) -> None:
        self.files({"proj/build/a.py": "import b\n", "proj/build/b.py": "import a\n"})
        base = self.root / "proj" / "build"
        res = self.run_tool(str(base), "--json")
        self.assertEqual(res.code, 1)
        dotted = self.root / ".hid" / "venv"
        dotted.mkdir(parents=True)
        (dotted / "a.py").write_text("import b\n")
        (dotted / "b.py").write_text("import a\n")
        res = self.run_tool(str(dotted))
        self.assertEqual(res.code, 1)

    def test_exclude_is_matched_from_folder(self) -> None:
        self.prefixed("tests")
        self.prefixed("pkg/tests")
        self.assertEqual(len(self.cycles()), 2)
        self.assertEqual(self.cycles("--exclude", "tests"), [["pkg.tests.a", "pkg.tests.b", "pkg.tests.a"]])
        self.assertEqual(self.cycles("--exclude", "./tests/"), [["pkg.tests.a", "pkg.tests.b", "pkg.tests.a"]])
        self.assertEqual(self.cycles("--exclude", "pkg//tests", "--exclude", "tests"), [])

    def test_exclude_file_and_part_by_part(self) -> None:
        self.files({"p/a.py": "import p.b\n", "p/b.py": "import p.a\n", "p/ab.py": ""})
        self.assertEqual(self.cycles("--exclude", "p/a.py"), [])
        self.assertEqual(len(self.cycles("--exclude", "p/a")), 1)  # no prefix of a part
        self.assertEqual(len(self.cycles("--exclude", "nothing/here")), 1)

    def test_exclude_with_src_is_relative_to_folder(self) -> None:
        self.files({"src/lib/a.py": "import lib.b\n", "src/lib/b.py": "import lib.a\n"})
        self.assertEqual(len(self.cycles("--src", "src")), 1)
        self.assertEqual(self.cycles("--src", "src", "--exclude", "src/lib/a.py"), [])
        self.assertEqual(len(self.cycles("--src", "src", "--exclude", "lib/a.py")), 1)

    def test_symlinks_are_not_followed(self) -> None:
        self.files({"real/a.py": "import real.b\n", "real/b.py": "import real.a\n", "x.py": ""})
        try:
            os.symlink(self.root / "real", self.root / "linkdir")
            os.symlink(self.root / "real" / "a.py", self.root / "linkfile.py")
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks here")
        doc = self.run_json()
        self.assertEqual(doc["modules"], 3)
        self.assertEqual(len(doc["cycles"]), 1)

    def test_file_names_must_end_in_py(self) -> None:
        self.files({"a.py": "import b\n", "b.py": "import a\n", "c.PY": "(", "d.py.bak": "("})
        self.assertEqual(self.run_tool().code, 1)

    def test_default_folder_is_dot(self) -> None:
        self.files(self.CYCLE)
        res = self.run_tool()
        self.assertEqual(res.code, 1)
        elsewhere = self.root.parent
        res2 = self.run_tool(str(self.root), cwd=elsewhere)
        self.assertEqual(res.out, res2.out)

    def test_src_reads_only_the_source_folder(self) -> None:
        self.files(
            {
                "src/app/__init__.py": "",
                "src/app/a.py": "from app import b\n",
                "src/app/b.py": "from app import a\n",
                "other/x.py": "(",
                "top.py": "(",
            }
        )
        res = self.run_tool("--src", "src")
        self.assertEqual(res.code, 1, res.err)
        self.assertEqual(
            res.out,
            "cycle 1: app.a -> app.b -> app.a\n"
            "  modules: app.a, app.b\n"
            "  src/app/a.py:1: app.a -> app.b\n"
            "  src/app/b.py:1: app.b -> app.a\n"
            "1 import cycle; modules: 3\n",
        )

    def test_src_absolute_and_nested(self) -> None:
        self.files({"s/t/a.py": "import b\n", "s/t/b.py": "import a\n"})
        self.assertEqual(self.cycles("--src", "s/t"), [["a", "b", "a"]])
        self.assertEqual(self.cycles("--src", str(self.root / "s" / "t")), [["a", "b", "a"]])
        # without --src the names are relative to the folder, so no edge is found
        self.assertEqual(self.cycles(), [])


class Resolving(ToolCase):
    def edges(self) -> set[tuple[str, str]]:
        out = set()
        for line in self.run_tool("--dot").lines():
            if "->" in line:
                a, b = line.strip().rstrip(";").split(" [")[0].split(" -> ")
                out.add((a.strip('"'), b.strip('"')))
        return out

    def test_longest_prefix(self) -> None:
        self.files(
            {
                "a/__init__.py": "",
                "a/b/__init__.py": "",
                "m.py": "import a.b.c.d\nimport a.zzz\nimport os.path\nimport requests\n",
            }
        )
        self.assertEqual(self.edges(), {("m", "a.b"), ("m", "a")})

    def test_import_as_and_several_names(self) -> None:
        self.files({"p.py": "", "q.py": "", "m.py": "import p as x, q\n"})
        self.assertEqual(self.edges(), {("m", "p"), ("m", "q")})

    def test_from_submodule_wins_over_name(self) -> None:
        self.files(
            {
                "pkg/__init__.py": "x = 1\n",
                "pkg/sub.py": "",
                "m.py": "from pkg import sub, x\nfrom pkg import *\n",
            }
        )
        self.assertEqual(self.edges(), {("m", "pkg.sub"), ("m", "pkg")})

    def test_from_a_name_resolves_the_base(self) -> None:
        self.files({"a/__init__.py": "", "a/b.py": "", "m.py": "from a.b import thing\n"})
        self.assertEqual(self.edges(), {("m", "a.b")})

    def test_no_edge_for_parent_packages(self) -> None:
        self.files({"a/__init__.py": "", "a/b/__init__.py": "", "a/b/c.py": "", "m.py": "import a.b.c\n"})
        self.assertEqual(self.edges(), {("m", "a.b.c")})

    def test_relative_imports(self) -> None:
        self.files(
            {
                "p/__init__.py": "",
                "p/y.py": "",
                "p/q/__init__.py": "from . import m\nfrom .. import y\n",
                "p/q/m.py": "from ..y import z\nfrom . import w\nfrom .. import y as yy\n",
                "p/q/w.py": "",
                "top.py": "from . import p\nfrom .p import y\n",
            }
        )
        edges = self.edges()
        self.assertIn(("p.q", "p.q.m"), edges)
        self.assertIn(("p.q", "p.y"), edges)
        self.assertIn(("p.q.m", "p.y"), edges)
        self.assertIn(("p.q.m", "p.q.w"), edges)
        # `top` is a top level module: its package has no parts, level 1 climbs above the top
        self.assertEqual({e for e in edges if e[0] == "top"}, set())

    def test_relative_import_climbing_above_top_is_ignored(self) -> None:
        self.files({"p/__init__.py": "", "p/m.py": "from ... import x\nfrom ...z import w\nfrom .. import p\n"})
        # level 2 in package p (1 part) is above the top; so is level 3
        self.assertEqual(self.edges(), set())

    def test_relative_from_package_to_itself(self) -> None:
        self.files({"pkg/__init__.py": "from . import name\n"})
        self.assertEqual(self.cycles(), [["pkg", "pkg"]])

    def test_relative_in_module_of_package(self) -> None:
        self.files({"pkg/__init__.py": "", "pkg/a.py": "from . import b\n", "pkg/b.py": "from .a import f\n"})
        self.assertEqual(self.cycles(), [["pkg.a", "pkg.b", "pkg.a"]])

    def test_import_module_and_dunder_import(self) -> None:
        self.files(
            {
                "a.py": "import importlib\nimportlib.import_module('b')\n",
                "b.py": "from importlib import import_module\nimport_module('c')\n",
                "c.py": "__import__('a', fromlist=['x'])\n",
            }
        )
        doc = self.run_json()
        self.assertEqual([i["form"] for i in doc["cycles"][0]["imports"]], ["call"] * 3)
        self.assertEqual(doc["cycles"][0]["path"], ["a", "b", "c", "a"])

    def test_calls_that_are_not_imports(self) -> None:
        self.files(
            {
                "t.py": "",
                "m.py": (
                    "import importlib\n"
                    "name = 't'\n"
                    "importlib.import_module(name)\n"
                    "importlib.import_module('t' + '')\n"
                    "importlib.import_module(f't')\n"
                    "importlib.import_module(name='t')\n"
                    "importlib.import_module(*['t'])\n"
                    "importlib.import_module()\n"
                    "imp = __import__\n"
                    "imp('t')\n"
                    "exec('import t')\n"
                    "eval('__import__(\"t\")')\n"
                    "other.import_module('t')\n"
                    "importlib.util.import_module('t')\n"
                    "import_module(b't')\n"
                ),
            }
        )
        self.assertEqual(self.edges(), set())

    def test_call_strings_with_empty_parts_name_nothing(self) -> None:
        self.files({"a.py": "", "m.py": "__import__('')\n__import__('.a')\n__import__('a..b')\n__import__('a.')\n"})
        self.assertEqual(self.edges(), set())

    def test_call_with_dotted_string_and_nested_call(self) -> None:
        self.files({"a/__init__.py": "", "a/b.py": "", "m.py": "__import__('a.b.c', 0)\n"})
        self.assertEqual(self.edges(), {("m", "a.b")})

    def test_self_import_is_a_cycle(self) -> None:
        self.files({"a.py": "import a\n"})
        res = self.run_tool()
        self.assertEqual(
            res.out,
            "cycle 1: a -> a\n  modules: a\n  a.py:1: a -> a\n1 import cycle; modules: 1\n",
        )

    def test_name_shadowing_stdlib_is_project_module(self) -> None:
        self.files({"json.py": "import app\n", "app.py": "import json\n"})
        self.assertEqual(self.cycles(), [["app", "json", "app"]])


class Marks(ToolCase):
    def imports_of_first_cycle(self, *args: str) -> list[dict]:
        return self.run_json(*args)["cycles"][0]["imports"]

    def pair(self, body: str) -> list[dict]:
        self.write("a.py", body)
        self.write("b.py", "import a\n")
        return self.imports_of_first_cycle()[0:1]

    def test_deferred_in_functions(self) -> None:
        for body in [
            "def f():\n    import b\n",
            "async def f():\n    import b\n",
            "def f():\n    class C:\n        import b\n",
            "f = lambda: __import__('b')\n",
            "def f():\n    if True:\n        try:\n            import b\n        except ImportError:\n            pass\n",
        ]:
            with self.subTest(body=body):
                first = self.pair(body)[0]
                self.assertTrue(first["deferred"])
                self.assertFalse(first["typing"])

    def test_not_deferred(self) -> None:
        for body in [
            "class C:\n    import b\n",
            "if True:\n    import b\n",
            "try:\n    import b\nexcept ImportError:\n    b = None\n",
            "with open('x') as f:\n    import b\n",
            "for i in []:\n    import b\n",
            "while False:\n    import b\n",
            "if __name__ == '__main__':\n    import b\n",
            "import b\n",
        ]:
            with self.subTest(body=body):
                first = self.pair(body)[0]
                self.assertFalse(first["deferred"])
                self.assertFalse(first["typing"])

    def test_decorators_defaults_and_annotations_are_not_inside_the_function(self) -> None:
        for body in [
            "@__import__('b').deco\ndef f(): pass\n",
            "def f(x=__import__('b')): pass\n",
            "def f(*, x=__import__('b')): pass\n",
            "def f(x: __import__('b')): pass\n",
            "def f() -> __import__('b'): pass\n",
            "g = lambda x=__import__('b'): x\n",
        ]:
            with self.subTest(body=body):
                self.assertFalse(self.pair(body)[0]["deferred"])

    def test_typing(self) -> None:
        for test in ["TYPE_CHECKING", "typing.TYPE_CHECKING", "t.x.TYPE_CHECKING"]:
            with self.subTest(test=test):
                first = self.pair(f"if {test}:\n    import b\n")[0]
                self.assertTrue(first["typing"])
                self.assertFalse(first["deferred"])

    def test_not_typing(self) -> None:
        for body in [
            "if not TYPE_CHECKING:\n    import b\n",
            "if TYPE_CHECKING:\n    pass\nelse:\n    import b\n",
            "if TYPE_CHECKING or x:\n    import b\n",
            "if TYPE_CHECKING():\n    import b\n",
            "if TYPE_CHECK:\n    import b\n",
            "try:\n    import b\nexcept ImportError:\n    pass\n",
        ]:
            with self.subTest(body=body):
                self.assertFalse(self.pair(body)[0]["typing"])

    def test_elif_type_checking_counts(self) -> None:
        first = self.pair("if x:\n    pass\nelif TYPE_CHECKING:\n    import b\n")[0]
        self.assertTrue(first["typing"])

    def test_both_marks(self) -> None:
        self.write("a.py", "if TYPE_CHECKING:\n    def f():\n        import b\n")
        self.write("b.py", "import a\n")
        res = self.run_tool()
        self.assertIn("  a.py:3: a -> b (deferred, typing)\n", res.out)
        res = self.run_tool("--dot")
        self.assertIn('"a" -> "b" [color=red, style=dotted];', res.out)

    def test_typing_marks_in_plain(self) -> None:
        self.write("a.py", "if TYPE_CHECKING:\n    import b\n")
        self.write("b.py", "import a\n")
        self.assertIn("  a.py:2: a -> b (typing)\n", self.run_tool().out)

    def test_runtime_only_drops_typing_keeps_deferred(self) -> None:
        self.write("a.py", "if TYPE_CHECKING:\n    import b\n")
        self.write("b.py", "def f():\n    import a\n")
        self.assertEqual(self.run_tool().code, 1)
        res = self.run_tool("--runtime-only")
        self.assertEqual((res.code, res.out), (0, "no import cycles; modules: 2\n"))
        self.write("a.py", "def f():\n    import b\n")
        self.assertEqual(self.run_tool("--runtime-only").code, 1)

    def test_runtime_only_lets_a_runtime_import_show(self) -> None:
        self.write("a.py", "if TYPE_CHECKING:\n    import b\nimport b\n")
        self.write("b.py", "import a\n")
        res = self.run_tool("--runtime-only")
        self.assertIn("  a.py:3: a -> b\n", res.out)

    def test_runtime_only_keeps_module_count_and_dot_nodes(self) -> None:
        self.write("a.py", "if TYPE_CHECKING:\n    import b\n")
        self.write("b.py", "")
        self.assertEqual(self.run_tool("--runtime-only").out, "no import cycles; modules: 2\n")
        res = self.run_tool("--runtime-only", "--dot")
        self.assertEqual(res.out, 'digraph imports {\n  "a";\n  "b";\n}\n')


class ShownImport(ToolCase):
    def shown(self, body: str) -> dict:
        self.write("a.py", body)
        self.write("b.py", "import a\n")
        return self.run_json()["cycles"][0]["imports"][0]

    def test_runtime_beats_everything_else(self) -> None:
        shown = self.shown("if TYPE_CHECKING:\n    import b\ndef f():\n    import b\nimport b\n")
        self.assertEqual((shown["line"], shown["deferred"], shown["typing"]), (5, False, False))

    def test_not_typing_before_not_deferred(self) -> None:
        # typing at line 2 (not deferred) loses to deferred at line 4 (not typing)
        shown = self.shown("if TYPE_CHECKING:\n    import b\ndef f():\n    import b\n")
        self.assertEqual((shown["line"], shown["deferred"], shown["typing"]), (4, True, False))

    def test_not_deferred_before_line(self) -> None:
        shown = self.shown("def f():\n    import b\nimport b\n")
        self.assertEqual((shown["line"], shown["deferred"]), (3, False))

    def test_smallest_line(self) -> None:
        shown = self.shown("x = 1\nimport b\nimport b\n")
        self.assertEqual(shown["line"], 2)

    def test_smallest_column_then_position(self) -> None:
        shown = self.shown("import b; import b\n")
        self.assertEqual(shown["line"], 1)
        shown = self.shown("import b, b.c\n")
        self.assertEqual(shown["to"], "b")

    def test_column_decides_between_statement_and_call(self) -> None:
        # same line: the statement at column 0 beats a call that starts later
        shown = self.shown("import b; __import__('b')\n")
        self.assertEqual(shown["form"], "import")
        shown = self.shown("x = __import__('b'); import b\n")
        self.assertEqual(shown["form"], "call")

    def test_line_is_that_of_the_statement(self) -> None:
        shown = self.shown("\n\nfrom b import (\n    x,\n    y,\n)\n")
        self.assertEqual((shown["line"], shown["form"]), (3, "from"))

    def test_line_of_a_call_is_where_the_call_begins(self) -> None:
        shown = self.shown("x = [\n  1,\n  __import__(\n    'b'),\n]\n")
        self.assertEqual((shown["line"], shown["form"]), (3, "call"))

    def test_forms(self) -> None:
        self.assertEqual(self.shown("import b\n")["form"], "import")
        self.assertEqual(self.shown("from b import x\n")["form"], "from")
        self.assertEqual(self.shown("import importlib\nimportlib.import_module('b')\n")["form"], "call")

    def test_import_p_comma_q_is_two_imports_on_one_line(self) -> None:
        self.files({"p.py": "import q, r\n", "q.py": "import p\n", "r.py": "import p\n"})
        doc = self.run_json()
        (cycle,) = [c for c in doc["cycles"] if c["modules"] == ["p", "q", "r"]]
        self.assertEqual(cycle["path"], ["p", "q", "p"])
        self.assertEqual(cycle["imports"][0]["line"], 1)
        self.assertEqual(cycle["imports"][0]["to"], "q")
        dot = self.run_tool("--dot").out
        self.assertIn('"p" -> "q" [color=red];', dot)
        self.assertIn('"p" -> "r" [color=red];', dot)


class Cycles(ToolCase):
    def test_example_of_the_job(self) -> None:
        self.write("a.py", "import b\n")
        self.write("b.py", "x = 1\n\n\ndef f():\n    import a\n")
        res = self.run_tool()
        self.assertEqual(res.code, 1)
        self.assertEqual(res.err, "")
        self.assertEqual(
            res.out,
            "cycle 1: a -> b -> a\n"
            "  modules: a, b\n"
            "  a.py:1: a -> b\n"
            "  b.py:5: b -> a (deferred)\n"
            "1 import cycle; modules: 2\n",
        )

    def test_no_cycle(self) -> None:
        self.files({"a.py": "import b\n", "b.py": "", "c.py": ""})
        res = self.run_tool()
        self.assertEqual((res.code, res.out, res.err), (0, "no import cycles; modules: 3\n", ""))

    def test_shortest_cycle_through_first_module(self) -> None:
        # a -> b -> c -> d -> a (4) and a -> e -> a (2): the shortest wins
        self.files(
            {
                "a.py": "import b\nimport e\n",
                "b.py": "import c\n",
                "c.py": "import d\n",
                "d.py": "import a\n",
                "e.py": "import a\n",
            }
        )
        doc = self.run_json()
        self.assertEqual(doc["cycles"][0]["path"], ["a", "e", "a"])
        self.assertEqual(doc["cycles"][0]["modules"], ["a", "b", "c", "d", "e"])

    def test_tie_break_by_module_order_after_start(self) -> None:
        # two cycles of length 3 through a: via (c, d) and via (b, e)
        self.files(
            {
                "a.py": "import c\nimport b\n",
                "b.py": "import e\n",
                "c.py": "import d\n",
                "d.py": "import a\n",
                "e.py": "import a\n",
            }
        )
        # lists after start: [b, e, a] and [c, d, a]; b < c
        self.assertEqual(self.cycles(), [["a", "b", "e", "a"]])

    def test_tie_break_looks_deeper_than_the_first_step(self) -> None:
        # a -> b -> y -> a and a -> b -> x -> a: x < y
        self.files(
            {
                "a.py": "import b\n",
                "b.py": "import y\nimport x\n",
                "x.py": "import a\n",
                "y.py": "import a\n",
            }
        )
        self.assertEqual(self.cycles(), [["a", "b", "x", "a"]])

    def test_tie_break_does_not_follow_dead_ends(self) -> None:
        # b leads nowhere back: the greedy smallest step must still reach a
        self.files(
            {
                "a.py": "import b\nimport c\n",
                "b.py": "import d\n",
                "c.py": "import a\n",
                "d.py": "",
            }
        )
        self.assertEqual(self.cycles(), [["a", "c", "a"]])

    def test_cycles_in_the_order_of_their_first_modules(self) -> None:
        self.files(
            {
                "z1.py": "import z2\n",
                "z2.py": "import z1\n",
                "m1.py": "import m2\n",
                "m2.py": "import m1\n",
                "a.py": "import a\n",
                "k.py": "import a\nimport m1\n",
            }
        )
        self.assertEqual(self.cycles(), [["a", "a"], ["m1", "m2", "m1"], ["z1", "z2", "z1"]])
        res = self.run_tool()
        self.assertTrue(res.out.endswith("3 import cycles; modules: 6\n"))
        self.assertIn("cycle 3: z1 -> z2 -> z1\n", res.out)

    def test_a_module_is_in_one_cycle_only(self) -> None:
        self.files(
            {
                "a.py": "import b\nimport c\n",
                "b.py": "import a\n",
                "c.py": "import a\n",
            }
        )
        doc = self.run_json()
        self.assertEqual(len(doc["cycles"]), 1)
        self.assertEqual(doc["cycles"][0]["modules"], ["a", "b", "c"])

    def test_self_loop_inside_a_larger_component(self) -> None:
        self.files({"a.py": "import a\nimport b\n", "b.py": "import a\n"})
        self.assertEqual(self.cycles(), [["a", "a"]])

    def test_cycle_through_namespace_and_packages(self) -> None:
        self.files(
            {
                "app/__init__.py": "from app.models import User\n",
                "app/models.py": "import app\n",
            }
        )
        res = self.run_tool()
        self.assertIn("cycle 1: app -> app.models -> app\n", res.out)

    def test_plural_summary(self) -> None:
        self.files({"a.py": "import a\n", "b.py": "import b\n"})
        self.assertTrue(self.run_tool().out.endswith("2 import cycles; modules: 2\n"))

    def test_long_chain_does_not_overflow(self) -> None:
        n = 1500
        for i in range(n):
            self.write(f"m{i:04d}.py", f"import m{(i + 1) % n:04d}\n")
        res = self.run_tool()
        self.assertEqual(res.code, 1)
        self.assertIn(f"modules: {n}\n", res.out)


class Output(ToolCase):
    def setUp(self) -> None:
        super().setUp()
        self.files(
            {
                "a.py": "import b\nimport c\n",
                "b.py": "def f():\n    import a\n",
                "c.py": "if TYPE_CHECKING:\n    import a\n",
                "d.py": "import a\nimport os\n",
            }
        )

    def test_json(self) -> None:
        res = self.run_tool("--json")
        self.assertEqual(res.code, 1)
        self.assertTrue(res.out.endswith("\n"))
        self.assertEqual(res.out.count("\n"), res.out.rstrip("\n").count("\n") + 1)
        doc = json.loads(res.out)
        self.assertEqual(set(doc), {"tool", "version", "modules", "cycles"})
        self.assertEqual((doc["tool"], doc["version"], doc["modules"]), ("import-cycles", "0.1.0", 4))
        (cycle,) = doc["cycles"]
        self.assertEqual(set(cycle), {"modules", "path", "imports"})
        self.assertEqual(cycle["modules"], ["a", "b", "c"])
        self.assertEqual(cycle["path"], ["a", "b", "a"])
        self.assertEqual(
            cycle["imports"],
            [
                {"from": "a", "to": "b", "file": "a.py", "line": 1, "form": "import",
                 "deferred": False, "typing": False},
                {"from": "b", "to": "a", "file": "b.py", "line": 2, "form": "import",
                 "deferred": True, "typing": False},
            ],
        )

    def test_json_without_cycle(self) -> None:
        for name in ["a.py", "b.py", "c.py"]:
            self.write(name, "")
        res = self.run_tool("--json")
        self.assertEqual(res.code, 0)
        self.assertEqual(
            json.loads(res.out),
            {"tool": "import-cycles", "version": "0.1.0", "modules": 4, "cycles": []},
        )

    def test_dot(self) -> None:
        res = self.run_tool("--dot")
        self.assertEqual(res.code, 1)
        self.assertEqual(
            res.out,
            "digraph imports {\n"
            '  "a";\n'
            '  "b";\n'
            '  "c";\n'
            '  "d";\n'
            '  "a" -> "b" [color=red];\n'
            '  "a" -> "c" [color=red];\n'
            '  "b" -> "a" [color=red, style=dashed];\n'
            '  "c" -> "a" [color=red, style=dotted];\n'
            '  "d" -> "a";\n'
            "}\n",
        )

    def test_dot_edge_off_the_path_but_in_the_component_is_red(self) -> None:
        self.write("b.py", "import c\ndef f():\n    import a\n")
        res = self.run_tool("--dot")
        self.assertIn('  "b" -> "c" [color=red];\n', res.out)

    def test_dot_self_loop_and_exit_code_zero(self) -> None:
        self.files({"s.py": "import s\n"})
        self.assertIn('  "s" -> "s" [color=red];\n', self.run_tool("--dot").out)
        for name in ["a.py", "b.py", "c.py", "d.py", "s.py"]:
            self.write(name, "")
        res = self.run_tool("--dot")
        self.assertEqual(res.code, 0)
        self.assertNotIn("->", res.out)

    def test_plain(self) -> None:
        res = self.run_tool()
        self.assertEqual(
            res.out,
            "cycle 1: a -> b -> a\n"
            "  modules: a, b, c\n"
            "  a.py:1: a -> b\n"
            "  b.py:2: b -> a (deferred)\n"
            "1 import cycle; modules: 4\n",
        )

    def test_nothing_on_stderr_on_success(self) -> None:
        for flags in ([], ["--json"], ["--dot"], ["--runtime-only"]):
            self.assertEqual(self.run_tool(*flags).err, "")

    def test_options_in_any_order(self) -> None:
        a = self.run_tool("--json", "--runtime-only", ".", "--exclude", "zzz")
        b = self.run_tool("--exclude", "zzz", "--runtime-only", "--json", str(self.root))
        self.assertEqual(a.out, b.out)

    def test_runtime_only_in_json(self) -> None:
        self.write("b.py", "if TYPE_CHECKING:\n    import a\n")
        self.write("c.py", "import os\n")
        res = self.run_tool("--runtime-only", "--json")
        self.assertEqual(res.code, 0)
        self.assertEqual(res.json()["cycles"], [])
        self.assertEqual(res.json()["modules"], 4)


class ParseErrors(ToolCase):
    def test_syntax_error_report(self) -> None:
        self.files({"a.py": "import b\n", "b.py": "import a\n", "pkg/bad.py": "x = (\n"})
        res = self.run_tool()
        self.assertEqual(res.code, 2)
        self.assertEqual(res.out, "")
        (line,) = res.err.splitlines()
        file, lineno, col, message = line.split(":", 3)
        self.assertEqual((file, lineno), ("pkg/bad.py", "1"))
        self.assertTrue(col.isdigit() and int(col) >= 1)
        self.assertTrue(message.strip())

    def test_line_and_column_of_the_error(self) -> None:
        self.write("e.py", "x = 1\ny = 2\nz = = 3\n")
        res = self.run_tool("--json")
        self.assertEqual(res.code, 2)
        self.assertEqual(res.out, "")
        self.assertTrue(res.err.startswith("e.py:3:5: "), res.err)

    def test_all_failing_files_in_file_order(self) -> None:
        self.files({"z.py": "(", "B/q.py": "(", "a.py": "(", "a/b.py": "(", "ok.py": "import ok\n"})
        res = self.run_tool("--dot")
        self.assertEqual(res.code, 2)
        self.assertEqual(res.out, "")
        files = [l.split(":")[0] for l in res.err.splitlines()]
        self.assertEqual(files, sorted(files))
        self.assertEqual(files, ["B/q.py", "a.py", "a/b.py", "z.py"])

    def test_not_a_syntax_error(self) -> None:
        self.write("nul.py", b"x = 1\x00\n")
        res = self.run_tool()
        self.assertEqual(res.code, 2)
        self.assertEqual(res.out, "")
        self.assertTrue(res.err.startswith("nul.py:"), res.err)

    def test_undecodable_file(self) -> None:
        self.write("bin.py", b"x = '\xff\xfe'\n")
        res = self.run_tool()
        self.assertEqual(res.code, 2)
        self.assertEqual(res.out, "")
        self.assertTrue(res.err.startswith("bin.py:"), res.err)

    def test_folder_named_like_a_file_is_not_a_file(self) -> None:
        path = self.write("a.py", "")
        path.unlink()
        path.mkdir()  # a folder named a.py is not a file: skipped
        self.assertEqual(self.run_tool().code, 0)

    def test_files_not_read_are_not_parsed(self) -> None:
        self.files({"my-x.py": "(", "build/a.py": "(", ".v/a.py": "(", "ex/a.py": "(", "ok.py": ""})
        res = self.run_tool("--exclude", "ex")
        self.assertEqual((res.code, res.err), (0, ""))

    def test_encodings_and_crlf(self) -> None:
        self.write("a.py", b"\xef\xbb\xbfimport b\r\n")  # BOM + CRLF
        self.write("b.py", "# -*- coding: latin-1 -*-\nname = 'caf\u00e9'\nimport a\n".encode("latin-1"))
        res = self.run_tool()
        self.assertEqual(res.code, 1, res.err)
        self.assertIn("  a.py:1: a -> b\n", res.out)
        self.assertIn("  b.py:3: b -> a\n", res.out)

    def test_cr_only_and_mixed_lines(self) -> None:
        self.write("a.py", b"x = 1\rimport b\r\n\nimport b\n")
        self.write("b.py", "import a\n")
        self.assertIn("a.py:2: a -> b", self.run_tool().out)

    def test_syntax_warnings_are_silent(self) -> None:
        self.write("w.py", "x = 'a\\d'\nassert (1, 'x')\ny = 1 is 1\n")
        res = self.run_tool()
        self.assertEqual((res.code, res.err), (0, ""))

    def test_nothing_is_run(self) -> None:
        marker = self.root.parent / "ran"
        self.write("boom.py", f"open({str(marker)!r}, 'w').write('x')\nraise SystemExit(5)\n")
        self.assertEqual(self.run_tool().code, 0)
        self.assertFalse(marker.exists())

    def test_deeply_nested_source(self) -> None:
        self.write("deep.py", "x = " + "[" * 5000 + "]" * 5000 + "\n")
        res = self.run_tool()
        self.assertIn(res.code, (0, 2))
        if res.code == 2:
            self.assertEqual(res.out, "")
            self.assertTrue(res.err.startswith("deep.py:1:"))


class UsageErrors(ToolCase):
    def test_unknown_option(self) -> None:
        for arg in ["--help", "-h", "--src=x", "-x", "--exclude=a", "-", "--Json", "--runtime_only"]:
            with self.subTest(arg=arg):
                self.usage_error(arg)

    def test_missing_values(self) -> None:
        self.usage_error("--src")
        self.usage_error("--exclude")
        self.usage_error("--json", "--exclude")

    def test_value_is_always_the_next_argument(self) -> None:
        (self.root / "--json").mkdir()
        self.write("--json/m.py", "")
        res = self.run_tool("--src", "--json")
        self.assertEqual((res.code, res.out), (0, "no import cycles; modules: 1\n"))
        res = self.run_tool("--exclude", "--json")  # excludes a path called --json
        self.assertEqual((res.code, res.out), (0, "no import cycles; modules: 0\n"))

    def test_second_folder(self) -> None:
        other = self.root.parent / "other"
        other.mkdir()
        self.usage_error(str(self.root), str(other))

    def test_src_twice(self) -> None:
        self.write("s/m.py")
        self.usage_error("--src", "s", "--src", "s")

    def test_empty_exclude(self) -> None:
        for value in ["", ".", "./", "//", "././/."]:
            with self.subTest(value=value):
                self.usage_error("--exclude", value)

    def test_json_with_dot(self) -> None:
        self.usage_error("--json", "--dot")
        self.usage_error("--dot", "--json")

    def test_folder_must_exist_and_be_a_folder(self) -> None:
        self.usage_error("nope")
        self.write("file.txt", "x")
        self.usage_error("file.txt")
        self.usage_error("")

    def test_src_must_exist_and_be_a_folder(self) -> None:
        self.usage_error("--src", "nope")
        self.write("file.py", "x = 1\n")
        self.usage_error("--src", "file.py")

    def test_usage_errors_come_before_reading_files(self) -> None:
        self.write("bad.py", "(")
        res = self.run_tool("--json", "--dot")
        self.assertEqual(res.code, 2)
        self.assertNotIn("bad.py", res.err)

    def test_usage_error_even_with_cycles(self) -> None:
        self.files({"a.py": "import a\n"})
        self.usage_error("--bogus")

    def test_main_is_importable_and_returns_the_code(self) -> None:
        code = (
            "import sys, importlib.util;"
            f"s = importlib.util.spec_from_file_location('ic', {str(TOOL)!r});"
            "m = importlib.util.module_from_spec(s); s.loader.exec_module(m);"
            "sys.exit(m.main(['--json', '--dot']))"
        )
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code], capture_output=True, cwd=str(self.root)
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")


if __name__ == "__main__":
    unittest.main()
