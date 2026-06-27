#!/usr/bin/env python3
"""No-dependency test runner. Discovers test_* functions in this directory, injects the fixtures they
name as parameters (c_common / ti_core / agent_mod), runs them, and reports. Use this when pytest isn't
installed; `pytest tests/` works too (see conftest.py)."""
import importlib
import inspect
import pathlib
import sys
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _loaders import get_fixture  # noqa: E402

TEST_MODULES = ["test_chapter_c_dsl", "test_chapter_b_core", "test_agent_logic"]


def main():
    passed, failures = 0, []
    for mod_name in TEST_MODULES:
        module = importlib.import_module(mod_name)
        tests = [(n, f) for n, f in inspect.getmembers(module, inspect.isfunction)
                 if n.startswith("test_") and f.__module__ == mod_name]
        for name, fn in sorted(tests):
            args = [get_fixture(p) for p in inspect.signature(fn).parameters]
            try:
                fn(*args)
                passed += 1
            except Exception:
                failures.append((mod_name, name, traceback.format_exc()))
    total = passed + len(failures)
    print(f"\n{passed}/{total} passed")
    for mod_name, name, tb in failures:
        print(f"\nFAILED {mod_name}::{name}\n{tb}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
