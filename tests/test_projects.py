import json
import os
import unittest

from helpers import FactoryCase, git, read, write


class ProjectCommandsTest(FactoryCase):
    def test_add_link_and_detection(self):
        api = self.repo("api")
        git(api, "remote", "set-head", "origin", "main")
        self.cli("project", "add", "shop", "--key", "SHOP", "--description", "The shop")
        linked = self.cli("project", "link-repo", "shop", api, "--role", "service")
        self.assertEqual(linked["name"], "api")
        self.assertEqual(linked["path"], api)
        self.assertEqual(linked["remote"], os.path.join(self.tmp, "remotes", "api.git"))
        self.assertEqual(linked["default_branch"], "main")
        self.assertEqual(linked["role"], "service")
        local = self.repo("tools", origin=False)
        git(local, "checkout", "-q", "-b", "trunk")
        git(local, "branch", "-q", "-D", "main")
        linked = self.cli("project", "link-repo", "shop", os.path.join(local, "."), "--as", "devtools")
        self.assertEqual(linked["name"], "devtools")
        self.assertNotIn("remote", linked)
        self.assertEqual(linked["default_branch"], "trunk")
        self.assertIn("already linked", self.fails("project", "link-repo", "shop", api, "--as", "other"))
        self.assertIn("not a git checkout", self.fails("project", "link-repo", "shop", self.user))
        listed = self.cli("project", "list")
        self.assertEqual(listed[0]["repos"], ["api", "devtools"])
        self.cli("project", "unlink-repo", "shop", "devtools")
        self.assertEqual(self.cli("project", "list")[0]["repos"], ["api"])

    def test_link_repo_relative_path(self):
        api = self.repo("api")
        self.cli("project", "add", "shop", "--key", "SHOP")
        linked = self.cli("project", "link-repo", "shop", "api", cwd=os.path.dirname(api))
        self.assertEqual(linked["path"], api)

    def test_set_validates_and_writes(self):
        self.project(repos=[self.repo("api")])
        shown = self.cli("project", "set", "shop", "defaults.max_rounds", "2")
        self.assertEqual(shown["defaults"]["max_rounds"], 2)
        self.assertEqual(shown["defaults"]["parallel"], 3)
        self.assertIn("defaults.max_rounds", self.fails("project", "set", "shop", "defaults.max_rounds", "0"))
        self.cli("project", "set", "shop", "key", '"STORE"')
        self.assertEqual(self.cli("project", "list")[0]["key"], "STORE")
        self.cli("project", "add", "blog", "--key", "BLOG")
        self.assertIn("used by project blog", self.fails("project", "set", "shop", "key", '"BLOG"'))

    def test_show_resolves_defaults_and_availability(self):
        api = self.repo("api")
        local = self.repo("tools", origin=False)
        self.project(repos=[api, local])
        write(os.path.join(self.user, "docs", "api.md"), "# api\n")
        self.cli("project", "set", "shop", "knowledge", json.dumps({
            "label": "shop docs", "read_first": ["~/docs/missing.md"], "per_repo": ["~/docs/{repo}.md"],
        }))
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "git"}')
        shown = self.cli("project", "show", "shop")
        self.assertEqual(shown["integrations"]["tracker"]["adapter"], "local")
        self.assertEqual(shown["integrations"]["tracker"]["capabilities"]["fetch_issue"]["status"], "available")
        push = shown["integrations"]["code_host"]["capabilities"]["push"]
        self.assertEqual(push["status"], "partial")
        self.assertEqual(push["repos"], {"api": "available", "tools": "missing"})
        self.assertTrue(shown["integrations"]["code_host"]["adapter_md"].endswith("adapters/git/adapter.md"))
        self.assertNotIn("design", shown["integrations"])
        self.assertEqual(shown["knowledge"]["read_first"], [])
        self.assertEqual(shown["knowledge"]["per_repo"], {"api": [os.path.join(self.user, "docs", "api.md")], "tools": []})
        whats = sorted(entry["what"] for entry in shown["skipped"])
        self.assertEqual(whats, ["knowledge.per_repo", "knowledge.read_first"])
        self.assertEqual(sorted(shown["improvement_targets"]), ["factory", "knowledge", "process", "repo"])
        self.assertEqual(shown["defaults"], {"parallel": 3, "max_rounds": 4, "confirm_plan": False,
                                             "branch_prefix": "feature/", "require_code_review": True, "land": "off",
                                             "land_fixes": 2, "agent_runs": None})

    def test_show_warns_for_a_path_that_is_not_a_checkout(self):
        api = self.repo("api")
        self.project(repos=[api])
        config = os.path.join(self.home, "projects", "shop", "project.json")
        data = json.loads(read(config))
        data["repos"].append({"name": "gone", "path": os.path.join(self.tmp, "gone")})
        write(config, json.dumps(data))
        shown = self.cli("project", "show", "shop")
        self.assertIn("not a git checkout", shown["warnings"][0])

    def test_pin_writes_the_pointer_file(self):
        api = self.repo("api")
        self.project(repos=[api])
        pinned = self.cli("project", "pin", "shop", "--repo", api)
        self.assertEqual(read(os.path.join(api, ".factory", "project")), "shop\n")
        self.assertEqual(pinned["warnings"], [])
        other = self.repo("other", origin=False)
        pinned = self.cli("project", "pin", "shop", cwd=other)
        self.assertIn("not a repo of project shop", pinned["warnings"][0])

    def test_archive(self):
        api = self.repo("api")
        self.project(repos=[api])
        self.cli("project", "archive", "shop")
        self.assertTrue(self.cli("project", "list")[0]["archived"])
        self.assertIsNone(self.cli("project", "here", cwd=api)["project"])


class ResolutionTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.api = self.repo("api")
        self.web = self.repo("web")
        self.project("shop", "SHOP", [self.api])
        self.project("blog", "BLOG", [self.web])
        self.project("docs", "DOCS")

    def here(self, cwd, *args):
        return self.cli("project", "here", *args, cwd=cwd)

    def test_each_step_in_order(self):
        self.cli("config", "set", "default_project", '"docs"')
        found = self.here(self.api, "--project", "blog")
        self.assertEqual((found["project"], found["step"]), ("blog", 1))
        os.environ["FACTORY_PROJECT"] = "docs"
        found = self.here(self.api)
        self.assertEqual((found["project"], found["step"]), ("docs", 2))
        del os.environ["FACTORY_PROJECT"]
        write(os.path.join(self.api, ".factory", "project"), "blog\n")
        found = self.here(self.api)
        self.assertEqual((found["project"], found["step"]), ("blog", 3))
        os.unlink(os.path.join(self.api, ".factory", "project"))
        found = self.here(os.path.join(self.api))
        self.assertEqual((found["project"], found["step"], found["how"]), ("shop", 4, "linked repo"))
        found = self.here(self.user)
        self.assertEqual((found["project"], found["step"]), ("docs", 5))
        self.cli("config", "set", "default_project", "null")
        found = self.here(self.user)
        self.assertEqual((found["project"], found["step"]), (None, 6))
        loose = self.repo("loose", origin=False)
        found = self.here(loose)
        self.assertEqual((found["project"], found["step"], found["toplevel"]), (None, 6, loose))

    def test_subfolder_and_remote_match(self):
        os.makedirs(os.path.join(self.api, "src"))
        self.assertEqual(self.here(os.path.join(self.api, "src"))["project"], "shop")
        clone = os.path.join(self.tmp, "second-clone")
        git(self.tmp, "clone", "-q", os.path.join(self.tmp, "remotes", "web.git"), clone)
        found = self.here(clone)
        self.assertEqual((found["project"], found["step"]), ("blog", 4))
        self.assertIn("by remote", found["why"])

    def test_remote_forms_are_equal(self):
        from factory.util import normalise_remote

        forms = [
            "git@example.com:team/api.git",
            "ssh://git@example.com/team/api",
            "https://user:secret@example.com/team/api.git",
            "https://example.com/team/api/",
        ]
        self.assertEqual({normalise_remote(form) for form in forms}, {"example.com/team/api"})

    def test_ambiguity_is_an_error(self):
        self.cli("project", "link-repo", "docs", self.api)
        message = self.fails("project", "here", cwd=self.api)
        self.assertIn("several projects (docs, shop)", message)
        self.assertEqual(self.here(self.api, "--project", "shop")["project"], "shop")

    def test_unknown_project_names_fail(self):
        self.assertIn("no project nope", self.fails("project", "here", "--project", "nope"))
        os.environ["FACTORY_PROJECT"] = "nope"
        self.assertIn("no project nope", self.fails("project", "here", cwd=self.api))


class AdhocProjectTest(FactoryCase):
    def test_run_new_registers_an_adhoc_project_with_a_skip(self):
        repo = self.repo("my-tool")
        shown = self.cli("project", "show", cwd=repo)
        self.assertTrue(shown["adhoc"])
        self.assertEqual(self.cli("project", "list"), [])
        run = self.cli("run", "new", "Add a flag", cwd=repo)
        self.assertEqual(run["project"], "my-tool")
        self.assertIn({"dependency": "project", "effect": "ad-hoc project from the current repo"},
                      [{key: skip[key] for key in ("dependency", "effect")} for skip in run["skipped"]])
        listed = self.cli("project", "list")
        self.assertEqual([(entry["name"], entry["key"]) for entry in listed], [("my-tool", "MYTOOL")])
        found = self.cli("project", "here", cwd=repo)
        self.assertEqual((found["project"], found["step"]), ("my-tool", 4))
        self.assertEqual(self.cli("run", "new", "Second change", cwd=repo)["project"], "my-tool")

    def test_adhoc_names_avoid_clashes(self):
        self.cli("project", "add", "app", "--key", "APP")
        repo = self.repo("app", origin=False)
        run = self.cli("run", "new", "A change", cwd=repo)
        self.assertEqual(run["project"], "app-2")
        listed = {entry["name"]: entry["key"] for entry in self.cli("project", "list")}
        self.assertEqual(listed["app-2"], "APP2")

    def test_no_checkout_and_no_project_fails(self):
        self.assertIn("not in a git checkout", self.fails("run", "new", "A change", cwd=self.user))


if __name__ == "__main__":
    unittest.main()
