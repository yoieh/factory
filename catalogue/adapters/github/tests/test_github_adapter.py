import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ADAPTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(ADAPTER)))
SCRIPT = os.path.join(ADAPTER, "scripts", "pr_state.py")
URL = "https://github.com/example/shop-api/pull/12"

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as handle:
    handle.write(json.dumps({"args": args, "prompt": os.environ.get("GH_PROMPT_DISABLED")}) + "\\n")
for rule in json.loads(os.environ.get("FAKE_GH_RULES", "[]")):
    if args[:len(rule["prefix"])] == rule["prefix"]:
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        sys.exit(rule.get("exit", 0))
sys.exit(1)
"""


class FakeGh:
    def __init__(self, folder):
        self.bin = os.path.join(folder, "fake-bin")
        os.makedirs(self.bin, exist_ok=True)
        path = os.path.join(self.bin, "gh")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(FAKE_GH)
        os.chmod(path, 0o755)
        self.log = os.path.join(folder, "gh.log")
        self.tools = os.path.join(folder, "tools")
        os.makedirs(self.tools, exist_ok=True)
        for name, target in (("python3", sys.executable), ("git", shutil.which("git"))):
            if target and not os.path.exists(os.path.join(self.tools, name)):
                os.symlink(target, os.path.join(self.tools, name))

    def env(self, rules, path=True):
        found = dict(os.environ, FAKE_GH_LOG=self.log, FAKE_GH_RULES=json.dumps(rules))
        found["PATH"] = (self.bin + os.pathsep if path else "") + self.tools
        return found

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]


def view(state, url=URL, exit_code=0):
    return [{"prefix": ["pr", "view"], "stdout": json.dumps({"state": state, "url": url}), "exit": exit_code}]


class PrStateScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pr-state-")
        self.gh = FakeGh(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, rules, url=URL, path=True, extra=None):
        env = self.gh.env(rules, path)
        env.update(extra or {})
        return subprocess.run([sys.executable, SCRIPT, "--url", url], capture_output=True, text=True, cwd=ADAPTER, env=env)

    def test_states(self):
        for state, expected in (("OPEN", "OPEN"), ("MERGED", "MERGED"), ("CLOSED", "DECLINED")):
            done = self.run_script(view(state))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads(done.stdout), {"state": expected, "url": URL})
        call = self.gh.calls()[0]
        self.assertEqual(call["args"], ["pr", "view", URL, "--json", "state,url"])
        self.assertEqual(call["prompt"], "1")

    def test_refuses_a_url_of_another_host(self):
        done = self.run_script(view("OPEN"), url="https://code.example.com/a/b/pull/1")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(self.gh.calls(), [])

    def test_exit_codes(self):
        self.assertEqual(self.run_script([{"prefix": ["pr", "view"], "exit": 4}]).returncode, 3)
        secret = "gho_secret_token_value_1234"
        failed = self.run_script([{"prefix": ["pr", "view"], "exit": 1, "stderr": "boom " + secret}],
                                 extra={"GH_TOKEN": secret})
        self.assertEqual(failed.returncode, 1)
        self.assertNotIn(secret, failed.stderr + failed.stdout)
        self.assertEqual(len(failed.stderr.strip().splitlines()), 1)
        self.assertEqual(self.run_script(view("OPEN"), path=False).returncode, 1)
        self.assertEqual(self.run_script([{"prefix": ["pr", "view"], "stdout": "not json"}]).returncode, 1)
        self.assertEqual(self.run_script(view("DRAFT")).returncode, 1)


sys.path.insert(0, os.path.join(ROOT, "tests"))
from helpers import FactoryCase


class CoreWithPrStateTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.gh = FakeGh(self.tmp)
        self.api = self.repo("api")
        self.project(repos=[self.api])
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "github"}')
        self.cli("issue", "add", "shop", "Add a lead form")

    def use_gh(self, rules):
        os.environ.update(self.gh.env(rules))

    def test_availability_needs_gh(self):
        os.environ["PATH"] = self.gh.tools
        shown = self.cli("project", "show", "shop")
        state = shown["integrations"]["code_host"]["capabilities"]["pr_state"]
        self.assertEqual(state["status"], "missing")
        self.use_gh(view("OPEN"))
        shown = self.cli("project", "show", "shop")
        self.assertEqual(shown["integrations"]["code_host"]["capabilities"]["pr_state"]["status"], "available")
        self.assertEqual(shown["integrations"]["code_host"]["capabilities"]["push"]["status"], "available")

    def test_run_pr_and_prs(self):
        self.use_gh(view("OPEN"))
        run = self.cli("run", "new", "Add a lead form", "--issue", "SHOP-1", "--repo", "api", "--project", "shop")
        self.assertFalse(run["no_pr"])
        self.assertIn("does not match the github pr_url pattern",
                      self.fails("run", "pr", run["id"], "https://code.example.com/a/b/pull/1", "--repo", "api"))
        stored = self.cli("run", "pr", run["id"], URL, "--repo", "api")
        self.assertEqual(stored["repos"][0]["pr_url"], URL)
        self.assertEqual(self.cli("issue", "show", "SHOP-1")["status"], "in_review")
        checked = self.cli("run", "prs", run["id"])
        self.assertEqual(checked["prs"][0]["state"], "OPEN")
        self.use_gh(view("MERGED"))
        checked = self.cli("run", "prs")
        self.assertEqual((checked["prs"][0]["state"], checked["issues_done"]), ("MERGED", ["SHOP-1"]))
        self.assertEqual(self.cli("issue", "show", "SHOP-1")["status"], "done")
        calls = len(self.gh.calls())
        again = self.cli("run", "prs", run["id"])
        self.assertEqual((again["prs"][0]["checked"], again["prs"][0]["reason"]), (False, "final state"))
        self.assertEqual(len(self.gh.calls()), calls)
        self.cli("run", "prs", run["id"], "--force")
        self.assertEqual(len(self.gh.calls()), calls + 1)

    def test_missing_credentials_are_unchecked(self):
        self.use_gh([{"prefix": ["pr", "view"], "exit": 4}])
        run = self.cli("run", "new", "Add a lead form", "--repo", "api", "--project", "shop")
        self.cli("run", "pr", run["id"], URL, "--repo", "api")
        checked = self.cli("run", "prs", run["id"])
        self.assertEqual((checked["prs"][0]["state"], checked["prs"][0]["checked"]), ("unchecked", False))
        self.assertIn("credentials missing", checked["prs"][0]["reason"])
        self.assertIsNone(self.cli("run", "show", run["id"])["repos"][0]["pr_state"])

    def test_adapter_run(self):
        self.use_gh(view("MERGED"))
        result = self.cli("adapter", "run", "github", "pr_state", "--project", "shop", "--repo", "api", "--pr_url", URL)
        self.assertEqual(result, {"state": "MERGED", "url": URL})
        self.use_gh([{"prefix": ["pr", "view"], "exit": 4}])
        self.assertIn("credentials missing",
                      self.fails("adapter", "run", "github", "pr_state", "--project", "shop", "--pr_url", URL, code=3))
        self.assertIn("{pr_url} has no value", self.fails("adapter", "run", "github", "pr_state", "--project", "shop"))
        self.assertIn("done by the orchestrator",
                      self.fails("adapter", "run", "github", "open_pr", "--project", "shop"))


if __name__ == "__main__":
    unittest.main()
