import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ADAPTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(ADAPTER)))
SCRIPT = os.path.join(ADAPTER, "scripts", "sync.py")
FIELDS = "number,title,state,stateReason,url,updatedAt"

FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as handle:
    handle.write(json.dumps({"args": args, "prompt": os.environ.get("GH_PROMPT_DISABLED")}) + "\\n")
for rule in json.loads(os.environ.get("FAKE_GH_RULES", "[]")):
    if args[:len(rule["prefix"])] == rule["prefix"]:
        sys.stdout.write(rule.get("stdout", ""))
        sys.exit(rule.get("exit", 0))
sys.exit(1)
"""


def issue(number, title, state="OPEN", reason=None, updated="2026-10-20T08:00:00Z"):
    return {"number": number, "title": title, "state": state, "stateReason": reason,
            "url": f"https://github.com/example/shop-api/issues/{number}", "updatedAt": updated}


def rules(listed, viewed=None, auth=0, list_exit=0):
    found = [{"prefix": ["auth", "status"], "exit": auth},
             {"prefix": ["issue", "list"], "stdout": json.dumps(listed), "exit": list_exit}]
    for number, data in (viewed or {}).items():
        found.append({"prefix": ["issue", "view", str(number)], "stdout": json.dumps(data) if data else "",
                      "exit": 0 if data else 1})
    return found


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

    def env(self, found_rules, path=True):
        found = dict(os.environ, FAKE_GH_LOG=self.log, FAKE_GH_RULES=json.dumps(found_rules))
        found["PATH"] = (self.bin + os.pathsep if path else "") + self.tools
        return found

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line)["args"] for line in handle]


class SyncScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sync-")
        self.gh = FakeGh(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, found_rules, repository="example/shop-api", keys="12,7", path=True):
        return subprocess.run([sys.executable, SCRIPT, "--repository", repository, "--keys", keys],
                              capture_output=True, text=True, cwd=ADAPTER, env=self.gh.env(found_rules, path))

    def test_list_view_fallback_and_statuses(self):
        done = self.run_script(rules(
            [issue(12, "Fix the cart"), issue(5, "Other")],
            viewed={7: issue(7, "Old one", "CLOSED", "COMPLETED", "2026-10-01T10:00:00+02:00"), 9: None},
        ), keys="12,7,9")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"issues": [
            {"external_key": "12", "title": "Fix the cart", "url": "https://github.com/example/shop-api/issues/12",
             "external_status": "OPEN", "updated_at": "2026-10-20T08:00:00Z"},
            {"external_key": "7", "title": "Old one", "url": "https://github.com/example/shop-api/issues/7",
             "external_status": "CLOSED (COMPLETED)", "updated_at": "2026-10-01T08:00:00Z"},
        ]})
        calls = self.gh.calls()
        self.assertEqual(calls[0], ["auth", "status", "--hostname", "github.com"])
        self.assertEqual(calls[1], ["issue", "list", "--repo", "example/shop-api", "--state", "all", "--limit", "1000",
                                    "--json", FIELDS])
        self.assertIn(["issue", "view", "7", "--repo", "example/shop-api", "--json", FIELDS], calls)

    def test_no_keys(self):
        done = self.run_script([], keys="")
        self.assertEqual((done.returncode, json.loads(done.stdout)), (0, {"issues": []}))
        self.assertEqual(self.gh.calls(), [])

    def test_refusals_and_exit_codes(self):
        self.assertEqual(self.run_script(rules([]), repository="not a repo").returncode, 1)
        self.assertEqual(self.run_script(rules([]), keys="12,abc").returncode, 1)
        self.assertEqual(self.run_script(rules([], auth=1)).returncode, 3)
        self.assertEqual(self.run_script(rules([], list_exit=4)).returncode, 3)
        self.assertEqual(self.run_script(rules([], list_exit=1)).returncode, 1)
        self.assertEqual(self.run_script(rules([]), path=False).returncode, 1)


sys.path.insert(0, os.path.join(ROOT, "tests"))
from helpers import FactoryCase


class CoreWithIssueTrackerTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.gh = FakeGh(self.tmp)
        self.api = self.repo("api")
        self.project(repos=[self.api])
        self.cli("project", "set", "shop", "integrations.tracker",
                 '{"adapter": "github-issues", "repository": "example/shop-api"}')

    def test_keys_and_branch(self):
        os.environ.update(self.gh.env([]))
        run = self.cli("run", "new", "Fix the cart", "--external-key",
                       "https://github.com/example/shop-api/issues/12", "--repo", "api", "--project", "shop")
        self.assertEqual(run["feature_branch"], "feature/12-fix-the-cart")
        issue = self.cli("issue", "show", run["issue_key"])
        self.assertEqual((issue["source"], issue["external_key"]), ("github-issues", "12"))
        self.assertIn("is not a key or issue URL", self.fails("issue", "import", "shop", "SHOP-1", "--title", "x"))

    def test_issue_sync(self):
        self.cli("issue", "import", "shop", "12", "--title", "Fix the cart")
        self.cli("issue", "import", "shop", "7", "--title", "Old one")
        self.cli("issue", "add", "shop", "A local issue")
        os.environ.update(self.gh.env(rules([issue(12, "Fix the cart, now with tests")],
                                            viewed={7: issue(7, "Old one", "CLOSED", "NOT_PLANNED")})))
        result = self.cli("issue", "sync", "shop")
        self.assertEqual(sorted(result["updated"]), ["SHOP-1", "SHOP-2"])
        first = self.cli("issue", "show", "SHOP-1")
        self.assertEqual((first["title"], first["external_status"], first["status"]),
                         ("Fix the cart, now with tests", "OPEN", "open"))
        self.assertEqual(self.cli("issue", "show", "SHOP-2")["external_status"], "CLOSED (NOT_PLANNED)")
        keys = [call for call in self.gh.calls() if call[:2] == ["issue", "list"]]
        self.assertEqual(len(keys), 1)
        os.environ.update(self.gh.env(rules([], auth=1)))
        self.assertIn("credentials missing", self.fails("issue", "sync", "shop", code=3))

    def test_sync_needs_the_repository_setting(self):
        self.cli("project", "set", "shop", "integrations.tracker", '{"adapter": "github-issues"}')
        self.cli("issue", "import", "shop", "12", "--title", "Fix the cart")
        os.environ.update(self.gh.env([]))
        self.assertIn("{repository} has no value", self.fails("issue", "sync", "shop"))


if __name__ == "__main__":
    unittest.main()
