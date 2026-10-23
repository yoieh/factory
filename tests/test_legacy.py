import json
import os
import unittest

from helpers import FactoryCase, read, write

RETRO = """# Retro 2026-10-17-002-second

Scope: 001-first 002-second

## Went well
- Fine.

## Proposals
1. Add a test for the empty list. It failed once.
   - Kind: feature
   - Evidence: factory/jobs/002-second/round-1/review-code.md
   - Benefit: fewer rounds.
2. Name the timeout in every spec.
   - Kind: process
   - Evidence: factory/jobs/001-first/spec.md
3. Let the dashboard show skipped reviewers.
   - Kind: factory
   - Evidence: factory/board.json
4. Keep the board history honest.
   - Kind: factory
   - Evidence: factory/board.json
"""

BACKLOG = """# Backlog

## Stage 1
- [x] First feature → 001-first
- [x] Second feature → 002-second

## Retro 2026-10-17-002-second
- [x] Add a test for the empty list. (retro 2026-10-17-002-second#1) → 003-empty-list
- [ ] Name the timeout in every spec. (retro 2026-10-17-002-second#2) (id: 004-name-the-timeout)
- [?] Let the dashboard show skipped reviewers. (retro 2026-10-17-002-second#3)
"""

ARCHIVE = """## Retro 2026-10-17-002-second
- [-] Keep the board history honest. (retro 2026-10-17-002-second#4) (rejected: the new factory replaces the board)
"""


class LegacyImportTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.app = self.repo("app")
        self.project("app", "APP", repos=[self.app])
        self.old = os.path.join(self.tmp, "old", "factory")
        board = {"updated": "2026-10-17T10:00:00Z", "jobs": [
            {"id": "001-first", "title": "First feature", "branch": "factory/001-first", "stage": "merged", "round": 2,
             "note": "approved by human", "updated": "2026-10-17T09:00:00Z", "thread": "mcp:abc"},
            {"id": "002-second", "title": "Second feature", "branch": "factory/002-second", "stage": "merged", "round": 1,
             "note": "merged after 1 round", "updated": "2026-10-17T10:00:00Z"},
            {"id": "003-open", "title": "Still open", "branch": "factory/003-open", "stage": "building", "round": 1,
             "updated": "2026-10-17T10:00:00Z"},
        ]}
        write(os.path.join(self.old, "board.json"), json.dumps(board))
        first = os.path.join(self.old, "jobs", "001-first")
        write(os.path.join(first, "feature.md"), "# First feature\n\nDo the first thing.\n")
        write(os.path.join(first, "spec.md"), "# First feature\n\n## Goal\nThe first thing.\n")
        write(os.path.join(first, "build.md"), "# Build: 001-first (round 2)\n")
        write(os.path.join(first, "round-1", "review-code.md"), "VERDICT: CHANGES\nA bug.\n")
        write(os.path.join(first, "round-1", "review-security.md"), "VERDICT: PASS\nFine.\n")
        write(os.path.join(first, "round-1", "decision.md"), "REWORK\n\n## Why\n- the bug\n")
        write(os.path.join(first, "round-2", "review-code.md"), "VERDICT: PASS\nFixed.\n")
        write(os.path.join(first, "round-2", "review-human.md"), "VERDICT: CHANGES\nHuman rework.\n")
        write(os.path.join(first, "decision.md"), "ESCALATE\n\n## Why\n- a new dependency\n")
        write(os.path.join(first, "answers.md"),
              "- Should the list be sorted? — Yes, by name\n- Not a question line\n- Keep the old field? — B: drop it\n")
        second = os.path.join(self.old, "jobs", "002-second")
        write(os.path.join(second, "feature.md"), "# Second feature\n")
        write(os.path.join(second, "spec.md"), "# Second feature\n")
        write(os.path.join(second, "build.md"), "# Build: 002-second\n")
        write(os.path.join(second, "round-1", "review-code.md"), "VERDICT: PASS\nFine.\n")
        write(os.path.join(second, "decision.md"), "APPROVE\n")
        write(os.path.join(self.old, "jobs", "003-open", "feature.md"), "# Still open\n")
        write(os.path.join(self.old, "retros", "2026-10-17-002-second", "retro.md"), RETRO)
        write(os.path.join(self.old, "retros", "2026-10-17-002-second", "gate.md"), "1. AUTO: a test\n")
        write(os.path.join(self.old, "retros", "2026-10-18-nobody", "retro.md"), "# Not a retro\n\n## Proposals\n")
        write(os.path.join(self.old, "backlog.md"), BACKLOG)
        write(os.path.join(self.old, "backlog-archive.md"), ARCHIVE)

    def imported(self, *extra):
        return self.cli("legacy", "import", self.old, "--project", "app", *extra)

    def test_dry_run_counts_and_changes_nothing(self):
        result = self.imported("--dry-run")
        self.assertEqual(result["runs"], 2)
        self.assertEqual(result["improvements"], {"open": 1, "accepted": 1, "rejected": 1, "done": 1})
        self.assertIn({"job": "003-open", "reason": "stage building on the board"}, result["skipped"])
        self.assertIn({"retro": "2026-10-18-nobody", "reason": "its scope names no imported job"}, result["skipped"])
        self.assertEqual(self.cli("run", "list", "--project", "app"), [])
        self.assertFalse(os.path.isdir(os.path.join(self.home, "runs")) and os.listdir(os.path.join(self.home, "runs")))

    def test_jobs_become_finished_runs(self):
        result = self.imported()
        self.assertEqual((result["runs"], result["questions"], result["improvements_added"]), (2, 2, 4))
        listed = {run["feature_branch"]: run for run in self.cli("run", "list", "--project", "app")}
        self.assertEqual(set(listed), {"factory/001-first", "factory/002-second"})
        first = listed["factory/001-first"]
        self.assertEqual((first["status"], first["jobs"], first["jobs_done"]), ("done", 1, 1))
        self.assertRegex(first["id"], r"^[0-9]{8}-[0-9]{6}-001-first$")
        shown = self.cli("run", "show", first["id"])
        job = shown["jobs"][0]
        self.assertEqual((job["id"], job["stage"], job["title"]), ("001-first", "done", "First feature"))
        group = job["groups"][0]
        self.assertEqual((group["step"], group["phase"], group["round"]), ("build", "passed", 2))
        self.assertEqual(group["gates"], ["review-code", "review-security", "approve", "human"])
        verdicts = {(entry["round"], entry["gate"]): entry["verdict"] for entry in group["verdicts"]}
        self.assertEqual(verdicts, {
            (1, "review-code"): "CHANGES", (1, "review-security"): "PASS", (1, "approve"): "CHANGES",
            (2, "review-code"): "PASS", (2, "human"): "PASS", (2, "approve"): "QUESTION",
        })
        folder = self.run_dir(first["id"])
        for relative in ("plan.md", "report.md", "project.json", "catalogue.json", "jobs/001-first/spec.md",
                         "jobs/001-first/feature.md", "jobs/001-first/groups/01-build/output.md",
                         "jobs/001-first/groups/01-build/round-1/review-code.md"):
            self.assertTrue(os.path.isfile(os.path.join(folder, relative)), relative)
        for entry in group["verdicts"]:
            self.assertTrue(os.path.isfile(entry["path"]), entry["path"])
        self.assertIn("approved by human", read(os.path.join(folder, "report.md")))
        answered = [(question["text"], question["answer"], question["status"]) for question in shown["questions"]]
        self.assertEqual(answered, [("QUESTION: Should the list be sorted?", "Yes, by name", "resolved"),
                                    ("QUESTION: Keep the old field?", "B: drop it", "resolved")])
        kinds = [event["kind"] for event in shown["events"]]
        self.assertEqual(kinds[:2], ["run.new", "run.status"])
        self.assertIn("run.import", kinds)
        times = [event["at"] for event in shown["events"] if event["kind"] != "run.import"]
        self.assertEqual(times, sorted(times))
        self.assertEqual(self.cli("question", "list", "--status", "open", "--status", "clarify"), [])

    def test_proposals_become_improvements_on_the_last_job(self):
        self.imported()
        second = next(run for run in self.cli("run", "list", "--project", "app") if run["feature_branch"] == "factory/002-second")
        found = {item["title"]: item for item in self.cli("improvement", "list", "--project", "app")}
        self.assertEqual({item["run_id"] for item in found.values()}, {second["id"]})
        test = found["Add a test for the empty list."]
        self.assertEqual((test["status"], test["target"], test["repo"]), ("done", "repo", "app"))
        self.assertIn("built in job 003-empty-list", test["note"])
        self.assertIn("Benefit: fewer rounds.", test["proposal"])
        self.assertEqual(test["evidence"], "factory/jobs/002-second/round-1/review-code.md")
        self.assertEqual(found["Name the timeout in every spec."]["status"], "accepted")
        self.assertEqual(found["Name the timeout in every spec."]["target"], "process")
        self.assertEqual(found["Let the dashboard show skipped reviewers."]["status"], "open")
        honest = found["Keep the board history honest."]
        self.assertEqual((honest["status"], honest["target"]), ("rejected", "factory"))
        self.assertIn("the new factory replaces the board", honest["note"])
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir(second["id"]), "retros", "2026-10-17-002-second", "gate.md")))

    def test_accepted_proposal_links_its_issue(self):
        issue = self.cli("issue", "add", "app", "Name the timeout", "--type", "task")
        write(os.path.join(self.tmp, "issues.json"), json.dumps({issue["key"]: {"old": "004-name-the-timeout"}}))
        self.imported("--issues", os.path.join(self.tmp, "issues.json"))
        accepted = self.cli("improvement", "list", "--project", "app", "--status", "accepted")
        self.assertEqual([item["issue_key"] for item in accepted], [issue["key"]])

    def test_import_twice_adds_nothing(self):
        self.imported()
        again = self.imported()
        self.assertEqual((again["runs"], again["runs_already_imported"], again["improvements_added"]), (0, 2, 0))
        self.assertEqual(len(self.cli("run", "list", "--project", "app")), 2)
        self.assertEqual(len(self.cli("improvement", "list", "--project", "app")), 4)

    def test_refusals(self):
        self.assertIn("no board.json", self.fails("legacy", "import", self.tmp, "--project", "app"))
        self.assertIn("no project", self.fails("legacy", "import", self.old, "--project", "nope"))
        self.assertIn("not a repo of app", self.fails("legacy", "import", self.old, "--project", "app", "--repo", "web"))


if __name__ == "__main__":
    unittest.main()
