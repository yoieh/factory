import importlib.util
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADAPTERS = os.path.join(ROOT, "catalogue", "adapters")


def adapter_test_files():
    found = []
    for name in sorted(os.listdir(ADAPTERS)) if os.path.isdir(ADAPTERS) else []:
        folder = os.path.join(ADAPTERS, name, "tests")
        if not os.path.isdir(folder):
            continue
        for entry in sorted(os.listdir(folder)):
            if entry.startswith("test_") and entry.endswith(".py"):
                found.append((name, os.path.join(folder, entry)))
    return found


def load_tests(loader, tests, pattern):
    for name, path in adapter_test_files():
        module_name = "adapter_tests_" + name.replace("-", "_") + "_" + os.path.basename(path)[:-3]
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tests.addTests(loader.loadTestsFromModule(module))
    return tests
