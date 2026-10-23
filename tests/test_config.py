import json
import os
import unittest

from helpers import FactoryCase, read, write


class ConfigTest(FactoryCase):
    def test_show_and_set(self):
        self.assertEqual(self.cli("config", "show")["max_rounds"], 4)
        result = self.cli("config", "set", "dashboard.port", "9000")
        self.assertEqual(result["dashboard"]["port"], 9000)
        self.cli("config", "set", "factory_source", '"~/src/core"')
        stored = json.loads(read(os.path.join(self.home, "config.json")))
        self.assertEqual(stored["factory_source"], os.path.join(self.user, "src", "core"))

    def test_unknown_keys_are_refused(self):
        self.assertIn("unknown key 'colour'", self.fails("config", "set", "colour", '"red"'))
        self.assertIn("unknown key 'dashboard.host'", self.fails("config", "set", "dashboard.host", '"0.0.0.0"'))
        config = os.path.join(self.home, "config.json")
        self.cli("init")
        data = json.loads(read(config))
        data["extra"] = 1
        write(config, json.dumps(data))
        self.assertIn("unknown key 'extra'", self.fails("config", "show"))

    def test_dashboard_hosts(self):
        self.assertEqual(self.cli("config", "show")["dashboard"]["hosts"], [])
        result = self.cli("config", "set", "dashboard.hosts", '["box.tailnet.ts.net"]')
        self.assertEqual(result["dashboard"]["hosts"], ["box.tailnet.ts.net"])
        for bad in ('"box.tailnet.ts.net"', '["https://box.tailnet.ts.net"]', '["box.tailnet.ts.net:443"]', '["Box"]', '[1]'):
            self.assertIn("dashboard.hosts", self.fails("config", "set", "dashboard.hosts", bad), bad)

    def test_values_are_checked(self):
        self.assertIn("max_rounds", self.fails("config", "set", "max_rounds", "11"))
        self.assertIn("parallel", self.fails("config", "set", "parallel", "true"))
        self.assertIn("absolute", self.fails("config", "set", "catalogue_dirs", '["relative/dir"]'))
        self.assertIn("must be JSON", self.fails("config", "set", "parallel", "three"))

    def test_hand_edited_paths_are_expanded_on_read(self):
        self.cli("init")
        config = os.path.join(self.home, "config.json")
        data = json.loads(read(config))
        data["catalogue_dirs"] = ["~/team-catalogue"]
        write(config, json.dumps(data))
        self.assertEqual(self.cli("config", "show")["catalogue_dirs"], [os.path.join(self.user, "team-catalogue")])


class ProjectConfigTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.project(repos=[self.repo("api")])
        self.config = os.path.join(self.home, "projects", "shop", "project.json")

    def edit(self, change):
        data = json.loads(read(self.config))
        change(data)
        write(self.config, json.dumps(data))

    def test_unknown_keys_at_every_level(self):
        cases = [
            (lambda d: d.update(colour="red"), "unknown key 'colour'"),
            (lambda d: d["repos"][0].update(token="x"), "repos[1]: unknown key 'token'"),
            (lambda d: d.update(knowledge={"read_first": [], "extra": []}), "knowledge: unknown key 'extra'"),
            (lambda d: d.update(defaults={"parallel": 2, "speed": 1}), "defaults: unknown key 'speed'"),
            (lambda d: d.update(step_skills={"code": [{"path": "/x", "when": "y", "z": 1}]}), "unknown key 'z'"),
            (lambda d: d.update(improvement_targets={"repo": {"base": "default", "owner": "me"}}), "unknown key 'owner'"),
            (lambda d: d.update(integrations={"chat": {"adapter": "none"}}), "unknown slot 'chat'"),
        ]
        original = read(self.config)
        for change, message in cases:
            write(self.config, original)
            self.edit(change)
            self.assertIn(message, self.fails("project", "show", "shop"))

    def test_paths_are_expanded_and_must_be_absolute(self):
        self.edit(lambda d: d.update(knowledge={"read_first": ["~/docs/overview.md"]}))
        write(os.path.join(self.user, "docs", "overview.md"), "# docs\n")
        shown = self.cli("project", "show", "shop")
        self.assertEqual(shown["knowledge"]["read_first"], [os.path.join(self.user, "docs", "overview.md")])
        self.edit(lambda d: d.update(knowledge={"read_first": ["docs/overview.md"]}))
        self.assertIn("absolute", self.fails("project", "show", "shop"))

    def test_adapter_settings_are_enforced(self):
        message = self.fails("project", "set", "shop", "integrations.code_host", '{"adapter": "git", "draft": true}')
        self.assertIn("'draft' is not a setting of adapter git", message)
        self.assertIn("no adapter 'nothing'",
                      self.fails("project", "set", "shop", "integrations.code_host", '{"adapter": "nothing"}'))
        self.assertIn("is a tracker adapter",
                      self.fails("project", "set", "shop", "integrations.code_host", '{"adapter": "local"}'))

    def test_a_token_looking_key_is_refused_and_not_saved(self):
        secret = "tok_9f8e7d6c5b4a39281706"
        before = read(self.config)
        message = self.fails("project", "set", "shop", "integrations.code_host",
                             json.dumps({"adapter": "git", "api_token": secret}))
        self.assertIn("'api_token' is not a setting", message)
        self.assertIn("credentials go in the environment", message)
        self.assertNotIn(secret, message)
        self.assertEqual(read(self.config), before)
        events = self.cli("project", "list")
        self.assertNotIn(secret, json.dumps(events))

    def test_setting_types_are_checked(self):
        folder = os.path.join(self.home, "catalogue", "adapters", "acme-host")
        write(os.path.join(folder, "adapter.json"), json.dumps({
            "name": "acme-host", "slot": "code_host", "version": 1, "vendors": [],
            "capabilities": {"push": {"via": "git"}},
            "settings": {"draft": {"type": "bool", "default": True}, "reviewers": {"type": "list", "default": None}},
            "patterns": {},
        }))
        self.assertIn("draft must be true or false", self.fails(
            "project", "set", "shop", "integrations.code_host", '{"adapter": "acme-host", "draft": "yes"}'))
        self.assertIn("reviewers must be a list of strings", self.fails(
            "project", "set", "shop", "integrations.code_host", '{"adapter": "acme-host", "reviewers": "team"}'))
        shown = self.cli("project", "set", "shop", "integrations.code_host",
                         '{"adapter": "acme-host", "reviewers": ["team"]}')
        self.assertEqual(shown["integrations"]["code_host"]["settings"], {"draft": True, "reviewers": ["team"]})

    def test_name_and_key_rules(self):
        self.assertIn("project name", self.fails("project", "add", "Bad_Name", "--key", "BAD"))
        self.assertIn("project key", self.fails("project", "add", "other", "--key", "x1"))
        self.assertIn("is used by project shop", self.fails("project", "add", "other", "--key", "SHOP"))
        self.assertIn("cannot be renamed", self.fails("project", "set", "shop", "name", '"other"'))


if __name__ == "__main__":
    unittest.main()
