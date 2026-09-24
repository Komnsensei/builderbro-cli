#!/usr/bin/env python3
"""source_integrity_test.py — every source file in this tree compiles, or says so.

WHY THIS EXISTS
---------------
A full-repository audit on 2026-09-23 found four Python files that **could not be
parsed at all** while the test suite stayed green:

    dycrag_system/main.py            dycrag_system/agent.py
    NewState/persona_wizard.py       dycrag_system/agent.agent.py

The signature was identical in every one of them: a *raw newline where a `\\n`
escape belonged*, inside a string literal. Nothing imported them, so no test
failed and no run broke — the repository simply carried sources that could not be
executed by anything, including a reviewer.

Two rules follow, and this file is both of them:

1. **A source file that cannot be parsed is a defect even if nothing imports it.**
   Walking the tree is the only check that does not depend on some other file
   happening to reference it.

2. **A generator must be tested on its output, not on its text.** `dycrag_system/
   main.py` writes `agent.py` and `knowledge_base.py` from two embedded string
   templates at import time. Its *source* parsed cleanly while its *output* did
   not: a template holds a real newline, so ``\n`` inside one has to be written
   ``\\n`` to put an escape in the generated file. Both deps were therefore
   regenerated broken on every run — the corruption was the program's normal
   behaviour, and that is the defect this test pins.

Skipped on purpose: `.git`, `__pycache__`, `node_modules`, and anything in
`IGNORE_DIRS`. Those are other people's files or compiled artifacts; the point is
this repository's own sources.

Run: python3 source_integrity_test.py
"""

import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}

DYCRAG = os.path.join(HERE, "dycrag_system")
DYCRAG_TEMPLATES = {
    "knowledge_base_content": os.path.join(DYCRAG, "knowledge_base.py"),
    "agent_content": os.path.join(DYCRAG, "agent.py"),
}


def python_files(root=HERE):
    """Every `.py` file under `root` that is this repository's own source."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".py"):
                yield os.path.join(dirpath, name)


def compile_error(path):
    """None if the file parses, else the `SyntaxError` that stopped it."""
    try:
        with open(path, "rb") as handle:
            compile(handle.read(), path, "exec")
        return None
    except (SyntaxError, ValueError) as exc:
        return exc


class _Corruption(unittest.TestCase):
    """The exact defect found, written out so the detector can be tested on it."""

    RAW_NEWLINE_IN_STRING = 'x = "a\nb"\n'


class EverySourceCompilesTest(unittest.TestCase):

    def test_the_detector_flags_the_corruption_it_was_written_for(self):
        # Negative control. Both spellings on one screen: the first is a raw
        # newline inside a string literal (what four files shipped), the second
        # is the escape it should have been.
        try:
            compile(_Corruption.RAW_NEWLINE_IN_STRING, "<control>", "exec")
            self.fail("a raw newline inside a string literal is not valid Python")
        except SyntaxError as exc:
            self.assertIn("unterminated", str(exc).lower())
        compile('x = "a\\nb"\n', "<control-ok>", "exec")  # the escaped form parses

    def test_every_python_source_file_parses(self):
        failed = []
        for path in python_files():
            exc = compile_error(path)
            if exc is not None:
                failed.append("%s:%s: %s" % (os.path.relpath(path, HERE), exc.lineno, exc.msg))
        self.assertEqual(failed, [], "unparseable sources:\n  " + "\n  ".join(failed))

    def test_the_audit_walks_more_than_the_imported_files(self):
        # A guard that silently found nothing would pass forever. The tree holds
        # files no test imports (`policy_search.py`, `dycrag_system/main.py`).
        found = {os.path.basename(p) for p in python_files()}
        for expected in ("policy_search.py", "main.py", "persona_wizard.py"):
            self.assertIn(expected, found)


@unittest.skipUnless(os.path.isfile(os.path.join(DYCRAG, "main.py")),
                     "the dycrag generator is not in this tree")
class GeneratorTemplatesTest(unittest.TestCase):
    """`main.py` regenerates its own dependencies, so its output is the contract."""

    def templates(self):
        with open(os.path.join(DYCRAG, "main.py"), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        out = {}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and isinstance(node.targets[0], ast.Name)):
                out[node.targets[0].id] = node.value.value
        return out

    def test_each_embedded_template_is_valid_python(self):
        for name, path in DYCRAG_TEMPLATES.items():
            with self.subTest(template=name):
                try:
                    compile(self.templates()[name], path, "exec")
                except SyntaxError as exc:
                    self.fail("%s would be written broken: line %s: %s"
                              % (os.path.relpath(path, HERE), exc.lineno, exc.msg))

    def test_the_written_modules_match_the_templates(self):
        # main.py rewrites both modules on every run, so a hand edit to either
        # file is silently discarded. Equality here means a re-run is a no-op.
        for name, path in DYCRAG_TEMPLATES.items():
            with self.subTest(module=os.path.relpath(path, HERE)):
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), self.templates()[name],
                                     "regenerate with `python3 main.py` in dycrag_system/")


if __name__ == "__main__":
    sys.exit(unittest.main(verbosity=2))
