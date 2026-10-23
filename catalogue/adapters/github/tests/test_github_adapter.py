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
CHECKS = os.path.join(ADAPTER, "scripts", "pr_checks.py")
LAND = os.path.join(ADAPTER, "scripts", "land_pr.py")
HEAD = "a" * 40
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


def rollup(*entries, state="OPEN", draft=True, head=HEAD, mergeable="MERGEABLE", review=""):
    data = {"state": state, "url": URL, "isDraft": draft, "headRefOid": head, "mergeable": mergeable,
            "reviewDecision": review, "statusCheckRollup": list(entries)}
    return [{"prefix": ["pr", "view"], "stdout": json.dumps(data)}]


def run_check(name, status="COMPLETED", conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def status_check(name, state):
    return {"__typename": "StatusContext", "context": name, "state": state}


class ScriptCase(unittest.TestCase):
    script = None

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="land-scripts-")
        self.gh = FakeGh(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_script(self, rules, *args):
        return subprocess.run([sys.executable, self.script] + list(args), capture_output=True, text=True, cwd=ADAPTER,
                              env=self.gh.env(rules))


class PrChecksScriptTest(ScriptCase):
    script = CHECKS

    def checks(self, rules):
        done = self.run_script(rules, "--url", URL)
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(done.stdout)

    def test_passing_failing_pending_and_none(self):
        found = self.checks(rollup(run_check("unit"), run_check("lint", conclusion="SKIPPED"), status_check("ci", "SUCCESS")))
        self.assertEqual(found, {"state": "OPEN", "url": URL, "draft": True, "head_sha": HEAD, "mergeable": "MERGEABLE",
                                 "review": "", "checks": "passing", "failing": [], "pending": []})
        found = self.checks(rollup(run_check("unit", conclusion="FAILURE"), run_check("e2e", status="IN_PROGRESS")))
        self.assertEqual((found["checks"], found["failing"], found["pending"]), ("failing", ["unit"], ["e2e"]))
        found = self.checks(rollup(run_check("unit"), status_check("deploy", "PENDING")))
        self.assertEqual((found["checks"], found["pending"]), ("pending", ["deploy"]))
        found = self.checks(rollup(status_check("old", "ERROR")))
        self.assertEqual(found["failing"], ["old"])
        found = self.checks(rollup(state="CLOSED", draft=False, mergeable="CONFLICTING", review="CHANGES_REQUESTED"))
        self.assertEqual((found["state"], found["draft"], found["mergeable"], found["review"], found["checks"]),
                         ("DECLINED", False, "CONFLICTING", "CHANGES_REQUESTED", "none"))
        self.assertEqual(self.gh.calls()[0]["args"][:4], ["pr", "view", URL, "--json"])

    def test_refusals(self):
        self.assertEqual(self.run_script(rollup(), "--url", "https://code.example.com/a/b/pull/1").returncode, 1)
        self.assertEqual(self.run_script([{"prefix": ["pr", "view"], "exit": 4}], "--url", URL).returncode, 3)
        self.assertEqual(self.run_script(rollup(state="DRAFT"), "--url", URL).returncode, 1)


class LandPrScriptTest(ScriptCase):
    script = LAND

    def land(self, rules, action="merge", head=HEAD, method="squash"):
        return self.run_script(rules + [{"prefix": ["pr", "ready"]}, {"prefix": ["pr", "merge"]}],
                               "--url", URL, "--action", action, "--head", head, "--method", method)

    def test_merge_marks_ready_then_merges_the_judged_head(self):
        done = self.land(rollup())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(json.loads(done.stdout), {"action": "merged", "url": URL})
        calls = [call["args"] for call in self.gh.calls()][1:]
        self.assertEqual(calls, [["pr", "ready", URL], ["pr", "merge", URL, "--squash", "--match-head-commit", HEAD]])

    def test_ready_only_and_not_twice(self):
        done = self.land(rollup(), action="ready")
        self.assertEqual(json.loads(done.stdout), {"action": "ready", "url": URL})
        self.assertEqual([call["args"][:2] for call in self.gh.calls()], [["pr", "view"], ["pr", "ready"]])
        self.land(rollup(draft=False), action="ready")
        self.assertEqual([call["args"][:2] for call in self.gh.calls()][2:], [["pr", "view"]])

    def test_refusals_change_nothing(self):
        for rules, args in ((rollup(head="b" * 40), {}), (rollup(state="MERGED"), {}), (rollup(), {"head": "abc"}),
                            (rollup(), {"method": "admin"}), (rollup(), {"action": "close"})):
            self.assertEqual(self.land(rules, **args).returncode, 1, args)
        self.assertEqual({tuple(call["args"][:2]) for call in self.gh.calls()}, {("pr", "view")})
        self.assertEqual(self.land([{"prefix": ["pr", "view"], "exit": 4}]).returncode, 3)
        failed = self.run_script(rollup() + [{"prefix": ["pr", "ready"]}, {"prefix": ["pr", "merge"], "exit": 1}],
                                 "--url", URL, "--action", "merge", "--head", HEAD, "--method", "squash")
        self.assertEqual((failed.returncode, failed.stderr.strip()), (1, "land_pr: gh pr merge failed with exit 1"))
        self.assertFalse(any("--admin" in call["args"] or "--auto" in call["args"] for call in self.gh.calls()))



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

    def test_land_merges_through_gh(self):
        self.cli("project", "set", "shop", "defaults.land", '"merge"')
        run = self.cli("run", "new", "Add a lead form", "--issue", "SHOP-1", "--repo", "api", "--project", "shop")
        worktree = self.feature_worktree(run, "api")
        head = self.commit(worktree)
        self.use_gh(view("OPEN"))
        self.cli("run", "pr", run["id"], URL, "--repo", "api")
        for status in ("running", "finishing", "done"):
            self.cli("run", "status", run["id"], status)
        self.use_gh(rollup(run_check("unit"), head=head))
        started = self.cli("land", "start", run["id"], "--repo", "api")
        with open(started["review_file"], "w", encoding="utf-8") as handle:
            handle.write("VERDICT: PASS\nReady.\n")
        merged = [dict(view("MERGED")[0], prefix=["pr", "view", URL, "--json", "state,url"])]
        self.use_gh(merged + rollup(run_check("unit"), head=head) + [{"prefix": ["pr", "ready"]}, {"prefix": ["pr", "merge"]}])
        applied = self.cli("land", "apply", run["id"], "--repo", "api")
        self.assertEqual((applied["action"], applied["issues_done"]), ("merged", ["SHOP-1"]))
        merge = [call["args"] for call in self.gh.calls() if call["args"][:2] == ["pr", "merge"]]
        self.assertEqual(merge, [["pr", "merge", URL, "--squash", "--match-head-commit", head]])

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
