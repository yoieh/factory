import json
import os
import sqlite3
import unittest

from helpers import FactoryCase, code_job, git, read, write

HOST = {
    "name": "acme-host",
    "slot": "code_host",
    "version": 1,
    "vendors": [],
    "capabilities": {
        "push": {"via": "git"},
        "open_pr": {"via": "cli", "requires": {"bin": ["acme-cli-that-is-not-installed"]}},
    },
    "settings": {"draft": {"type": "bool", "default": True}},
    "patterns": {"pr_url": "^https://code\\.example\\.com/[a-z]+/[a-z]+/pull/[0-9]+$"},
}


def add_host(home, host=HOST):
    folder = os.path.join(home, "catalogue", "adapters", host["name"])
    write(os.path.join(folder, "adapter.json"), json.dumps(host))
    write(os.path.join(folder, "adapter.md"), "## Setup\n")


def dependencies(run):
    return [(skip["dependency"], skip["effect"]) for skip in run["skipped"]]


class RunNewTest(FactoryCase):
    def test_feature_run_without_a_code_host(self):
        api = self.repo("api")
        self.project(repos=[api])
        run = self.cli("run", "new", "Add a lead endpoint", "--repo", "api", "--session", "session-0001", cwd=api)
        self.assertRegex(run["id"], r"^[0-9]{8}-[0-9]{6}-add-a-lead-endpoint$")
        self.assertEqual(run["feature_branch"], "feature/add-a-lead-endpoint")
        self.assertEqual((run["status"], run["no_pr"], run["confirm_plan"], run["issue_key"], run["issue_file"]),
                         ("planning", True, False, None, None))
        self.assertEqual(run["repos"][0]["name"], "api")
        self.assertFalse(run["repos"][0]["can_push"])
        self.assertTrue(os.path.isdir(os.path.join(run["run_dir"], "design")))
        self.assertIn(("code_host.push", "no code host that can push (none): nothing is pushed, no PR is opened"),
                      dependencies(run))
        notes = read(os.path.join(run["run_dir"], "retro-notes.md"))
        self.assertIn("- skipped code_host.push: no code host", notes)
        shown = self.cli("run", "show", run["id"])
        self.assertEqual(shown["sessions"], ["session-0001"])
        self.assertEqual(shown["history"], [])

    def test_code_host_capabilities_set_no_pr_and_skips(self):
        add_host(self.home)
        api = self.repo("api")
        local = self.repo("tools", origin=False)
        self.project(repos=[api, local])
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "acme-host"}')
        run = self.cli("run", "new", "Two repos", "--project", "shop", "--repo", "api", "--repo", "tools")
        self.assertFalse(run["no_pr"])
        self.assertEqual([(repo["name"], repo["can_push"]) for repo in run["repos"]], [("api", True), ("tools", False)])
        found = dependencies(run)
        self.assertIn(("code_host.push", "tools has no origin remote, nothing is pushed for it"), found)
        self.assertTrue(any(dependency == "code_host.open_pr" and "not on PATH" in effect for dependency, effect in found))
        run = self.cli("run", "new", "Only local", "--project", "shop", "--repo", "tools")
        self.assertTrue(run["no_pr"])
        run = self.cli("run", "new", "Choose later", "--project", "shop")
        self.assertFalse(run["no_pr"])
        self.write_plan(run["id"], [code_job("x", repo="tools")])
        loaded = self.cli("plan", "load", run["id"])
        self.assertTrue(loaded["no_pr"])
        self.assertIn("tools has no origin remote", json.dumps(self.cli("skip", "list", run["id"])))

    def test_flags(self):
        api = self.repo("api")
        git(api, "branch", "release")
        self.project(repos=[api])
        run = self.cli("run", "new", "Tune it", "--project", "shop", "--repo", "api", "--branch", "topic/tune",
                       "--base", "release", "--parallel", "5", "--max-rounds", "2", "--confirm-plan", "--no-pr")
        shown = self.cli("run", "show", run["id"])
        self.assertEqual((shown["feature_branch"], shown["parallel"], shown["max_rounds"], shown["confirm_plan"], shown["no_pr"]),
                         ("topic/tune", 5, 2, True, True))
        self.assertEqual(shown["repos"][0]["base_branch"], "release")
        self.assertIn("valid branch name", self.fails("run", "new", "x", "--project", "shop", "--branch", "bad..name"))
        self.assertIn("--max-rounds must be from 1 to 10", self.fails("run", "new", "x", "--project", "shop",
                                                                      "--max-rounds", "11", code=2))
        self.assertIn("not a repo of project shop", self.fails("run", "new", "x", "--project", "shop", "--repo", "web"))
        self.assertIn("bad session id", self.fails("run", "new", "x", "--project", "shop", "--session", "short", code=2))

    def test_repo_by_path_and_project_defaults(self):
        api = self.repo("api")
        self.project(repos=[api])
        self.cli("project", "set", "shop", "defaults", '{"branch_prefix": "work/", "confirm_plan": true, "parallel": 2}')
        run = self.cli("run", "new", "By path", "--repo", os.path.join(api, "."), cwd=self.tmp)
        self.assertEqual(run["project"], "shop")
        self.assertEqual(run["feature_branch"], "work/by-path")
        self.assertTrue(run["confirm_plan"])
        self.assertEqual(self.cli("run", "show", run["id"])["parallel"], 2)

    def test_session_fallback_and_run_session(self):
        api = self.repo("api")
        self.project(repos=[api])
        os.environ["CLAUDE_CODE_SESSION_ID"] = "fallback-session-1"
        run = self.cli("run", "new", "Sessions", "--project", "shop", "--session", "")
        self.assertEqual(self.cli("run", "show", run["id"])["sessions"], ["fallback-session-1"])
        result = self.cli("run", "session", run["id"], "resumed-session-2")
        self.assertEqual(result["sessions"], ["fallback-session-1", "resumed-session-2"])
        self.cli("run", "session", run["id"], "resumed-session-2")
        self.assertEqual(len(self.cli("run", "show", run["id"])["sessions"]), 2)

    def test_a_repo_that_is_not_a_checkout_is_refused(self):
        api = self.repo("api")
        self.project(repos=[api])
        config = os.path.join(self.home, "projects", "shop", "project.json")
        data = json.loads(read(config))
        data["repos"].append({"name": "gone", "path": os.path.join(self.tmp, "gone")})
        write(config, json.dumps(data))
        self.assertIn("is not a git checkout", self.fails("run", "new", "x", "--project", "shop", "--repo", "gone"))


class RunLifecycleTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.api = self.repo("api")
        self.project(repos=[self.api])
        self.run = self.cli("run", "new", "Lifecycle", "--project", "shop", "--repo", "api")
        self.run_id = self.run["id"]

    def test_status_machine(self):
        self.assertIn("cannot move from planning to done", self.fails("run", "status", self.run_id, "done"))
        for status in ("confirm", "planning", "running", "finishing", "running", "finishing", "done", "running"):
            self.assertTrue(self.cli("run", "status", self.run_id, status)["changed"])
        self.assertFalse(self.cli("run", "status", self.run_id, "running")["changed"])
        self.cli("run", "status", self.run_id, "failed")
        self.assertIn("cannot move from failed", self.fails("run", "status", self.run_id, "running"))
        self.assertIn("status must be one of", self.fails("run", "status", self.run_id, "paused", code=2))
        history = self.cli("run", "show", self.run_id)["history"]
        self.assertEqual([entry["to"] for entry in history][-2:], ["running", "failed"])

    def test_paths(self):
        found = self.cli("paths", self.run_id)
        folder = os.path.join(self.home, "runs", self.run_id)
        self.assertEqual(found["run_dir"], folder)
        for key, name in (("plan", "plan.md"), ("issue_file", "issue.md"), ("design_dir", "design"),
                          ("project_file", "project.json"), ("catalogue_file", "catalogue.json"),
                          ("retro_file", "retro.md"), ("retro_notes", "retro-notes.md")):
            self.assertEqual(found[key], os.path.join(folder, name))
        self.assertEqual(found["feature_worktrees"], {"api": os.path.join(self.home, "worktrees", self.run_id, "_feature", "api")})

    def test_rounds(self):
        self.write_plan(self.run_id, [code_job("a"), code_job("b")])
        self.cli("plan", "load", self.run_id)
        self.assertEqual(self.cli("run", "rounds", self.run_id, "6")["groups"], 2)
        self.assertEqual([job["groups"][0]["max_round"] for job in self.cli("run", "show", self.run_id)["jobs"]], [6, 6])
        self.assertIn("from 1 to 10", self.fails("run", "rounds", self.run_id, "0", code=2))

    def test_list(self):
        self.cli("run", "new", "Another", "--project", "shop")
        listed = self.cli("run", "list")
        self.assertEqual(len(listed), 2)
        self.assertEqual(listed[1]["id"], self.run_id)
        self.assertEqual(listed[1]["open_questions"], 0)
        self.assertEqual(self.cli("run", "list", "--project", "other"), [])
        table = self.cli("run", "list", "--table")
        self.assertIn(self.run_id, table)

    def test_skips_and_events(self):
        first = self.cli("skip", "add", self.run_id, "design.fetch_design", "UI built without the design", "--pr")
        self.assertTrue(first["added"])
        again = self.cli("skip", "add", self.run_id, "design.fetch_design", "UI built  without the design", "--pr")
        self.assertFalse(again["added"])
        self.assertEqual(len(self.cli("skip", "list", self.run_id, "--pr")), 1)
        self.assertEqual(len(self.cli("skip", "list", self.run_id)), 2)
        self.assertIn("bad dependency", self.fails("skip", "add", self.run_id, "Design!", "x", code=2))
        self.assertIn("has no job ghost", self.fails("skip", "add", self.run_id, "setup", "x", "--job", "ghost"))
        notes = read(os.path.join(self.run["run_dir"], "retro-notes.md")).splitlines()
        self.assertEqual(notes.count("- skipped design.fetch_design: UI built without the design"), 1)
        self.cli("event", self.run_id, "merge.conflict", "--payload", '{"files": ["a.py"]}')
        last = self.cli("run", "show", self.run_id)["events"][-1]
        self.assertEqual((last["kind"], last["payload"]), ("merge.conflict", {"files": ["a.py"]}))
        self.assertIn("JSON object", self.fails("event", self.run_id, "x.y", "--payload", "[1]", code=2))
        self.assertIn("bad event kind", self.fails("event", self.run_id, "Bad Kind", code=2))

    def worktrees(self):
        run = dict(self.run, repos=self.cli("plan", "load", self.run_id)["repos"])
        feature = self.feature_worktree(run, "api")
        self.cli("run", "status", self.run_id, "running")
        started = self.cli("job", "start", f"{self.run_id}/a")
        self.job_worktree(started)
        return feature, started

    def test_archive_and_unarchive(self):
        self.write_plan(self.run_id, [code_job("a")])
        feature, started = self.worktrees()
        self.assertIn("archive a done or failed run", self.fails("run", "archive", self.run_id))
        self.cli("run", "status", self.run_id, "failed")
        result = self.cli("run", "archive", self.run_id)
        self.assertEqual(result["warnings"], [])
        self.assertFalse(os.path.exists(feature))
        self.assertFalse(os.path.exists(started["worktree"]))
        self.assertEqual(git(self.api, "branch", "--list", started["branch"]), "")
        self.assertNotEqual(git(self.api, "branch", "--list", self.run["feature_branch"]), "")
        self.assertTrue(os.path.isdir(self.run["run_dir"]))
        self.assertEqual(self.cli("run", "list"), [])
        self.assertEqual(self.cli("run", "list", "--archived")[0]["id"], self.run_id)
        self.cli("run", "unarchive", self.run_id)
        self.assertEqual(self.cli("run", "list")[0]["id"], self.run_id)

    def test_delete(self):
        self.write_plan(self.run_id, [code_job("a")])
        feature, started = self.worktrees()
        self.assertIn("delete a done or failed run", self.fails("run", "delete", self.run_id))
        conn = sqlite3.connect(os.path.join(self.home, "factory.db"))
        conn.execute(
            "INSERT INTO improvements (run_id, project, key, target, title, evidence, proposal, status, created_at) "
            "VALUES (?, 'shop', 'k', 'repo', 't', 'e', 'p', 'accepted', 'now')", (self.run_id,))
        conn.commit()
        self.assertIn("accepted improvements not applied", self.fails("run", "delete", self.run_id, "--force"))
        conn.execute("UPDATE improvements SET status = 'rejected'")
        conn.commit()
        conn.close()
        result = self.cli("run", "delete", self.run_id, "--force")
        self.assertTrue(result["deleted"])
        self.assertFalse(os.path.exists(self.run["run_dir"]))
        self.assertFalse(os.path.exists(feature))
        self.assertEqual(git(self.api, "branch", "--list", self.run["feature_branch"]), "")
        self.assertIn("no run", self.fails("run", "show", self.run_id))


if __name__ == "__main__":
    unittest.main()
