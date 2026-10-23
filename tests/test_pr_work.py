import json
import os
import unittest

from fake_adapters import PR_URL, install
from helpers import FactoryCase, code_job, git, read, write
from test_after import after_host

from factory import db

CHANGES = """VERDICT: CHANGES
Two things block.

## Tests
`make test`: 3 passed

## Must fix
- `api/lead.py:4`: the email is not checked, so bad leads are stored; check it
- `api/lead.py:9`: the error is swallowed; return 400
{extra}
## Notes
- naming could be shorter
"""


class PrWorkTest(FactoryCase):
    """A done run's PR as the agents see it: review, fix, merge and the steps after merge, each by its role."""

    def setUp(self):
        super().setUp()
        os.environ["LAND_LOG"] = os.path.join(self.tmp, "land.log")
        os.environ["AFTER_LOG"] = os.path.join(self.tmp, "after.log")
        self.run = self.started_run([code_job("lead")])
        self.run_id = self.run["id"]
        install(self.home, after_host())
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "acme-land"}')
        self.ref = f"{self.run_id}/lead"
        started = self.cli("job", "start", self.ref)
        self.job_worktree(started)
        self.commit(started["worktree"])
        self.output(started["group"])
        self.cli("step", "sync", self.ref)
        self.verdict(started["group"]["round_dir"], "review-code")
        self.cli("gate", "sync", self.ref)
        self.cli("job", "next", self.ref)
        self.worktree = started["feature_worktree"]
        git(self.worktree, "merge", "-q", "--squash", started["branch"])
        git(self.worktree, "commit", "-q", "-m", "feat: lead")
        self.cleanup(started)
        self.cli("job", "stage", self.ref, "done")
        self.cli("run", "pr", self.run_id, PR_URL, "--repo", "api")
        self.finish()

    def cleanup(self, started):
        git(started["repo_path"], "worktree", "remove", "--force", started["worktree"])
        git(started["repo_path"], "branch", "-D", started["branch"])

    def merged(self):
        path = self.run["repos"][0]["path"]
        git(path, "merge", "-q", "--squash", self.run["feature_branch"])
        git(path, "commit", "-q", "-m", "Add the lead form (#7)")
        git(path, "push", "-q", "origin", "main")
        os.environ["LAND_STATE"] = "MERGED"
        self.addCleanup(os.environ.pop, "LAND_STATE", None)
        self.checks(state="MERGED", merge_sha=git(path, "rev-parse", "HEAD"))
        self.cli("run", "prs", self.run_id, "--force")

    def finish(self):
        for status in ("finishing", "done"):
            self.cli("run", "status", self.run_id, status)
        self.head = git(self.worktree, "rev-parse", "HEAD")
        self.checks()

    def checks(self, **changes):
        found = {"state": "OPEN", "url": PR_URL, "draft": True, "head_sha": self.head, "mergeable": "MERGEABLE",
                 "review": "", "checks": "passing", "failing": [], "pending": [], "merge_sha": ""}
        found.update(changes)
        os.environ["LAND_CHECKS"] = json.dumps(found)

    def work(self, *flags):
        items = [item for item in self.cli("inbox", *flags)["items"] if item["type"] == "pr"]
        return items[0] if items else None

    def review(self, text, by="human"):
        started = self.cli("land", "start", self.run_id, "--repo", "api", "--by", by)
        write(started["review_file"], text)
        return started

    def test_roles_are_set_per_agent_and_listed(self):
        added = self.cli("agent", "add", "reviewer", "--roles", "review,merge")
        self.assertEqual(added["roles"], ["review", "merge"])
        self.assertEqual(self.cli("agent", "add", "reviewer", "--roles", "fix,review")["roles"], ["review", "fix"])
        self.assertEqual(self.cli("agent", "add", "reviewer")["roles"], ["review", "fix"])
        self.assertEqual(self.cli("agent", "add", "reviewer", "--roles", "none")["roles"], [])
        self.assertIn("--roles is a comma list", self.fails("agent", "add", "reviewer", "--roles", "push", code=2))
        self.assertIn("roles", self.cli("agent", "list", "--table").splitlines()[0])
        events = [event for event in self.cli("events", "--kind", "agent")["events"]]
        self.assertEqual(events[-1]["payload"], {"name": "reviewer", "roles": []})

    def test_each_move_needs_its_role(self):
        self.assertIn("agent helper has no role review; the user gives it with factory agent add helper --roles review",
                      self.fails("land", "start", self.run_id, "--repo", "api", "--by", "helper"))
        self.cli("agent", "add", "helper", "--roles", "review")
        started = self.review("VERDICT: PASS\nFine.\n", by="helper")
        self.assertEqual(started["round"], 1)
        self.assertIn("has no role merge", self.fails("land", "apply", self.run_id, "--repo", "api", "--by", "helper"))
        dry = self.cli("land", "apply", self.run_id, "--repo", "api", "--dry-run", "--by", "helper")
        self.assertEqual(dry["verdict"], "PASS")
        self.cli("agent", "add", "helper", "--roles", "review,merge")
        applied = self.cli("land", "apply", self.run_id, "--repo", "api", "--by", "helper")
        self.assertEqual(applied["reasons"], ["landing is off for project shop (defaults.land)"])
        shown = self.cli("agent", "show", "helper")
        self.assertEqual(shown["pr_work"], 3)
        self.assertEqual([(item["action"], item["repo"]) for item in shown["pr_work_recent"]],
                         [("apply", "api"), ("apply", "api"), ("start", "api")])
        self.assertEqual(shown["pr_work_recent"][0]["outcome"], "none")

    def test_the_inbox_walks_a_pr_from_review_to_merge(self):
        self.cli("project", "set", "shop", "defaults.land", '"merge"')
        first = self.work()
        self.assertEqual((first["next"], first["role"], first["id"]), ("review", "review", f"{self.run_id}/api"))
        self.assertEqual(first["session"], f"/factory land {self.run_id} --repo api --by <your agent name>")
        self.assertEqual(self.work("--as", "bot")["session"], f"/factory land {self.run_id} --repo api --by bot")
        self.assertIsNone(self.work("--role", "fix"))
        self.assertEqual(self.cli("inbox", "--role", "review")["count"], 1)
        self.assertIn("--role must be one of", self.fails("inbox", "--role", "push", code=2))

        started = self.cli("land", "start", self.run_id, "--repo", "api")
        busy = self.work()
        self.assertEqual((busy["next"], busy["busy"]["by"]), ("review", "human"))
        write(started["review_file"], CHANGES.format(extra=""))
        fix = self.work("--role", "fix")
        self.assertEqual((fix["next"], fix["reason"]), ("fix", "2 Must fix item(s); fix 1 of 2"))
        self.assertEqual(self.cli("land", "check", self.run_id)["repos"][0]["next"]["next"], "fix")

        self.assertIn("has no code job web", self.fails("land", "fix", self.run_id, "--repo", "api", "--job", "web"))
        fixed = self.cli("land", "fix", self.run_id, "--repo", "api")
        self.assertEqual((fixed["job"], fixed["fix"], fixed["fixes"], fixed["round"]), ("lead", 1, 2, 1))
        rework = read(os.path.join(fixed["rework"]["group"]["round_dir"], "rework.md"))
        self.assertIn("- `api/lead.py:9`: the error is swallowed; return 400", rework)
        self.assertIn(started["review_file"], rework)
        self.assertEqual(self.cli("run", "show", self.run_id)["status"], "running")
        self.assertIsNone(self.work())
        self.assertEqual(self.cli("land", "check", self.run_id)["repos"][0]["next"]["next"], "fixing")
        self.assertIn("is already being fixed by job lead", self.fails("land", "fix", self.run_id, "--repo", "api"))

        group = fixed["rework"]["group"]
        self.job_worktree(fixed["rework"])
        self.commit(fixed["rework"]["worktree"], "lead.py", "checked\n", "fix: check the email")
        self.output(group)
        self.cli("step", "sync", self.ref)
        self.verdict(group["round_dir"], "review-code")
        self.cli("gate", "sync", self.ref)
        self.cli("job", "next", self.ref)
        git(self.worktree, "merge", "-q", "--squash", fixed["rework"]["branch"])
        git(self.worktree, "commit", "-q", "-m", "fix: lead")
        self.cleanup(fixed["rework"])
        self.cli("job", "stage", self.ref, "done")
        self.finish()
        again = self.work()
        self.assertEqual((again["next"], again["reason"]), ("review", "new commits since land round 1"))

        self.review("VERDICT: PASS\nFine.\n")
        merge = self.work("--role", "merge")
        self.assertEqual((merge["next"], merge["role"], merge["reason"]), ("merge", "merge", "the review passed"))
        self.checks(checks="pending", pending=["e2e"])
        self.cli("land", "apply", self.run_id, "--repo", "api")
        self.assertEqual(self.work()["reason"], "checks still run: e2e")
        self.checks()
        os.environ["LAND_STATE"] = "MERGED"
        self.addCleanup(os.environ.pop, "LAND_STATE", None)
        self.assertEqual(self.cli("land", "apply", self.run_id, "--repo", "api")["action"], "merged")
        self.assertIsNone(self.work())

    def test_a_point_for_a_person_and_the_fix_cap_stop_the_factory(self):
        self.review(CHANGES.format(extra="- [human] should a lead without a phone count?\n"))
        decide = self.work()
        self.assertEqual((decide["next"], decide["role"]), ("decide", None))
        self.assertEqual(decide["reason"], "the review has 1 point(s) a person must decide first")
        self.assertIn("a person decides, then /factory rework", self.fails("land", "fix", self.run_id, "--repo", "api"))
        self.cli("project", "set", "shop", "defaults.land_fixes", "0")
        self.review(CHANGES.format(extra=""))
        capped = self.work()
        self.assertEqual(capped["next"], "decide")
        self.assertIn("the most defaults.land_fixes allows (0)", capped["reason"])
        self.assertIn("defaults.land_fixes must be an integer from 0 to 5",
                      self.fails("project", "set", "shop", "defaults.land_fixes", "9"))
        self.review("VERDICT: PASS\nFine.\n")
        self.assertIn("says PASS; land fix acts on CHANGES", self.fails("land", "fix", self.run_id, "--repo", "api"))

    def test_off_and_ready_leave_the_merge_to_a_person(self):
        self.review("VERDICT: PASS\nFine.\n")
        off = self.work()
        self.assertEqual((off["next"], off["role"]), ("merge", None))
        self.assertIn("landing is off for project shop", off["reason"])
        self.cli("project", "set", "shop", "defaults.land", '"ready"')
        self.assertEqual(self.work()["role"], "merge")
        self.assertEqual(self.cli("land", "apply", self.run_id, "--repo", "api")["action"], "ready")
        ready = self.work()
        self.assertEqual((ready["role"], ready["reason"]), (None, "marked ready for review; a person merges the PR"))

    def test_a_merged_pr_waits_for_its_deploy(self):
        self.cli("project", "set", "shop", "repos.api.after_merge", '{"deploy": "echo deployed > ../deployed.txt", "watch": true}')
        self.merged()
        deploy = self.work("--role", "deploy")
        self.assertEqual((deploy["next"], deploy["steps"]), ("deploy", ["deploy", "watch"]))
        self.cli("agent", "add", "ops", "--roles", "deploy")
        after = self.cli("land", "after", self.run_id, "--repo", "api", "--by", "ops")
        self.assertEqual((after["todo"], after["deploy"]), (["deploy", "watch"], "echo deployed > ../deployed.txt"))
        dry = self.cli("land", "deploy", self.run_id, "--repo", "api", "--dry-run", "--by", "ops")
        self.assertEqual(dry["status"], "would_deploy")
        done = self.cli("land", "deploy", self.run_id, "--repo", "api", "--by", "ops")
        self.assertEqual((done["status"], done["deploy_dir"]), ("done", "removed"))
        self.assertIn("$ echo deployed", read(done["deploy_log"]))
        self.assertEqual(self.work()["steps"], ["watch"])
        write(after["watch_file"], "DEPLOY: VERIFIED\nHealthy.\n")
        self.assertEqual(self.cli("land", "watched", self.run_id, "--repo", "api", "--by", "ops")["verdict"], "VERIFIED")
        self.assertIsNone(self.work())
        self.assertIn("has no role deploy", self.fails("land", "deploy", self.run_id, "--repo", "api", "--by", "helper"))
        self.assertEqual([item["action"] for item in self.cli("agent", "show", "ops")["pr_work_recent"]],
                         ["watch", "deploy", "after"])

    def test_a_failed_deploy_runs_again(self):
        self.cli("project", "set", "shop", "repos.api.after_merge", '{"deploy": "exit 4"}')
        self.merged()
        self.cli("land", "after", self.run_id, "--repo", "api")
        failed = self.cli("land", "deploy", self.run_id, "--repo", "api")
        self.assertEqual((failed["status"], failed["reason"]), ("failed", "exit 4 exited with 4"))
        self.assertTrue(os.path.isdir(failed["deploy_dir"]))
        self.assertEqual(self.cli("land", "after", self.run_id, "--repo", "api")["todo"], ["deploy"])
        self.assertIn("deploy must be a non-empty string",
                      self.fails("project", "set", "shop", "repos.api.after_merge", '{"deploy": " "}'))

    def test_an_old_merge_does_not_ask_for_a_deploy(self):
        self.cli("project", "set", "shop", "repos.api.after_merge", '{"watch": true}')
        self.merged()
        self.assertEqual(self.work()["next"], "deploy")
        conn = db.connect(os.path.join(self.home, "factory.db"))
        with db.tx(conn):
            conn.execute("UPDATE events SET at = '2020-01-01T00:00:00.000Z' WHERE kind = 'run.pr_state'")
        conn.close()
        self.assertIsNone(self.work())


if __name__ == "__main__":
    unittest.main()
