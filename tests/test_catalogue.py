import json
import os
import unittest

from helpers import ROOT, FactoryCase, read, write

from factory import FactoryError, catalogue

STEP = """---
name: {name}
role: {role}
kinds: [code]
summary: {summary}
when: Always.
---

## Focus
- Something.
"""


def step(folder, name, role="gate", summary="A step.", extra=""):
    text = STEP.format(name=name, role=role, summary=summary)
    if extra:
        text = text.replace("---\n\n", extra + "---\n\n", 1)
    return write(os.path.join(folder, "steps", f"{name}.md"), text)


class FrontMatterTest(unittest.TestCase):
    def test_values(self):
        meta, body = catalogue.front_matter(
            "---\nname: x\nkinds: [code, files]\nrequires: []\ngates_optional: true\nversion: 3\n"
            "when: a: b, c\n---\n\nBody\n"
        )
        self.assertEqual(meta, {"name": "x", "kinds": ["code", "files"], "requires": [], "gates_optional": True,
                                "version": 3, "when": "a: b, c"})
        self.assertEqual(body.strip(), "Body")

    def test_errors(self):
        for text, message in (
            ("name: x\n", "no front matter"),
            ("---\nname: x\n", "not closed"),
            ("---\nname x\n---\n", "expected 'key: value'"),
            ("---\nname: x\nname: y\n---\n", "duplicate key"),
            ("---\nName: x\n---\n", "expected 'key: value'"),
        ):
            with self.assertRaises(FactoryError) as caught:
                catalogue.front_matter(text)
            self.assertIn(message, caught.exception.message)

    def test_every_built_in_step_parses(self):
        folder = os.path.join(ROOT, "catalogue", "steps")
        for name in sorted(os.listdir(folder)):
            entry, problems = catalogue.load_step(os.path.join(folder, name), "built-in", folder)
            self.assertEqual(problems, [], name)
            self.assertEqual(entry["name"], name[:-3])


class CatalogueTest(FactoryCase):
    def test_show_fills_defaults(self):
        shown = self.cli("catalogue", "show")
        build = shown["steps"]["build"]
        self.assertEqual(build["agent"], "factory-worker")
        self.assertEqual(build["code_review"], "required")
        self.assertIsNone(build["model"])
        self.assertEqual(build["level"], "built-in")
        self.assertEqual(shown["steps"]["review-code"]["agent"], "factory-reviewer")
        self.assertNotIn("gates_optional", shown["steps"]["review-code"])
        self.assertEqual(shown["steps"]["write-doc"]["code_review"], "optional")
        self.assertEqual(shown["pipelines"]["bug-fix"]["pipeline"][1]["step"], "fix")
        self.assertEqual(shown["problems"], [])

    def test_override_order_of_five_levels(self):
        api = self.repo("api")
        self.project(repos=[api])
        config_dir = os.path.join(self.tmp, "team-catalogue")
        project_dir = os.path.join(self.tmp, "project-catalogue")
        self.cli("config", "set", "catalogue_dirs", json.dumps([config_dir]))
        self.cli("project", "set", "shop", "catalogue_dirs", json.dumps([project_dir]))
        levels = [
            (os.path.join(self.home, "catalogue"), "home"),
            (config_dir, "config"),
            (project_dir, "project"),
            (os.path.join(api, ".factory"), "repo:api"),
        ]
        for folder, label in levels:
            step(folder, "review-code", summary=f"review-code from {label}")
        shown = self.cli("catalogue", "show", "--project", "shop")
        self.assertEqual(shown["steps"]["review-code"]["summary"], "review-code from repo:api")
        self.assertEqual(shown["steps"]["review-code"]["level"], "repo:api")
        self.assertEqual(shown["steps"]["build"]["level"], "built-in")
        self.assertEqual([entry["level"] for entry in shown["dirs"]], ["built-in", "home", "config", "project", "repo:api"])
        os.unlink(os.path.join(api, ".factory", "steps", "review-code.md"))
        self.assertEqual(self.cli("catalogue", "show", "--project", "shop")["steps"]["review-code"]["level"], "project")
        os.unlink(os.path.join(project_dir, "steps", "review-code.md"))
        self.assertEqual(self.cli("catalogue", "show", "--project", "shop")["steps"]["review-code"]["level"], "config")
        os.unlink(os.path.join(config_dir, "steps", "review-code.md"))
        self.assertEqual(self.cli("catalogue", "show", "--project", "shop")["steps"]["review-code"]["level"], "home")

    def test_repo_level_holds_only_steps_and_pipelines(self):
        api = self.repo("api")
        self.project(repos=[api])
        write(os.path.join(api, ".factory", "adapters", "fake", "adapter.json"), "{}")
        step(os.path.join(api, ".factory"), "review-repo")
        built = catalogue.build({"catalogue_dirs": []}, {"repos": [{"name": "api", "path": api}], "catalogue_dirs": []})
        self.assertIn("review-repo", built["steps"])
        self.assertNotIn("fake", built["adapters"])

    def test_check_reports_errors(self):
        folder = os.path.join(self.home, "catalogue")
        step(folder, "bad-role", role="judge")
        write(os.path.join(folder, "steps", "misnamed.md"), STEP.format(name="other", role="gate", summary="x"))
        step(folder, "review-special", extra="agent: special-reviewer\n")
        write(os.path.join(folder, "pipelines", "broken.json"), json.dumps({
            "name": "broken", "description": "d", "kinds": ["code"], "when": "w",
            "pipeline": [{"step": "nothing", "gates": ["build"]}],
        }))
        write(os.path.join(folder, "pipelines", "extra.json"), json.dumps({
            "name": "extra", "description": "d", "kinds": ["code"], "when": "w", "colour": "red",
            "pipeline": [{"step": "build", "gates": ["review-code"]}],
        }))
        found = self.call("catalogue", "check")
        self.assertEqual(found[0], 1)
        result = json.loads(found[1])
        self.assertFalse(result["ok"])
        problems = [(os.path.basename(entry["path"]), entry["problem"]) for entry in result["problems"]]
        self.assertIn(("bad-role.md", "role must be produce or gate"), problems)
        self.assertIn(("misnamed.md", "name 'other' must equal the file name 'misnamed'"), problems)
        self.assertIn(("broken.json", "group 1: unknown step 'nothing'"), problems)
        self.assertIn(("broken.json", "group 1: build in gates is not a gate step"), problems)
        self.assertIn(("extra.json", "unknown key 'colour'"), problems)
        self.assertIn(("review-special.md", "agent special-reviewer is not installed (factory skill install)"), problems)
        write(os.path.join(self.user, ".claude", "agents", "special-reviewer.md"), "---\nname: special-reviewer\n---\n")
        result = json.loads(self.call("catalogue", "check")[1])
        self.assertNotIn("review-special.md", [os.path.basename(entry["path"]) for entry in result["problems"]])

    def test_step_rules(self):
        folder = os.path.join(self.home, "catalogue")
        step(folder, "rework", role="produce")
        step(folder, "review-flag", extra="gates_optional: true\n")
        step(folder, "build-human", role="produce", extra="agent: human\n")
        step(folder, "review-person", extra="agent: human\n")
        write(os.path.join(folder, "steps", "review-asks.md"),
              STEP.format(name="review-asks", role="gate", summary="x").replace("---\n\n", "agent: human\n---\n\n", 1)
              + "\n## Ask\n\nQUESTION: Approve?\n\n**Options**\n- **A (recommended):** Yes.\n- **B:** No.\n\n## Notes\n")
        result = json.loads(self.call("catalogue", "check")[1])
        problems = {(os.path.basename(entry["path"]), entry["problem"]) for entry in result["problems"]}
        self.assertIn(("rework.md", "name 'rework' is reserved"), problems)
        self.assertIn(("review-flag.md", "gates_optional is only for produce steps"), problems)
        self.assertIn(("build-human.md", "agent human is only for gates"), problems)
        self.assertIn(("review-person.md", "a human gate needs exactly one '## Ask' section"), problems)
        self.assertIn(("review-asks.md", "'## Ask' must be the last section"), problems)

    def test_human_gate_ask_must_be_a_valid_question(self):
        folder = os.path.join(self.home, "catalogue")
        write(os.path.join(folder, "steps", "review-sign.md"),
              STEP.format(name="review-sign", role="gate", summary="x").replace("---\n\n", "agent: human\n---\n\n", 1)
              + "\n## Ask\n\nQUESTION: Approve {job_title}\n\n**Options**\n- **A:** Yes.\n- **B:** No.\n")
        result = json.loads(self.call("catalogue", "check")[1])
        problems = [entry["problem"] for entry in result["problems"]]
        self.assertTrue(any("must end with '?'" in problem for problem in problems), problems)
        self.assertTrue(any("exactly one option must be marked (recommended)" in problem for problem in problems))

    def test_invalid_override_keeps_the_earlier_entry(self):
        folder = os.path.join(self.home, "catalogue")
        write(os.path.join(folder, "steps", "build.md"), "not front matter\n")
        shown = self.cli("catalogue", "show")
        self.assertEqual(shown["steps"]["build"]["level"], "built-in")
        self.assertEqual(os.path.basename(shown["problems"][0]["path"]), "build.md")

    def test_snapshot_at_run_new(self):
        api = self.repo("api")
        self.project(repos=[api])
        step(os.path.join(api, ".factory"), "review-local")
        run = self.cli("run", "new", "Change it", "--project", "shop")
        snapshot = json.loads(read(run["catalogue_file"]))
        self.assertIn("review-local", snapshot["steps"])
        self.assertIn("code-change", snapshot["pipelines"])
        os.unlink(os.path.join(api, ".factory", "steps", "review-local.md"))
        self.assertIn("review-local", json.loads(read(run["catalogue_file"]))["steps"])
        project = json.loads(read(run["project_file"]))
        self.assertEqual(project["name"], "shop")


if __name__ == "__main__":
    unittest.main()
