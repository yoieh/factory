import copy
import json
import os
import unittest

from helpers import ROOT, FactoryCase, write

from factory import FactoryError, adapters

BASE = {
    "name": "acme-host",
    "slot": "code_host",
    "version": 1,
    "vendors": ["acme"],
    "capabilities": {
        "push": {"via": "git"},
        "pr_state": {"via": "script", "command": ["python3", "scripts/pr_state.py", "--url", "{pr_url}"],
                     "requires": {"bin": ["python3"]}},
        "pr_template": {"via": "file", "path": "pr-template.md"},
    },
    "settings": {"draft": {"type": "bool", "default": True}},
    "patterns": {"pr_url": "^https://code\\.example\\.com/[a-z]+/pull/[0-9]+$"},
}


def variant(change):
    data = copy.deepcopy(BASE)
    change(data)
    return data


class ValidationTest(unittest.TestCase):
    def test_built_in_adapters_are_valid(self):
        folder = os.path.join(ROOT, "catalogue", "adapters")
        for name in sorted(os.listdir(folder)):
            data, problems = adapters.load(os.path.join(folder, name))
            self.assertEqual(problems, [], name)
            self.assertIsInstance(data["vendors"], list)

    def test_rules(self):
        cases = [
            (lambda d: d.update(name="other"), "must equal the folder name"),
            (lambda d: d.update(slot="chat"), "slot must be one of"),
            (lambda d: d.update(vendors=["Acme Corp"]), "vendors must be a list of lowercase words"),
            (lambda d: d.update(vendors="acme"), "vendors must be a list"),
            (lambda d: d.update(colour="red"), "unknown key 'colour'"),
            (lambda d: d["capabilities"].update(fetch_issue={"via": "mcp"}), "fetch_issue is not a code_host capability"),
            (lambda d: d["capabilities"]["push"].update(via="magic"), "via must be one of"),
            (lambda d: d["capabilities"]["pr_state"].pop("command"), "a script needs command"),
            (lambda d: d["capabilities"]["pr_state"].update(command=["x", "{nothing}"]), "unknown placeholder {nothing}"),
            (lambda d: d["capabilities"]["pr_template"].pop("path"), "a file needs path"),
            (lambda d: d["capabilities"]["push"].update(requires={"bin": "git"}), "requires.bin must be a list"),
            (lambda d: d["capabilities"]["push"].update(requires={"network": []}), "unknown requires key"),
            (lambda d: d["settings"].update(pr_url={"type": "string"}), "setting name 'pr_url' is reserved"),
            (lambda d: d["settings"].update(level={"type": "float"}), "type must be one of"),
            (lambda d: d["settings"].update(level={"type": "int", "default": "high"}), "level must be an integer"),
            (lambda d: d["patterns"].update(pr_url="(unclosed"), "does not compile"),
            (lambda d: d["patterns"].update(pr_url="^[0-9]*$"), "must not match the empty string"),
            (lambda d: d["patterns"].update(pr_url="^\\d+$"), "uses \\d; use [0-9]"),
            (lambda d: d["patterns"].update(key_pattern="^[A-Z]+$"), "key_pattern is not a code_host pattern"),
        ]
        for change, message in cases:
            with self.subTest(message=message):
                problems = adapters.validate(variant(change), "acme-host")
                self.assertTrue(any(message in problem for problem in problems), problems)
        self.assertEqual(adapters.validate(BASE, "acme-host"), [])

    def test_tracker_patterns(self):
        tracker = {"name": "acme-tracker", "slot": "tracker", "capabilities": {"import": {"via": "core"}},
                   "patterns": {"key_pattern": "^[A-Z]+-[0-9]+$", "url_pattern": "^https://x\\.example\\.com/([A-Z]+-[0-9]+)$"}}
        problems = adapters.validate(tracker, "acme-tracker")
        self.assertIn("patterns.url_pattern needs a named group (?P<key>...)", problems)

    def test_key_extraction(self):
        patterns = {"key_pattern": "^[0-9]{1,9}$",
                    "url_pattern": "^https://x\\.example\\.com/[a-z]+/issues/(?P<key>[0-9]{1,9})$"}
        self.assertEqual(adapters.extract_key(patterns, "42"), ("42", None))
        self.assertEqual(adapters.extract_key(patterns, "https://x.example.com/shop/issues/42"),
                         ("42", "https://x.example.com/shop/issues/42"))
        with self.assertRaises(FactoryError):
            adapters.extract_key(patterns, "forty-two")
        with self.assertRaises(FactoryError) as caught:
            adapters.extract_key({"key_pattern": "^.+$"}, "a b")
        self.assertIn("branch name", caught.exception.message)


class AvailabilityTest(FactoryCase):
    def folder(self, data, files=()):
        folder = os.path.join(self.tmp, "adapters", data["name"])
        write(os.path.join(folder, "adapter.json"), json.dumps(data))
        for name in files:
            write(os.path.join(folder, name), "x\n")
        return folder

    def status(self, spec, folder=None, repos=()):
        return adapters.capability_status(folder or self.tmp, spec, list(repos))

    def test_bin_env_and_files(self):
        self.assertEqual(self.status({"via": "cli", "requires": {"bin": ["sh"]}})["status"], "available")
        missing = self.status({"via": "cli", "requires": {"bin": ["acme-cli-not-installed"]}})
        self.assertEqual((missing["status"], missing["reason"]), ("missing", "not on PATH: acme-cli-not-installed"))
        spec = {"via": "cli", "requires": {"env": ["ACME_TOKEN"], "files": ["~/.acme.env"]}}
        self.assertEqual(self.status(spec)["status"], "missing")
        write(os.path.join(self.user, ".acme.env"), "# token\nexport OTHER=1\nexport ACME_TOKEN='abc def'\n")
        self.assertEqual(self.status(spec)["status"], "available")
        os.unlink(os.path.join(self.user, ".acme.env"))
        os.environ["ACME_TOKEN"] = "from-env"
        self.assertEqual(self.status(spec)["status"], "available")

    def test_env_files_read_only_listed_names(self):
        write(os.path.join(self.user, ".acme.env"),
              "export ACME_USER=\"me\"\nACME_TOKEN=secret\nOTHER_SECRET=no\n\n# ACME_HIDDEN=x\n")
        found = adapters.read_env_files(["~/.acme.env", "~/missing.env"], ["ACME_USER", "ACME_TOKEN", "ACME_HIDDEN"])
        self.assertEqual(found, {"ACME_USER": "me", "ACME_TOKEN": "secret"})

    def test_script_not_built_and_file(self):
        folder = self.folder(BASE)
        state = self.status(BASE["capabilities"]["pr_state"], folder)
        self.assertEqual((state["status"], state["reason"]), ("not built", "script not built: scripts/pr_state.py"))
        self.assertEqual(self.status(BASE["capabilities"]["pr_template"], folder)["status"], "missing")
        folder = self.folder(BASE, files=["scripts/pr_state.py", "pr-template.md"])
        self.assertEqual(self.status(BASE["capabilities"]["pr_state"], folder)["status"], "available")
        self.assertEqual(self.status(BASE["capabilities"]["pr_template"], folder)["status"], "available")

    def test_git_core_and_session_checks(self):
        repos = [{"name": "api", "has_origin": True}, {"name": "tools", "has_origin": False}]
        push = self.status({"via": "git"}, repos=repos)
        self.assertEqual((push["status"], push["repos"]), ("partial", {"api": "available", "tools": "missing"}))
        self.assertEqual(self.status({"via": "git"}, repos=repos[:1])["status"], "available")
        self.assertEqual(self.status({"via": "git"}, repos=repos[1:])["status"], "missing")
        self.assertEqual(self.status({"via": "core"})["status"], "available")
        self.assertEqual(self.status({"via": "mcp", "requires": {"mcp": ["mcp__*__x"]}})["status"], "check_in_session")
        self.assertEqual(self.status({"via": "skill", "requires": {"skill": ["acme"]}})["status"], "check_in_session")
        self.assertEqual(self.status({"via": "mcp", "requires": {"bin": ["acme-cli-not-installed"]}})["status"], "missing")

    def test_v1_1_adapters_report_not_built(self):
        folder = os.path.join(ROOT, "catalogue", "adapters")
        for name in sorted(os.listdir(folder)):
            data, _ = adapters.load(os.path.join(folder, name))
            for capability, spec in data["capabilities"].items():
                if spec["via"] != "script":
                    continue
                state = self.status(spec, os.path.join(folder, name))
                if not os.path.exists(os.path.join(folder, name, adapters.script_files(spec["command"])[0])):
                    self.assertEqual(state["status"], "not built", f"{name}.{capability}")

    def test_project_show_reports_not_built(self):
        self.project()
        write(os.path.join(self.home, "catalogue", "adapters", "acme-host", "adapter.json"), json.dumps(BASE))
        shown = self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "acme-host"}')
        capabilities = shown["integrations"]["code_host"]["capabilities"]
        self.assertEqual(capabilities["pr_state"]["status"], "not built")
        self.assertEqual(shown["integrations"]["code_host"]["settings"], {"draft": True})
        self.assertEqual(shown["integrations"]["code_host"]["patterns"], BASE["patterns"])
        overridden = self.cli("project", "set", "shop", "integrations.code_host.patterns",
                              '{"pr_url": "^https://git\\\\.example\\\\.org/[a-z]+/pull/[0-9]+$"}')
        self.assertEqual(overridden["integrations"]["code_host"]["patterns"]["pr_url"],
                         "^https://git\\.example\\.org/[a-z]+/pull/[0-9]+$")

    def test_catalogue_check_includes_adapters(self):
        write(os.path.join(self.home, "catalogue", "adapters", "acme-host", "adapter.json"),
              json.dumps(variant(lambda d: d.update(vendors=["Bad Name"]))))
        result = json.loads(self.call("catalogue", "check")[1])
        self.assertIn("vendors must be a list of lowercase words (^[a-z0-9][a-z0-9_-]*$)",
                      [entry["problem"] for entry in result["problems"]])


if __name__ == "__main__":
    unittest.main()
