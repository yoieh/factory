import json
import os
import unittest
from unittest import mock

from fake_adapters import PR_URL, install
from helpers import FactoryCase, git, read, write

from factory import land

CHECKS_SCRIPT = """import json, os, sys
queue = os.environ.get("LAND_QUEUE")
if queue and os.path.exists(queue):
    with open(queue) as handle:
        items = json.load(handle)
    found = items.pop(0) if len(items) > 1 else items[0]
    with open(queue, "w") as handle:
        json.dump(items, handle)
    print(json.dumps(found))
else:
    print(os.environ["LAND_CHECKS"])
"""

LAND_SCRIPT = """import json, os, sys
args = sys.argv[1:]
with open(os.environ["LAND_LOG"], "a") as handle:
    handle.write(json.dumps(args) + "\\n")
if os.environ.get("LAND_REFUSE"):
    sys.stderr.write("land_pr: the merge was refused\\n")
    sys.exit(1)
action = args[args.index("--action") + 1]
print(json.dumps({"action": "merged" if action == "merge" else "ready", "url": args[args.index("--url") + 1]}))
"""

STATE_SCRIPT = """import json, os, sys
print(json.dumps({"state": os.environ.get("LAND_STATE", "OPEN"), "url": sys.argv[-1]}))
"""


def land_host(name="acme-land"):
    script = {"via": "script"}
    return {
        "data": {
            "name": name,
            "slot": "code_host",
            "version": 1,
            "vendors": [],
            "capabilities": {
                "push": {"via": "git"},
                "pr_state": dict(script, command=["python3", "scripts/pr_state.py", "--url", "{pr_url}"]),
                "pr_checks": dict(script, command=["python3", "scripts/pr_checks.py", "--url", "{pr_url}"]),
                "land_pr": dict(script, command=["python3", "scripts/land_pr.py", "--url", "{pr_url}", "--action", "{action}",
                                                 "--head", "{head_sha}", "--method", "{merge_method}"]),
            },
            "settings": {"merge_method": {"type": "string", "default": "squash"}},
            "patterns": {"pr_url": "^https://code\\.example\\.com/[a-z]+/[a-z]+/pull/[0-9]+$"},
        },
        "scripts": {"scripts/pr_state.py": STATE_SCRIPT, "scripts/pr_checks.py": CHECKS_SCRIPT,
                    "scripts/land_pr.py": LAND_SCRIPT},
    }


class LandTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.tmp, "land.log")
        os.environ["LAND_LOG"] = self.log
        self.api = self.repo("api")
        self.project(repos=[self.api])
        install(self.home, land_host())
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "acme-land"}')
        self.cli("issue", "add", "shop", "Lead form")
        self.run = self.cli("run", "new", "Lead form", "--issue", "SHOP-1", "--repo", "api")
        self.worktree = self.feature_worktree(self.run, "api")
        self.head = self.commit(self.worktree)
        self.cli("run", "pr", self.run["id"], PR_URL, "--repo", "api")
        for status in ("running", "finishing", "done"):
            self.cli("run", "status", self.run["id"], status)
        self.checks()

    def checks(self, **changes):
        found = {"state": "OPEN", "url": PR_URL, "draft": True, "head_sha": self.head, "mergeable": "MERGEABLE",
                 "review": "", "checks": "passing", "failing": [], "pending": []}
        found.update(changes)
        os.environ["LAND_CHECKS"] = json.dumps(found)
        return found

    def mode(self, value):
        self.cli("project", "set", "shop", "defaults.land", json.dumps(value))

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def review(self, started, verdict="PASS"):
        write(started["review_file"], f"VERDICT: {verdict}\nThe PR is ready.\n\n## Tests\n`make test`: 3 passed\n")

    def land(self, verdict="PASS", *flags):
        started = self.cli("land", "start", self.run["id"], "--repo", "api")
        self.review(started, verdict)
        return self.cli("land", "apply", self.run["id"], "--repo", "api", *flags)

    def test_off_by_default_reviews_but_changes_nothing(self):
        started = self.cli("land", "start", self.run["id"], "--repo", "api")
        self.assertEqual(started["mode"], "off")
        self.assertEqual((started["head_sha"], started["round"], started["previous"]), (self.head, 1, "none"))
        self.assertEqual(started["feature_worktree"], self.worktree)
        self.assertEqual(json.loads(read(started["checks_file"]))["checks"], "passing")
        self.review(started)
        applied = self.cli("land", "apply", self.run["id"], "--repo", "api")
        self.assertEqual((applied["verdict"], applied["action"]), ("PASS", "none"))
        self.assertIn("landing is off for project shop (defaults.land)", applied["reasons"])
        self.assertEqual(self.calls(), [])

    def test_merge_mode_merges_the_judged_head_and_finishes_the_issue(self):
        self.mode("merge")
        os.environ["LAND_STATE"] = "MERGED"
        applied = self.land()
        self.assertEqual((applied["action"], applied["reasons"]), ("merged", []))
        self.assertEqual(self.calls(), [["--url", PR_URL, "--action", "merge", "--head", self.head, "--method", "squash"]])
        self.assertEqual(applied["issues_done"], ["SHOP-1"])
        self.assertEqual(self.cli("issue", "show", "SHOP-1")["status"], "done")
        events = [event for event in self.cli("run", "show", self.run["id"])["events"] if event["kind"] == "land.apply"]
        self.assertEqual(events[-1]["payload"]["action"], "merged")

    def test_ready_mode_marks_a_draft_ready(self):
        self.mode("ready")
        applied = self.land()
        self.assertEqual(applied["action"], "ready")
        self.assertEqual(self.calls()[0][3], "ready")
        self.checks(draft=False)
        again = self.land()
        self.assertEqual((again["action"], again["reasons"]), ("none", ["the PR is already ready for review"]))

    def test_nothing_lands_without_pass_green_checks_and_the_same_head(self):
        self.mode("merge")
        self.assertEqual(self.land("CHANGES")["reasons"], ["the review says CHANGES"])
        cases = [
            ({"checks": "failing", "failing": ["unit"]}, "checks fail: unit"),
            ({"checks": "pending", "pending": ["e2e"]}, "checks still run: e2e"),
            ({"mergeable": "CONFLICTING"}, "the PR conflicts with its base branch"),
            ({"mergeable": "UNKNOWN"}, "the code host has not yet said whether the PR merges cleanly"),
            ({"review": "CHANGES_REQUESTED"}, "a reviewer asked for changes on the PR"),
        ]
        for changes, reason in cases:
            self.checks(**changes)
            applied = self.land()
            self.assertEqual((applied["action"], applied["reasons"]), ("none", [reason]), changes)
        self.checks()
        started = self.cli("land", "start", self.run["id"], "--repo", "api")
        self.review(started)
        self.checks(head_sha="f" * 40)
        moved = self.cli("land", "apply", self.run["id"], "--repo", "api")
        self.assertEqual(moved["reasons"], ["the PR has commits the review did not see; land again"])
        self.checks(state="MERGED")
        self.assertEqual(self.cli("land", "apply", self.run["id"], "--repo", "api")["reasons"], ["the PR is MERGED"])
        self.assertEqual(self.calls(), [])

    def test_dry_run_and_a_refused_merge(self):
        self.mode("merge")
        dry = self.land("PASS", "--dry-run")
        self.assertEqual((dry["action"], dry["reasons"]), ("would_merge", []))
        self.assertEqual(self.calls(), [])
        os.environ["LAND_REFUSE"] = "1"
        refused = self.cli("land", "apply", self.run["id"], "--repo", "api")
        self.assertEqual(refused["action"], "none")
        self.assertEqual(refused["reasons"], ["land_pr of acme-land: land_pr: the merge was refused"])
        self.assertEqual(self.cli("issue", "show", "SHOP-1")["status"], "in_review")

    def test_review_file_missing_invalid_and_saved(self):
        self.mode("merge")
        started = self.cli("land", "start", self.run["id"], "--repo", "api")
        self.assertEqual(self.cli("land", "apply", self.run["id"], "--repo", "api")["verdict"], "MISSING")
        write(started["review_file"], "Looks good to me\n")
        self.assertEqual(self.cli("land", "apply", self.run["id"], "--repo", "api")["verdict"], "INVALID")
        with mock.patch("sys.stdin") as stdin:
            stdin.read.return_value = "VERDICT: PASS\nFine."
            saved = self.cli("land", "save", self.run["id"], "--repo", "api")
        self.assertEqual(saved["saved"], started["review_file"])
        self.assertEqual(read(started["review_file"]), "VERDICT: PASS\nFine.\n")
        self.assertEqual(self.cli("land", "apply", self.run["id"], "--repo", "api")["action"], "merged")

    def test_rounds_count_up_and_list_the_previous_ones(self):
        first = self.cli("land", "start", self.run["id"], "--repo", "api")
        second = self.cli("land", "start", self.run["id"], "--repo", "api")
        self.assertEqual((second["round"], second["previous"]), (2, first["round_dir"]))
        self.assertTrue(second["review_file"].endswith(os.path.join("land", "api", "round-2", "review.md")))

    def test_start_refusals(self):
        self.assertIn("has no PR for repo web", self.fails("land", "start", self.run["id"], "--repo", "web"))
        self.checks(head_sha="e" * 40)
        self.assertIn("is not the feature worktree's HEAD",
                      self.fails("land", "start", self.run["id"], "--repo", "api"))
        self.checks(state="DECLINED")
        self.assertIn("is DECLINED; only an open PR lands", self.fails("land", "start", self.run["id"], "--repo", "api"))
        self.checks()
        self.cli("run", "status", self.run["id"], "running")
        self.assertIn("is running; land a run only when it is done",
                      self.fails("land", "start", self.run["id"], "--repo", "api"))
        self.assertIn("has no land round", self.fails("land", "apply", self.run["id"], "--repo", "api"))

    def test_check_waits_while_checks_run(self):
        queue = os.path.join(self.tmp, "queue.json")
        pending = self.checks(checks="pending", pending=["unit"])
        passing = self.checks()
        write(queue, json.dumps([pending, pending, passing]))
        os.environ["LAND_QUEUE"] = queue
        first = self.cli("land", "check", self.run["id"])
        self.assertEqual((first["mode"], first["waiting"]), ("off", ["api"]))
        self.assertEqual(first["repos"][0]["pending"], ["unit"])
        with mock.patch.object(land, "POLL", 0.01):
            waited = self.cli("land", "check", self.run["id"], "--wait", "5")
        self.assertEqual((waited["waiting"], waited["repos"][0]["checks"]), ([], "passing"))

    def test_without_the_capabilities_nothing_lands(self):
        self.mode("merge")
        data = land_host()["data"]
        del data["capabilities"]["land_pr"], data["capabilities"]["pr_checks"]
        install(self.home, {"data": data, "scripts": {"scripts/pr_state.py": STATE_SCRIPT}})
        checked = self.cli("land", "check", self.run["id"])
        self.assertEqual(checked["repos"][0]["checks"], "unchecked")
        applied = self.land()
        self.assertEqual(applied["action"], "none")
        self.assertEqual(applied["reasons"], ["the PR checks are unchecked: the code host acme-land has no pr_checks"])

    def test_project_validates_the_mode(self):
        self.assertIn("defaults.land must be one of off, ready, merge",
                      self.fails("project", "set", "shop", "defaults.land", '"yes"'))


if __name__ == "__main__":
    unittest.main()
