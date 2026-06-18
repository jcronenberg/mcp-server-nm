#!/usr/bin/env python3
"""Runner for the NetworkManager MCP server test suite.

Usage:
    python3 run_tests.py                        # all tests
    python3 run_tests.py -v                     # all tests, verbose
    python3 run_tests.py get_connectivity        # single test
    python3 run_tests.py get_connectivity -v     # single test, verbose
    python3 run_tests.py get_devices get_connections  # multiple tests
"""

import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness

ALL_TESTS = [
    "test_get_connectivity",
    "test_get_devices",
    "test_get_connections",
    "test_add_connection",
    "test_modify_connection",
    "test_delete_connection",
    "test_set_connection_state",
    "test_get_hostname",
    "test_set_hostname",
    "test_errors",
]


def main():
    verbose = "-v" in sys.argv
    args = [a for a in sys.argv[1:] if not a.startswith("-")]

    if args:
        selected = []
        for arg in args:
            name = arg if arg.startswith("test_") else f"test_{arg}"
            if name not in ALL_TESTS:
                known = ", ".join(t.removeprefix("test_") for t in ALL_TESTS)
                print(f"Unknown test {arg!r}. Known tests: {known}")
                sys.exit(1)
            selected.append(name)
    else:
        selected = ALL_TESTS

    passed = 0
    failed = 0

    print(f"Running {len(selected)} test(s)...\n")

    for name in selected:
        mod = importlib.import_module(name)
        ok, error = harness.run_test(mod.test, name, verbose=verbose)
        if ok:
            print(f"[ OK ] {name}")
            passed += 1
        else:
            print(f"[FAIL] {name}")
            print(f"   {error}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
