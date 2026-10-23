import json
import os
import unittest

from helpers import FactoryCase, write


def item(key, target="factory", **extra):
    found = {"key": key, "target": target, "where": "catalogue/steps/build.md, Rules", "title": f"Title {key}",
             "evidence": "jobs/x/groups/01-build/round-1/review-code.md: Must fix", "proposal": "Add a rule.",
             "impact": "high"}
    found.update(extra)
    return found


def retro_text(items):
    return ("# Retrospective: test\n\nIt went fine.\n\n## What went well\n\n## What cost time or tokens\n\n"
            "## Improvements\n\n```json\n" + json.dumps({"improvements": items}, indent=1) + "\n```\n")


class RetroLoadTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.api = self.repo("api")
        self.project(repos=[self.api])
        self.cli("project", "set", "shop", "improvement_targets",
                 '{"runbooks": {"repo": "api", "base": "main", "label": "runbooks"}, "notes": {"label": "notes"}}')
        self.run = self.cli("run", "new", "Change", "--project", "shop")

    def load(self, items, run=None):
        run = run or self.run
        write(os.path.join(run["run_dir"], "retro.md"), retro_text(items))
        return self.call("retro", "load", run["id"])

    def test_valid_load(self):
        code, out, err = self.load([
            item("spec-names-timeouts"),
            item("repo-test-script", "repo", repo="api", impact="medium"),
            item("issue-template", "process"),
            item("runbook-entry", "runbooks"),
            item("note-it", "notes", repo="api"),
        ])
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(len(result["new"]), 5)
        rows = {row["key"]: row for row in result["improvements"]}
        self.assertEqual(rows["repo-test-script"]["repo"], "api")
        self.assertEqual(rows["runbook-entry"]["repo"], "api")
        self.assertEqual(rows["spec-names-timeouts"]["location"], "catalogue/steps/build.md, Rules")
        self.assertEqual(rows["spec-names-timeouts"]["seen_in"], [self.run["id"]])
        self.assertEqual({row["status"] for row in result["improvements"]}, {"open"})
        self.assertEqual(len(self.cli("improvement", "list", "--run", self.run["id"])), 5)

    def test_problems(self):
        cases = [
            ([item(f"key-{number}") for number in range(9)], "9 improvements, at most 8"),
            ([item("x", "marketing")], "target 'marketing' is not one of"),
            ([item("Bad Key")], "key must match"),
            ([item("x"), item("x")], "duplicate key"),
            ([item("x", "repo")], "target repo needs repo"),
            ([item("x", "repo", repo="web")], "repo 'web' is not a repo of the project"),
            ([item("x", "factory", repo="api")], "target factory takes no repo"),
            ([item("x", "runbooks", repo="api")], "target runbooks takes no repo"),
            ([item("x", impact="huge")], "impact must be high, medium or low"),
            ([item("x", proposal="p" * 4001)], "proposal has 4001 characters, at most 4000"),
            ([item("x", colour="red")], "unknown key 'colour'"),
            ([dict(item("x"), title="")], "title is required"),
            ([{"key": "x", "same_as": 999}], "same_as 999 is not an improvement"),
        ]
        for items, message in cases:
            with self.subTest(message=message):
                code, _, err = self.load(items)
                self.assertEqual(code, 1)
                self.assertIn(message, err)
        write(os.path.join(self.run["run_dir"], "retro.md"), "# Retrospective\n\nNo json.\n")
        self.assertIn("no ```json block", self.fails("retro", "load", self.run["id"]))
        os.unlink(os.path.join(self.run["run_dir"], "retro.md"))
        self.assertIn("no retro.md", self.fails("retro", "load", self.run["id"]))

    def test_same_as_and_second_load_in_place(self):
        self.load([item("spec-names-timeouts"), item("drop-me", "process")])
        first = {row["key"]: row for row in self.cli("improvement", "list", "--run", self.run["id"])}
        self.cli("improvement", "decide", str(first["spec-names-timeouts"]["id"]), "accepted")
        later = self.cli("run", "new", "Another change", "--project", "shop")
        code, out, err = self.load([{"key": "timeouts-again", "same_as": first["spec-names-timeouts"]["id"]},
                                    item("new-one")], run=later)
        self.assertEqual(code, 0, err)
        result = json.loads(out)
        self.assertEqual(result["repeated"], [first["spec-names-timeouts"]["id"]])
        earlier = self.cli("improvement", "list", "--run", self.run["id"])[0]
        self.assertEqual(earlier["seen_in"], [self.run["id"], later["id"]])
        self.load([{"key": "timeouts-again", "same_as": first["spec-names-timeouts"]["id"]}, item("new-one")], run=later)
        self.assertEqual(self.cli("improvement", "list", "--run", self.run["id"])[0]["seen_in"], [self.run["id"], later["id"]])
        self.assertIn("is from this run", self.load([{"key": "self", "same_as": first["drop-me"]["id"]}])[2])
        code, out, _ = self.load([item("spec-names-timeouts", title="Renamed", impact="low")])
        self.assertEqual(code, 0)
        result = json.loads(out)
        self.assertEqual(result["updated"], [first["spec-names-timeouts"]["id"]])
        self.assertEqual(result["removed"], [first["drop-me"]["id"]])
        rows = self.cli("improvement", "list", "--run", self.run["id"])
        self.assertEqual([(row["id"], row["title"], row["impact"], row["status"]) for row in rows],
                         [(first["spec-names-timeouts"]["id"], "Renamed", "low", "accepted")])


if __name__ == "__main__":
    unittest.main()
