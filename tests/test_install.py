import json
import os
import subprocess
import unittest

from helpers import ROOT, FactoryCase, read

from factory import VERSION, db


class InstallTest(FactoryCase):
    def test_install_into_a_temp_prefix(self):
        prefix = os.path.join(self.tmp, "bin")
        done = subprocess.run(["sh", os.path.join(ROOT, "install.sh"), "--prefix", prefix],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        shim = os.path.join(prefix, "factory")
        self.assertTrue(os.access(shim, os.X_OK))
        current = os.path.join(self.home, "core", "current")
        self.assertEqual(os.readlink(current), VERSION)
        self.assertTrue(os.path.isfile(os.path.join(self.home, "core", VERSION, "factory", "cli.py")))
        self.assertTrue(os.path.isdir(os.path.join(self.home, "core", VERSION, "catalogue", "steps")))
        self.assertFalse(os.path.exists(os.path.join(self.home, "core", VERSION, ".git")))
        version = subprocess.run([shim, "--version"], capture_output=True, text=True)
        self.assertEqual(version.stdout.strip(), f"factory {VERSION}")
        init = subprocess.run([shim, "init"], capture_output=True, text=True)
        self.assertEqual(init.returncode, 0, init.stderr)
        self.assertEqual(json.loads(init.stdout)["home"], self.home)

    def test_install_twice_replaces_the_version_folder(self):
        prefix = os.path.join(self.tmp, "bin")
        for _ in range(2):
            done = subprocess.run(["sh", os.path.join(ROOT, "install.sh"), f"--prefix={prefix}"],
                                  capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
        entries = sorted(os.listdir(os.path.join(self.home, "core")))
        self.assertEqual(entries, [VERSION, "current"])

    def test_install_refuses_unknown_arguments(self):
        done = subprocess.run(["sh", os.path.join(ROOT, "install.sh"), "--root"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 2)
        self.assertIn("unknown argument", done.stderr)

    def test_shim_runs_a_checkout_from_factory_core(self):
        prefix = os.path.join(self.tmp, "bin")
        subprocess.run(["sh", os.path.join(ROOT, "install.sh"), "--prefix", prefix], check=True, capture_output=True)
        env = dict(os.environ, FACTORY_CORE=ROOT)
        done = subprocess.run([os.path.join(prefix, "factory"), "paths", "nope"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 1)
        self.assertIn("factory: no run nope", done.stderr)

    def test_bin_factory_runs_from_the_source(self):
        done = subprocess.run([os.path.join(ROOT, "bin", "factory"), "init"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout)["schema_version"], db.latest_version())


class HomeTest(FactoryCase):
    def test_init_twice(self):
        first = self.cli("init")
        second = self.cli("init")
        self.assertEqual(first, second)
        self.assertTrue(os.path.isfile(os.path.join(self.home, "factory.db")))
        self.assertEqual(json.loads(read(os.path.join(self.home, "config.json")))["parallel"], 3)
        mode = os.stat(self.home).st_mode & 0o777
        self.assertEqual(mode, 0o700)

    def test_a_first_command_creates_a_missing_home(self):
        self.assertFalse(os.path.exists(self.home))
        self.assertEqual(self.cli("project", "list"), [])
        for name in ("config.json", "factory.db", "projects", "issues", "runs", "worktrees", "catalogue/steps"):
            self.assertTrue(os.path.exists(os.path.join(self.home, name)), name)

    def test_doctor_reports_checks_and_the_start_command(self):
        repo = self.repo("api")
        self.project(repos=[repo])
        result = self.cli("doctor", "--project", "shop")
        names = [entry["check"] for entry in result["checks"]]
        for name in ("python", "git", "sqlite", "home", "path"):
            self.assertIn(name, names)
        self.assertEqual(result["start_command"], f"claude --add-dir {repo} --add-dir {self.home}")
        path_check = next(entry for entry in result["checks"] if entry["check"] == "path")
        if not path_check["ok"]:
            self.assertIn("install.sh", path_check["fix"])
        self.assertIn("tracker.fetch_issue", names)

    def test_doctor_without_a_project(self):
        result = self.cli("doctor")
        self.assertNotIn("start_command", result)
        self.assertEqual(result["version"], VERSION)


if __name__ == "__main__":
    unittest.main()
