import json
import os
import unittest

from fake_adapters import LOCKED_SCRIPT, PR_URL, host, install
from helpers import FactoryCase, git, read, write
from test_retro import item, retro_text


class ImprovementTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.api = self.repo("api")
        self.docs = self.repo("docs")
        git(self.docs, "branch", "release")
        self.project(repos=[self.api, self.docs])
        self.cli("project", "set", "shop", "improvement_targets",
                 '{"knowledge": {"repo": "docs", "base": "release", "label": "docs"}}')
        self.run = self.cli("run", "new", "Change", "--project", "shop")
        write(os.path.join(self.run["run_dir"], "retro.md"), retro_text([
            item("factory-rule"),
            item("repo-a", "repo", repo="api"),
            item("repo-b", "repo", repo="api", impact="low"),
            item("docs-a", "knowledge"),
            item("process-a", "process"),
        ]))
        loaded = self.cli("retro", "load", self.run["id"])
        self.ids = {row["key"]: row["id"] for row in loaded["improvements"]}

    def decide(self, key, status, *extra):
        return self.cli("improvement", "decide", str(self.ids[key]), status, *extra)

    def test_accept_creates_a_linked_issue_and_moves_it(self):
        accepted = self.decide("repo-a", "accepted", "--note", "Yes, in the README")
        self.assertEqual((accepted["status"], accepted["note"], accepted["changed"]), ("accepted", "Yes, in the README", True))
        issue = self.cli("issue", "show", accepted["issue_key"])
        self.assertEqual((issue["type"], issue["title"], issue["project"]), ("improvement", "Title repo-a", "shop"))
        self.assertIn("Add a rule.", issue["body"])
        self.assertEqual(issue["links"][0]["kind"], "improvement")
        self.assertEqual(issue["links"][0]["ref"], str(self.ids["repo-a"]))
        self.decide("repo-a", "done")
        self.assertEqual(self.cli("issue", "show", accepted["issue_key"])["status"], "done")
        reopened = self.decide("repo-a", "open")
        self.assertIsNone(reopened["decided_at"])
        self.assertEqual(self.cli("issue", "show", accepted["issue_key"])["status"], "open")
        self.decide("repo-a", "rejected", "--note", "Not now")
        self.assertEqual(self.cli("issue", "show", accepted["issue_key"])["status"], "closed")
        self.assertIn("cannot move to done", self.fails("improvement", "decide", str(self.ids["repo-b"]), "done"))
        self.assertFalse(self.decide("repo-b", "open")["changed"])
        self.assertIn("no improvement 999", self.fails("improvement", "decide", "999", "accepted"))

    def test_the_decision_records_who_made_it(self):
        accepted = self.decide("repo-a", "accepted")
        self.assertEqual(accepted["decided_by"], "human")
        self.assertIn("give the reason with --note",
                      self.fails("improvement", "decide", str(self.ids["repo-b"]), "rejected", "--by", "bertil", code=2))
        rejected = self.decide("repo-b", "rejected", "--note", "Too small to matter", "--by", "bertil")
        self.assertEqual((rejected["status"], rejected["decided_by"]), ("rejected", "bertil"))
        self.assertIsNone(self.decide("repo-b", "open", "--by", "bertil")["decided_by"])
        self.assertIn("reserved", self.fails("improvement", "decide", str(self.ids["docs-a"]), "accepted", "--note", "x",
                                             "--by", "retro", code=2))
        self.assertIn("only marks improvements done", self.fails("improvement", "decide", str(self.ids["docs-a"]), "accepted",
                                                                 "--by", "orchestrator", code=2))
        self.assertEqual(self.decide("repo-a", "done", "--by", "orchestrator")["decided_by"], "orchestrator")
        shown = self.cli("agent", "show", "bertil")
        self.assertEqual(shown["decisions"], 2)
        self.assertEqual([(item["improvement_id"], item["from"], item["to"], item["note"]) for item in shown["decisions_recent"]],
                         [(self.ids["repo-b"], "rejected", "open", None),
                          (self.ids["repo-b"], "open", "rejected", "Too small to matter")])
        self.assertEqual(shown["decisions_recent"][0]["title"], "Title repo-b")
        found = self.cli("events", "--kind", "improvement.decide")["events"]
        self.assertEqual([event["payload"]["by"] for event in found], ["human", "bertil", "bertil", "orchestrator"])

    def test_factory_items_go_to_the_factory_project(self):
        self.cli("project", "add", "factory", "--key", "FAC")
        self.cli("config", "set", "factory_project", '"factory"')
        accepted = self.decide("factory-rule", "accepted")
        self.assertTrue(accepted["issue_key"].startswith("FAC-"))

    def test_start_groups_per_repo_and_base(self):
        for key in ("factory-rule", "repo-a", "repo-b", "docs-a", "process-a"):
            self.decide(key, "accepted")
        started = self.cli("improvement", "start")
        groups = {group["repo"]: group for group in started["groups"]}
        self.assertEqual(sorted(groups), ["api", "docs"])
        api = groups["api"]
        self.assertEqual((api["repo_path"], api["base_branch"], sorted(api["ids"])),
                         (self.api, "main", sorted([self.ids["repo-a"], self.ids["repo-b"]])))
        self.assertEqual(api["branch"], f"chore/improvements-{started['batch']}")
        self.assertEqual(api["remote_url"], os.path.join(self.tmp, "remotes", "api.git"))
        self.assertEqual(api["worktree"], os.path.join(self.home, "worktrees", "improve", started["batch"], "api"))
        self.assertEqual(groups["docs"]["base_branch"], "release")
        text = read(api["items_file"])
        self.assertIn(f"## {self.ids['repo-a']}: Title repo-a", text)
        self.assertIn("- Note from the human: none", text)
        self.assertEqual(json.loads(read(api["project_file"]))["name"], "shop")
        self.assertTrue(api["report_file"].endswith(f"improve/{started['batch']}/api/report.md"))
        self.assertEqual(started["process"][0]["id"], self.ids["process-a"])
        self.assertEqual(started["skipped"], [{"id": self.ids["factory-rule"], "reason": "no factory source (config.json factory_source)"}])
        only = self.cli("improvement", "start", "--id", str(self.ids["repo-b"]), "--id", str(self.ids["process-a"]))
        self.assertEqual([group["ids"] for group in only["groups"]], [[self.ids["repo-b"]]])
        open_one = self.cli("improvement", "start", "--id", str(self.ids["repo-a"]))
        self.assertTrue(open_one["groups"])

    def test_start_with_a_factory_source(self):
        source = self.repo("factory-core")
        git(source, "checkout", "-q", "-b", "work")
        self.cli("config", "set", "factory_source", json.dumps(source))
        self.decide("factory-rule", "accepted")
        started = self.cli("improvement", "start")
        group = started["groups"][0]
        self.assertEqual((group["repo"], group["repo_path"], group["base_branch"], group["setup"]), ("factory", source, "work", None))
        project = json.loads(read(group["project_file"]))
        self.assertEqual(project["integrations"]["code_host"]["adapter"], "none")
        based = self.cli("improvement", "start", "--factory-base", "main")
        self.assertEqual(based["groups"][0]["base_branch"], "main")
        self.cli("project", "add", "factory", "--key", "FAC")
        self.cli("project", "link-repo", "factory", source, "--as", "core")
        self.cli("project", "set", "factory", "repos", json.dumps([{"name": "core", "path": source, "setup": "make deps"}]))
        self.cli("config", "set", "factory_project", '"factory"')
        linked = self.cli("improvement", "start")["groups"][0]
        self.assertEqual((linked["repo"], linked["setup"]), ("factory", "make deps"))
        self.assertEqual(json.loads(read(linked["project_file"]))["name"], "factory")

    def test_pr_and_prs(self):
        install(self.home, host())
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "acme-host"}')
        self.decide("repo-a", "accepted")
        self.decide("repo-b", "accepted")
        self.assertIn("is open, not accepted", self.fails("improvement", "pr", str(self.ids["docs-a"]), "--url", PR_URL,
                                                          "--branch", "chore/improvements-x"))
        self.assertIn("does not match", self.fails("improvement", "pr", str(self.ids["repo-a"]), "--url",
                                                   "https://other.example.com/pull/1", "--branch", "chore/improvements-x"))
        recorded = self.cli("improvement", "pr", str(self.ids["repo-a"]), str(self.ids["repo-b"]), "--url", PR_URL,
                            "--branch", "chore/improvements-x")
        self.assertEqual({entry["pr_url"] for entry in recorded}, {PR_URL})
        issue = self.cli("issue", "show", recorded[0]["issue_key"])
        self.assertIn(("pr", PR_URL), [(link["kind"], link["ref"]) for link in issue["links"]])
        listed = self.cli("improvement", "prs")
        self.assertEqual(listed, [{"url": PR_URL, "branch": "chore/improvements-x",
                                   "ids": [self.ids["repo-a"], self.ids["repo-b"]], "projects": ["shop"]}])
        os.environ["ACME_PR_JSON"] = json.dumps({"state": "MERGED", "url": PR_URL})
        checked = self.cli("improvement", "prs", "--check")
        self.assertEqual(checked[0]["state"], "MERGED")
        self.assertEqual(self.cli("improvement", "list", "--status", "accepted")[0]["status"], "accepted")
        skipped = self.cli("improvement", "start")
        self.assertEqual({entry["reason"] for entry in skipped["skipped"]}, {f"it already has a PR: {PR_URL}"})
        install(self.home, host(name="acme-locked", script=LOCKED_SCRIPT))
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "acme-locked"}')
        locked = self.cli("improvement", "prs", "--check")
        self.assertEqual(locked[0]["state"], "unchecked")
        self.assertIn("credentials missing", locked[0]["reason"])
        self.decide("repo-a", "open")
        self.assertIsNone(self.cli("improvement", "list", "--status", "open")[0]["pr_url"])

    def test_list_filters(self):
        self.decide("repo-a", "accepted")
        self.assertEqual(len(self.cli("improvement", "list", "--status", "accepted")), 1)
        self.assertEqual(len(self.cli("improvement", "list", "--project", "shop")), 5)
        self.assertEqual(self.cli("improvement", "list", "--project", "blog"), [])
        self.assertIn("title", self.cli("improvement", "list", "--table").splitlines()[0])


if __name__ == "__main__":
    unittest.main()
