import json
import os
import subprocess
import sys
import unittest

from helpers import ROOT, FactoryCase, read

from factory import VERSION, home


class ManifestTest(unittest.TestCase):
    def manifest(self, name):
        return json.loads(read(os.path.join(ROOT, ".claude-plugin", name)))

    def test_plugin_json(self):
        data = self.manifest("plugin.json")
        self.assertEqual(data["name"], "factory")
        self.assertEqual(data["version"], VERSION)
        self.assertTrue(data["description"])
        self.assertTrue(data["author"]["name"])

    def test_marketplace_json(self):
        data = self.manifest("marketplace.json")
        self.assertRegex(data["name"], r"^[a-z0-9][a-z0-9._-]*$")
        self.assertTrue(data["owner"]["name"])
        self.assertEqual(len(data["plugins"]), 1)
        entry = data["plugins"][0]
        self.assertEqual((entry["name"], entry["source"]), ("factory", "./"))
        self.assertNotIn("version", entry)

    def test_layout(self):
        for path in ("skills/factory/SKILL.md", "bin/factory", "agents/factory-planner.md", "agents/factory-worker.md",
                     "agents/factory-reviewer.md", "agents/factory-retro.md", "agents/factory-improver.md",
                     "agents/factory-lander.md"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, path)), path)
        self.assertTrue(os.access(os.path.join(ROOT, "bin", "factory"), os.X_OK))
        skill = read(os.path.join(ROOT, "skills", "factory", "SKILL.md"))
        self.assertTrue(skill.startswith("---\nname: factory\n"))
        self.assertIn('command -v "${CLAUDE_PLUGIN_ROOT}/bin/factory"', skill)


class CoreSyncTest(FactoryCase):
    def plugin_copy(self):
        folder = os.path.join(self.tmp, "plugin-cache", "factory", VERSION)
        home.copy_core(ROOT, folder)
        return folder

    def run_bin(self, folder, *args):
        return subprocess.run([sys.executable, os.path.join(folder, "bin", "factory")] + list(args),
                              capture_output=True, text=True, env=dict(os.environ))

    def test_a_plugin_folder_copies_itself_and_moves_current(self):
        folder = self.plugin_copy()
        done = self.run_bin(folder, "paths", "nope")
        self.assertIn("no run nope", done.stderr)
        target = os.path.join(self.home, "core", VERSION)
        self.assertTrue(os.path.isfile(os.path.join(target, "factory", "cli.py")))
        self.assertTrue(os.path.isfile(os.path.join(target, ".claude-plugin", "plugin.json")))
        self.assertEqual(os.readlink(os.path.join(self.home, "core", "current")), VERSION)
        self.assertFalse(os.path.exists(os.path.join(target, "factory-spec")))
        os.unlink(os.path.join(self.home, "core", "current"))
        os.symlink("0.0.1", os.path.join(self.home, "core", "current"))
        marker = os.path.join(target, "marker")
        with open(marker, "w") as handle:
            handle.write("kept")
        self.run_bin(folder, "init")
        self.assertEqual(os.readlink(os.path.join(self.home, "core", "current")), VERSION)
        self.assertTrue(os.path.exists(marker))

    def test_the_installed_copy_does_not_copy_again(self):
        folder = self.plugin_copy()
        self.run_bin(folder, "init")
        installed = os.path.join(self.home, "core", VERSION)
        os.unlink(os.path.join(self.home, "core", "current"))
        self.run_bin(installed, "init")
        self.assertFalse(os.path.lexists(os.path.join(self.home, "core", "current")))

    def test_the_installed_copy_knows_where_the_plugin_is(self):
        folder = self.plugin_copy()
        self.run_bin(folder, "init")
        installed = os.path.join(self.home, "core", VERSION)
        status = json.loads(self.run_bin(installed, "skill", "status").stdout)
        self.assertEqual(status["plugin"], folder)
        agents = json.loads(self.run_bin(installed, "agents").stdout)
        self.assertEqual(agents["roles"]["planner"], "factory:factory-planner")

    def test_a_copy_from_install_sh_is_not_the_plugin(self):
        prefix = os.path.join(self.tmp, "bin")
        done = subprocess.run(["sh", os.path.join(ROOT, "install.sh"), "--prefix", prefix], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)
        installed = os.path.join(self.home, "core", VERSION)
        self.assertTrue(os.path.isfile(os.path.join(installed, ".claude-plugin", "plugin.json")))
        status = json.loads(self.run_bin(installed, "skill", "status").stdout)
        self.assertIsNone(status["plugin"])
        agents = json.loads(self.run_bin(installed, "agents").stdout)
        self.assertEqual((agents["scope"], agents["roles"]["planner"]), (None, None))
        doctor = json.loads(self.run_bin(installed, "doctor").stdout)
        skill = next(item for item in doctor["checks"] if item["check"] == "skill")
        self.assertFalse(skill["ok"])

    def test_a_folder_without_plugin_json_is_left_alone(self):
        folder = self.plugin_copy()
        os.unlink(os.path.join(folder, ".claude-plugin", "plugin.json"))
        self.run_bin(folder, "init")
        self.assertFalse(os.path.exists(os.path.join(self.home, "core", VERSION)))


if __name__ == "__main__":
    unittest.main()
