import json
import os
import unittest

from helpers import QUESTION, FactoryCase, code_job, files_job, write


class PlanLoadTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.api = self.repo("api")
        self.web = self.repo("web")
        self.project(repos=[self.api, self.web])
        self.run = self.cli("run", "new", "Add a lead form", "--project", "shop")

    def load_problems(self, jobs, facts=None):
        self.write_plan(self.run["id"], jobs, facts=facts)
        return self.fails("plan", "load", self.run["id"])

    def test_valid_plan(self):
        jobs = [
            code_job("lead-endpoint", "api", [{"step": "build", "gates": ["review-code", "review-security"]}]),
            code_job("lead-form", "web", depends=["lead-endpoint"]),
            files_job("timeouts", reads=["api"]),
        ]
        self.write_plan(self.run["id"], jobs)
        loaded = self.cli("plan", "load", self.run["id"])
        self.assertEqual([job["id"] for job in loaded["jobs"]], ["lead-endpoint", "lead-form", "timeouts"])
        self.assertEqual(loaded["jobs"][0]["pipeline"], [{"step": "build", "gates": ["review-code", "review-security"]}])
        self.assertEqual(loaded["jobs"][2]["repo"], None)
        self.assertEqual([job["missing"] for job in loaded["jobs"]], [[], [], []])
        self.assertEqual([repo["name"] for repo in loaded["repos"]], ["api", "web"])
        self.assertEqual(loaded["repos"][0]["base_branch"], "main")
        self.assertTrue(loaded["repos"][0]["feature_worktree"].endswith(f"{self.run['id']}/_feature/api"))
        shown = self.cli("run", "show", self.run["id"])
        self.assertEqual(shown["jobs"][1]["depends_on"], ["lead-endpoint"])
        self.assertEqual(shown["jobs"][0]["groups"][0]["max_round"], 4)

    def test_repo_order_follows_the_first_job(self):
        run = self.cli("run", "new", "Two repos", "--project", "shop", "--repo", "api")
        self.write_plan(run["id"], [code_job("form", "web"), code_job("endpoint", "api")])
        loaded = self.cli("plan", "load", run["id"])
        self.assertEqual([repo["name"] for repo in loaded["repos"]], ["web", "api"])

    def test_files_job_reads_add_repos(self):
        self.write_plan(self.run["id"], [files_job("measure", reads=["web"])])
        loaded = self.cli("plan", "load", self.run["id"])
        self.assertEqual([repo["name"] for repo in loaded["repos"]], ["web"])

    def test_missing_spec(self):
        self.write_plan(self.run["id"], [code_job("lead-endpoint", "api")], specs=False)
        loaded = self.cli("plan", "load", self.run["id"])
        self.assertEqual(loaded["jobs"][0]["missing"], ["spec.md"])

    def test_each_rule(self):
        cases = [
            ([code_job("Bad_Id")], "id must match"),
            ([code_job("same"), code_job("same")], "job same: duplicate id"),
            ([code_job("x", steps=[{"step": "nothing", "gates": ["review-code"]}])], "unknown step 'nothing'"),
            ([code_job("x", steps=[{"step": "review-code", "gates": []}])], "review-code is a gate"),
            ([code_job("x", steps=[{"step": "build", "gates": ["build"]}])], "build is a produce step"),
            ([code_job("x", steps=[{"step": "research", "gates": ["review-code"]}])], "research does not fit a code job"),
            ([files_job("x", steps=[{"step": "research", "gates": ["review-code"]}])], "gate review-code does not fit a files job"),
            ([code_job("x", steps=[{"step": "build", "gates": ["review-code", "review-code"]}])], "gate review-code appears twice"),
            ([code_job("x", steps=[{"step": "build", "gates": ["review-code", "review-ux", "review-ui", "review-arch",
                                                            "review-security", "verify", "review-docs"]}])],
             "7 gates, at most 6"),
            ([code_job("x", steps=[{"step": "build", "gates": ["review-code"]}] * 7)], "7 groups, at most 6"),
            ([code_job("x", steps=[{"step": "build", "gates": []}])], "needs at least one gate"),
            ([code_job("x", steps=[{"step": "build", "gates": ["review-security"]}])], "needs review-code"),
            ([code_job("x", steps=[{"step": "fix", "gates": ["review-code"]}])], "fix requires repo-tests"),
            ([code_job("x", steps=[{"step": "build", "gates": ["review-code"], "optional_gates": ["verify"]}])],
             "optional_gates belongs to templates only"),
            ([code_job("x", depends=["ghost"])], "depends_on names an unknown job 'ghost'"),
            ([code_job("a", depends=["b"]), code_job("b", depends=["a"])], "depends_on forms a cycle"),
            ([code_job("x", depends=["x"])], "depends on itself"),
            ([code_job("x", repo="mobile")], "repo 'mobile' is not a repo of the project"),
            ([dict(files_job("x"), repo="api")], "a files job has no repo"),
            ([dict(code_job("x"), reads=["web"])], "reads is only for files jobs"),
            ([files_job("x", reads=["mobile"])], "reads names 'mobile'"),
            ([dict(code_job("x"), template="nothing")], "unknown template 'nothing'"),
            ([dict(code_job("x"), colour="red")], "unknown key 'colour'"),
            ([{"id": "x", "kind": "code", "title": "t", "pipeline": [{"step": "build", "gates": ["review-code"]}]}],
             "a code job needs repo"),
            ([], "jobs must be a non-empty list"),
        ]
        for jobs, message in cases:
            with self.subTest(message=message):
                self.assertIn(message, self.load_problems(jobs, facts={"api": ("", ""), "web": ("", "")}))

    def test_requires(self):
        self.write_plan(self.run["id"], [code_job("x", steps=[
            {"step": "reproduce", "gates": ["review-code"]}, {"step": "fix", "gates": ["review-code", "verify"]},
        ])])
        self.cli("plan", "load", self.run["id"])
        folder = os.path.join(self.home, "catalogue", "steps")
        for name, need in (("review-design", "designs"), ("review-chain", "upstream"), ("review-later", "previous"),
                           ("review-issue", "issue")):
            write(os.path.join(folder, f"{name}.md"),
                  f"---\nname: {name}\nrole: gate\nkinds: [code]\nsummary: s\nwhen: w\nrequires: [{need}]\n---\n")
        run = self.cli("run", "new", "Requires", "--project", "shop")
        self.write_plan(run["id"], [code_job("x", steps=[
            {"step": "build", "gates": ["review-code", "review-design", "review-chain", "review-later", "review-issue"]},
        ])])
        message = self.fails("plan", "load", run["id"])
        self.assertIn("review-design requires designs", message)
        self.assertIn("review-chain requires upstream", message)
        self.assertIn("review-later requires previous", message)
        self.assertIn("review-issue requires issue", message)
        write(os.path.join(run["run_dir"], "design", "home.png"), "png")
        write(os.path.join(run["run_dir"], "issue.md"), "# issue\n")
        self.write_plan(run["id"], [code_job("first"), code_job("x", depends=["first"], steps=[
            {"step": "build", "gates": ["review-code"]},
            {"step": "build", "gates": ["review-code", "review-design", "review-chain", "review-later", "review-issue"]},
        ])])
        self.cli("plan", "load", run["id"])

    def test_all_problems_are_listed_at_once(self):
        message = self.load_problems([
            code_job("Bad_Id"),
            code_job("x", repo="mobile", steps=[{"step": "nothing", "gates": []}]),
        ])
        self.assertTrue(message.startswith("plan.md has 3 problems:"), message)
        self.assertEqual(len([line for line in message.splitlines() if line.startswith("- ")]), 3)

    def test_code_review_rule_follows_the_project(self):
        self.cli("project", "set", "shop", "defaults.require_code_review", "false")
        run = self.cli("run", "new", "No review needed", "--project", "shop")
        self.write_plan(run["id"], [code_job("x", steps=[{"step": "build", "gates": ["review-security"]}])])
        self.cli("plan", "load", run["id"])
        run = self.cli("run", "new", "Docs", "--project", "shop")
        self.cli("project", "set", "shop", "defaults.require_code_review", "true")
        self.write_plan(run["id"], [code_job("x", steps=[{"step": "write-doc", "gates": ["review-docs"]}])])
        self.cli("plan", "load", run["id"])

    def test_question_in_plan(self):
        write(os.path.join(self.run["run_dir"], "plan.md"), QUESTION)
        first = self.cli("plan", "load", self.run["id"])
        again = self.cli("plan", "load", self.run["id"])
        self.assertEqual(first, again)
        question = self.cli("question", "list", "--run", self.run["id"])[0]
        self.assertEqual((question["id"], question["asked_by"], question["job_id"]), (first["question"], "planner", None))
        self.assertTrue(question["path"].endswith("plan.md"))

    def test_question_format_error(self):
        write(os.path.join(self.run["run_dir"], "plan.md"), "QUESTION: What now?\n\nNo options here.\n")
        result = self.cli("plan", "load", self.run["id"])
        self.assertTrue(result["path"].endswith("plan.md"))
        self.assertTrue(any("options" in problem for problem in result["question_error"]))
        self.assertEqual(self.cli("question", "list", "--run", self.run["id"]), [])

    def test_bad_json_and_missing_plan(self):
        self.assertIn("no plan.md", self.fails("plan", "load", self.run["id"]))
        write(os.path.join(self.run["run_dir"], "plan.md"), "# Plan\n\n```json\n{\"jobs\": [}\n```\n")
        self.assertIn("does not parse", self.fails("plan", "load", self.run["id"]))
        write(os.path.join(self.run["run_dir"], "plan.md"), "# Plan\n\nNo json.\n")
        self.assertIn("no ```json block", self.fails("plan", "load", self.run["id"]))

    def test_reload_replaces_jobs_and_refuses_after_a_start(self):
        self.write_plan(self.run["id"], [code_job("one"), code_job("two")])
        self.cli("plan", "load", self.run["id"])
        self.write_plan(self.run["id"], [code_job("three")])
        self.assertEqual([job["id"] for job in self.cli("plan", "load", self.run["id"])["jobs"]], ["three"])
        run = dict(self.run, repos=self.cli("plan", "load", self.run["id"])["repos"])
        self.feature_worktree(run, "api")
        self.cli("run", "status", self.run["id"], "running")
        self.cli("job", "start", f"{self.run['id']}/three")
        self.assertIn("started jobs", self.fails("plan", "load", self.run["id"]))

    def test_repo_facts_are_read(self):
        from factory import plans

        text = self.write_plan(self.run["id"], [code_job("x")], facts={"api": ("pytest -q", "pip install -e .")})
        self.assertEqual(plans.repo_facts(text)["api"], {"test": "pytest -q", "setup": "pip install -e ."})
        text = "## Repo facts\n\n### web\n- Test command: npm test (one file: npm test -- a)\n- Worktree setup: none\n\n## Jobs\n"
        self.assertEqual(plans.repo_facts(text)["web"], {"test": "npm test (one file: npm test -- a)", "setup": None})

    def test_a_setup_line_that_says_none_is_no_command(self):
        from factory import plans

        for line, expected in (
            ("none needed; `python3` (3.8+) on PATH is enough.", None),
            ("`none`", None),
            ("Nothing to do; `python3` is enough", None),
            ("n/a", None),
            ("`npm ci` (from the lockfile)", "npm ci"),
            ("`noneify --all`", "noneify --all"),
        ):
            with self.subTest(line=line):
                text = f"## Repo facts\n\n### api\n- Test command: `make test`\n- Worktree setup: {line}\n"
                self.assertEqual(plans.repo_facts(text)["api"]["setup"], expected)


if __name__ == "__main__":
    unittest.main()
