import json
import os
import unittest
from unittest import mock

from helpers import ROOT, FactoryCase, read, write
from factory import VERSION

GENERIC = ("factory-planner", "factory-worker", "factory-reviewer", "factory-retro", "factory-improver")
DEDICATED_STEP = """---
name: review-data
role: gate
kinds: [code]
agent: data-checker
summary: Checks data migrations.
when: The job moves data.
---

## Focus
- Data.
"""


class SkillInstallTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.claude = os.path.join(self.user, ".claude")

    def test_user_install_writes_files_and_records_it(self):
        result = self.cli("skill", "install", "--user")
        self.assertEqual((result["scope"], result["path"], result["project"]), ("user", self.claude, None))
        skill = os.path.join(self.claude, "skills", "factory", "SKILL.md")
        self.assertEqual(read(skill), read(os.path.join(ROOT, "skills", "factory", "SKILL.md")))
        for name in GENERIC:
            self.assertEqual(read(os.path.join(self.claude, "agents", f"{name}.md")),
                             read(os.path.join(ROOT, "agents", f"{name}.md")))
        status = self.cli("skill", "status")
        self.assertEqual(status["plugin"], ROOT)
        entry = status["installs"][0]
        self.assertEqual((entry["scope"], entry["path"], entry["current"]), ("user", self.claude, True))
        self.cli("skill", "install", "--user")
        self.assertEqual(len(self.cli("skill", "status")["installs"]), 1)

    def test_repo_install_is_recorded_on_the_project(self):
        api = self.repo("api")
        self.project(repos=[api])
        result = self.cli("skill", "install", "--repo", os.path.join(api, "."))
        self.assertEqual((result["scope"], result["path"], result["project"]), ("repo", os.path.join(api, ".claude"), "shop"))
        self.assertTrue(os.path.isfile(os.path.join(api, ".claude", "skills", "factory", "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(api, ".claude", "agents", "factory-worker.md")))
        shown = self.cli("project", "show", "shop")
        self.assertEqual([(entry["repo"], entry["current"]) for entry in shown["installs"]["repos"]], [("api", True)])
        self.assertIsNone(shown["installs"]["user"])
        loose = self.repo("loose", origin=False)
        self.assertIsNone(self.cli("skill", "install", "--repo", loose)["project"])
        self.assertIn("not inside a git checkout", self.fails("skill", "install", "--repo", self.user))
        self.assertEqual(self.call("skill", "install")[0], 2)

    def test_status_shows_drift(self):
        self.cli("skill", "install", "--user")
        with mock.patch("factory.VERSION", "9.9.9"):
            entry = self.cli("skill", "status")["installs"][0]
        self.assertEqual((entry["current"], entry["version"], entry["core_version"]), (False, VERSION, "9.9.9"))
        write(os.path.join(self.claude, "agents", "factory-worker.md"), "changed by hand\n")
        os.unlink(os.path.join(self.claude, "agents", "factory-retro.md"))
        entry = self.cli("skill", "status")["installs"][0]
        self.assertEqual(entry["changed"], ["agents/factory-worker.md"])
        self.assertEqual(entry["missing"], ["agents/factory-retro.md"])
        self.assertFalse(entry["current"])
        checks = {item["check"]: item for item in self.cli("doctor")["checks"]}
        self.assertTrue(checks["skill"]["ok"])

    def test_uninstall(self):
        self.cli("skill", "install", "--user")
        write(os.path.join(self.claude, "agents", "my-own-agent.md"), "mine\n")
        removed = self.cli("skill", "uninstall", "--user")
        self.assertEqual(len(removed["removed"]), 6)
        self.assertFalse(os.path.exists(os.path.join(self.claude, "skills", "factory")))
        self.assertTrue(os.path.isfile(os.path.join(self.claude, "agents", "my-own-agent.md")))
        self.assertEqual(self.cli("skill", "status")["installs"], [])
        self.assertIn("no factory install", self.fails("skill", "uninstall", "--user"))

    def test_dedicated_agents_from_the_catalogue(self):
        write(os.path.join(self.home, "catalogue", "steps", "review-data.md"), DEDICATED_STEP)
        result = self.cli("skill", "install", "--user")
        self.assertEqual((result["dedicated"], result["missing"]), ([], ["data-checker"]))
        write(os.path.join(self.home, "catalogue", "agents", "data-checker.md"), "---\nname: data-checker\n---\n")
        write(os.path.join(self.home, "catalogue", "agents", "unused-agent.md"), "---\nname: unused-agent\n---\n")
        result = self.cli("skill", "install", "--user")
        self.assertEqual((result["dedicated"], result["missing"]), (["data-checker"], []))
        self.assertTrue(os.path.isfile(os.path.join(self.claude, "agents", "data-checker.md")))
        self.assertFalse(os.path.exists(os.path.join(self.claude, "agents", "unused-agent.md")))
        os.unlink(os.path.join(self.home, "catalogue", "steps", "review-data.md"))
        self.cli("skill", "install", "--user")
        self.assertFalse(os.path.exists(os.path.join(self.claude, "agents", "data-checker.md")))

    def test_plugin_folder(self):
        folder = os.path.join(self.tmp, "my-plugin")
        result = self.cli("skill", "plugin", folder)
        manifest = json.loads(read(os.path.join(folder, ".claude-plugin", "plugin.json")))
        self.assertEqual((manifest["name"], manifest["version"]), ("factory", VERSION))
        self.assertTrue(os.path.isfile(os.path.join(folder, "skills", "factory", "SKILL.md")))
        self.assertTrue(os.path.isfile(os.path.join(folder, "agents", "factory-planner.md")))
        self.assertEqual(result["scope"], "plugin")
        self.cli("skill", "plugin", folder)
        self.assertEqual([entry["scope"] for entry in self.cli("skill", "status")["installs"]], ["plugin"])
        busy = os.path.join(self.tmp, "busy")
        write(os.path.join(busy, "notes.txt"), "x")
        self.assertIn("is not empty", self.fails("skill", "plugin", busy))


class AgentsTest(FactoryCase):
    def test_plugin_then_user_then_repo(self):
        api = self.repo("api")
        self.project(repos=[api])
        found = self.cli("agents", cwd=api)
        self.assertEqual(found["scope"], "plugin")
        self.assertEqual(found["roles"]["worker"], "factory:factory-worker")
        self.assertEqual((found["dedicated"], found["missing"]), ({}, []))
        self.cli("skill", "install", "--user")
        found = self.cli("agents", cwd=api)
        self.assertEqual((found["scope"], found["roles"]["planner"]), ("user", "factory-planner"))
        self.cli("skill", "install", "--repo", api)
        self.assertEqual(self.cli("agents", cwd=api)["scope"], "repo")
        self.assertEqual(self.cli("agents", cwd=self.user)["scope"], "user")

    def test_dedicated_and_missing(self):
        api = self.repo("api")
        self.project(repos=[api])
        write(os.path.join(api, ".factory", "steps", "review-data.md"), DEDICATED_STEP)
        found = self.cli("agents", cwd=api)
        self.assertEqual(found["missing"], ["data-checker"])
        write(os.path.join(self.user, ".claude", "agents", "data-checker.md"), "---\nname: data-checker\n---\n")
        found = self.cli("agents", cwd=api)
        self.assertEqual((found["dedicated"], found["missing"]), ({"data-checker": "data-checker"}, []))
        self.assertEqual(self.cli("agents", cwd=self.user)["dedicated"], {})

    def test_nothing_installed(self):
        with mock.patch("factory.home.is_plugin_folder", return_value=False):
            found = self.cli("agents")
        self.assertIsNone(found["scope"])
        self.assertEqual(sorted(found["missing"]), sorted(GENERIC))
        self.assertIsNone(found["roles"]["planner"])
        with mock.patch("factory.home.is_plugin_folder", return_value=False):
            check = next(item for item in self.cli("doctor")["checks"] if item["check"] == "skill")
        self.assertFalse(check["ok"])
        self.assertIn("skill install --user", check["fix"])


if __name__ == "__main__":
    unittest.main()
