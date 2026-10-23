import json
import os
import unittest
import zipfile
from unittest import mock

from fake_adapters import PR_URL, install
from helpers import FactoryCase, git, read, write
from test_land import land_host

RELEASE_SCRIPT = """import json, os, sys
args = sys.argv[1:]
with open(os.environ["AFTER_LOG"], "a") as handle:
    handle.write(json.dumps(["release"] + args) + "\\n")
if os.environ.get("AFTER_LOCKED"):
    sys.exit(3)
tag = args[args.index("--tag") + 1]
print(json.dumps({"action": "created", "url": "https://code.example.com/shop/api/releases/tag/" + tag}))
"""

PACKAGE_SCRIPT = """import json, os, sys
args = sys.argv[1:]
with open(os.environ["AFTER_LOG"], "a") as handle:
    handle.write(json.dumps(["package"] + args) + "\\n")
print(json.dumps({"action": "pushed", "ref": "registry.example.com/shop/api:" + args[args.index("--tag") + 1]}))
"""


def after_host():
    built = land_host()
    data, scripts = built["data"], built["scripts"]
    script = {"via": "script"}
    data["capabilities"]["publish_release"] = dict(script, command=[
        "python3", "scripts/publish_release.py", "--url", "{pr_url}", "--tag", "{tag}", "--target", "{merge_sha}",
        "--files", "{artifacts}", "--title", "{title}", "--notes-file", "{notes_file}", "--mode", "{release_mode}",
        "--prerelease", "{prerelease}"])
    data["capabilities"]["publish_package"] = dict(script, command=[
        "python3", "scripts/publish_package.py", "--url", "{pr_url}", "--tag", "{tag}", "--revision", "{merge_sha}",
        "--files", "{artifacts}"])
    data["settings"]["prerelease"] = {"type": "bool", "default": True}
    scripts["scripts/publish_release.py"] = RELEASE_SCRIPT
    scripts["scripts/publish_package.py"] = PACKAGE_SCRIPT
    return built


class AfterMergeTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.tmp, "after.log")
        os.environ["AFTER_LOG"] = self.log
        os.environ["LAND_LOG"] = os.path.join(self.tmp, "land.log")
        self.addCleanup(os.environ.pop, "AFTER_LOCKED", None)
        self.api = self.repo("api")
        self.project(repos=[self.api])
        install(self.home, after_host())
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "acme-land"}')
        self.cli("issue", "add", "shop", "Lead form")
        self.run = self.cli("run", "new", "Lead form", "--issue", "SHOP-1", "--repo", "api")
        self.worktree = self.feature_worktree(self.run, "api")
        self.head = self.commit(self.worktree)
        self.cli("run", "pr", self.run["id"], PR_URL, "--repo", "api")
        for status in ("running", "finishing", "done"):
            self.cli("run", "status", self.run["id"], status)
        write(os.path.join(self.run_dir(self.run["id"]), "pr-api.md"), "# Add the lead form\n\nWhat it does.\n")
        self.merge = self.merged()

    def merged(self):
        git(self.api, "merge", "-q", "--squash", self.run["feature_branch"])
        git(self.api, "commit", "-q", "-m", "Add the lead form (#7)")
        git(self.api, "push", "-q", "origin", "main")
        sha = git(self.api, "rev-parse", "HEAD")
        self.checks(state="MERGED", merge_sha=sha)
        return sha

    def checks(self, **changes):
        found = {"state": "OPEN", "url": PR_URL, "draft": False, "head_sha": self.head, "mergeable": "MERGEABLE",
                 "review": "", "checks": "passing", "failing": [], "pending": [], "merge_sha": ""}
        found.update(changes)
        os.environ["LAND_CHECKS"] = json.dumps(found)

    def after_merge(self, value):
        return self.cli("project", "set", "shop", "repos.api.after_merge", json.dumps(value))

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_build_release_and_package_the_merge_commit(self):
        self.after_merge({"build": "mkdir -p out/win && git rev-parse HEAD > out/win/game.txt && echo l > out/game-linux.zip",
                          "artifact": ["out/win", "out/game-linux.zip"],
                          "release": "draft", "package": "publish"})
        self.assertEqual(self.cli("land", "check", self.run["id"])["repos"][0]["after"], ["build", "release", "package"])
        started = self.cli("land", "after", self.run["id"], "--repo", "api")
        self.assertEqual(started["merge_sha"], self.merge)
        self.assertRegex(started["tag"], rf"^build-[0-9]{{8}}-{self.merge[:7]}$")
        self.assertEqual(started["todo"], ["build", "release", "package"])
        self.assertEqual(git(started["build_dir"], "rev-parse", "HEAD"), self.merge)
        self.assertEqual(self.cli("land", "after", self.run["id"], "--repo", "api"), started)
        self.assertIn("has no build to publish", self.fails("land", "publish", self.run["id"], "--repo", "api"))
        built = self.cli("land", "build", self.run["id"], "--repo", "api")
        self.assertEqual((built["status"], built["reason"], built["build_dir"]), ("done", None, "removed"))
        windows, linux = built["artifacts"]
        self.assertTrue(windows.endswith(os.path.join("after", "api-win.zip")))
        self.assertTrue(linux.endswith(os.path.join("after", "game-linux.zip")))
        with zipfile.ZipFile(windows) as archive:
            self.assertEqual(archive.read("game.txt").decode().strip(), self.merge)
        self.assertFalse(os.path.exists(started["build_dir"]))
        published = self.cli("land", "publish", self.run["id"], "--repo", "api")
        self.assertEqual(published["release"]["action"], "created")
        self.assertTrue(published["release"]["where"].endswith(started["tag"]))
        self.assertEqual(published["package"], {"action": "pushed", "where": f"registry.example.com/shop/api:{started['tag']}"})
        release, package = self.calls()
        for flag, value in (("--target", self.merge), ("--files", f"{windows},{linux}"), ("--mode", "draft"),
                            ("--prerelease", "true"), ("--title", f"api {self.merge[:7]}: Add the lead form")):
            self.assertEqual(release[release.index(flag) + 1], value, flag)
        notes = release[release.index("--notes-file") + 1]
        self.assertIn(f"Merged in {PR_URL} as {self.merge}.", read(notes))
        self.assertEqual(package[package.index("--revision") + 1], self.merge)
        again = self.cli("land", "publish", self.run["id"], "--repo", "api")
        self.assertEqual((again["release"]["action"], again["package"]["action"]), ("done", "done"))
        self.assertEqual(len(self.calls()), 2)
        self.assertEqual(self.cli("land", "check", self.run["id"])["repos"][0]["after"], [])
        kinds = [event["kind"] for event in self.cli("run", "show", self.run["id"])["events"]]
        for kind in ("land.after", "land.build", "land.publish"):
            self.assertIn(kind, kinds)

    def test_a_failed_build_publishes_nothing_and_runs_again(self):
        self.after_merge({"build": "echo compiling; exit 3", "artifact": "out.zip", "release": "publish"})
        started = self.cli("land", "after", self.run["id"], "--repo", "api")
        failed = self.cli("land", "build", self.run["id"], "--repo", "api")
        self.assertEqual((failed["status"], failed["reason"]), ("failed", "echo compiling; exit 3 exited with 3"))
        self.assertIn("compiling", read(failed["build_log"]))
        self.assertEqual(failed["build_dir"], started["build_dir"])
        self.assertIn("has no build to publish", self.fails("land", "publish", self.run["id"], "--repo", "api"))
        self.assertEqual(self.cli("land", "after", self.run["id"], "--repo", "api")["steps"]["build"], "failed")
        again = self.cli("land", "build", self.run["id"], "--repo", "api")
        self.assertEqual(again["status"], "failed")
        self.assertEqual(read(again["build_log"]).count("compiling"), 2)
        self.assertEqual(self.calls(), [])

    def test_a_build_without_its_artifact_fails(self):
        self.after_merge({"build": "true", "artifact": "dist/game.zip", "release": "publish"})
        self.cli("land", "after", self.run["id"], "--repo", "api")
        self.assertEqual(self.cli("land", "build", self.run["id"], "--repo", "api")["reason"], "the build left no dist/game.zip")

    def test_dry_run_and_locked_credentials(self):
        self.after_merge({"build": "echo zip > game.zip", "artifact": "game.zip", "release": "publish"})
        self.cli("land", "after", self.run["id"], "--repo", "api")
        built = self.cli("land", "build", self.run["id"], "--repo", "api")
        self.assertTrue(built["artifacts"][0].endswith(os.path.join("after", "game.zip")))
        dry = self.cli("land", "publish", self.run["id"], "--repo", "api", "--dry-run")
        self.assertEqual(dry["release"], {"action": "would_release", "mode": "publish"})
        self.assertEqual(self.calls(), [])
        os.environ["AFTER_LOCKED"] = "1"
        locked = self.cli("land", "publish", self.run["id"], "--repo", "api")
        self.assertEqual(locked["release"]["action"], "none")
        self.assertIn("credentials missing; see Setup of the acme-land adapter", locked["release"]["reason"])
        del os.environ["AFTER_LOCKED"]
        self.assertEqual(self.cli("land", "publish", self.run["id"], "--repo", "api")["release"]["action"], "created")

    def test_deploy_watch_verdict(self):
        self.after_merge({"watch": True})
        started = self.cli("land", "after", self.run["id"], "--repo", "api")
        self.assertEqual((started["todo"], started["build_dir"], started["checkout"]), (["watch"], "none", self.worktree))
        self.assertEqual(self.cli("land", "watched", self.run["id"], "--repo", "api")["verdict"], "MISSING")
        write(started["watch_file"], "Deploy looks fine\n")
        self.assertEqual(self.cli("land", "watched", self.run["id"], "--repo", "api")["verdict"], "INVALID")
        with mock.patch("sys.stdin") as stdin:
            stdin.read.return_value = "DEPLOY: RESTORED\napi rolled back to the previous deploy\n\n## Actions\n- rollback"
            saved = self.cli("land", "save", self.run["id"], "--repo", "api", "--watch")
        self.assertEqual(saved["saved"], started["watch_file"])
        watched = self.cli("land", "watched", self.run["id"], "--repo", "api")
        self.assertEqual((watched["verdict"], watched["conclusion"]), ("RESTORED", "api rolled back to the previous deploy"))
        self.assertEqual(self.cli("land", "after", self.run["id"], "--repo", "api")["todo"], [])
        self.assertIn("has no build after merge", self.fails("land", "build", self.run["id"], "--repo", "api"))

    def test_merge_by_land_apply_lists_the_steps_after(self):
        self.after_merge({"watch": True, "build": "true"})
        self.cli("project", "set", "shop", "defaults.land", '"merge"')
        self.checks()
        os.environ["LAND_STATE"] = "MERGED"
        self.addCleanup(os.environ.pop, "LAND_STATE", None)
        started = self.cli("land", "start", self.run["id"], "--repo", "api")
        write(started["review_file"], "VERDICT: PASS\nFine.\n")
        applied = self.cli("land", "apply", self.run["id"], "--repo", "api")
        self.assertEqual((applied["action"], applied["after"]), ("merged", ["watch", "build"]))

    def test_a_new_pr_starts_the_steps_again(self):
        self.after_merge({"watch": True})
        first = self.cli("land", "after", self.run["id"], "--repo", "api")
        write(first["watch_file"], "DEPLOY: VERIFIED\nAll good.\n")
        self.cli("land", "watched", self.run["id"], "--repo", "api")
        self.cli("run", "pr", self.run["id"], PR_URL.replace("/7", "/8"), "--repo", "api")
        self.commit(self.worktree, "more.txt")
        second_merge = self.merged()
        second = self.cli("land", "after", self.run["id"], "--repo", "api")
        self.assertEqual((second["merge_sha"], second["todo"]), (second_merge, ["watch"]))
        self.assertTrue(os.path.isfile(os.path.join(first["after_dir"] + "-" + self.merge[:7], "watch.md")))

    def test_refusals(self):
        self.assertIn("has no after_merge steps", self.fails("land", "after", self.run["id"], "--repo", "api"))
        self.assertIn("has no steps after merge yet", self.fails("land", "build", self.run["id"], "--repo", "api"))
        self.after_merge({"watch": True})
        self.checks()
        self.assertIn("the PR of api is OPEN; the steps after merge run once it is merged",
                      self.fails("land", "after", self.run["id"], "--repo", "api"))
        self.checks(state="MERGED")
        self.assertIn("did not say which commit", self.fails("land", "after", self.run["id"], "--repo", "api"))

    def test_project_validates_after_merge(self):
        cases = [
            ({"release": "yes", "build": "make", "artifact": "a.zip"}, "release must be one of off, draft, publish"),
            ({"package": "draft", "build": "make", "artifact": "a.zip"}, "package must be one of off, publish"),
            ({"artifact": "a.zip"}, "artifact needs build"),
            ({"build": "make", "release": "draft"}, "release needs build and artifact"),
            ({"build": "make", "artifact": "../a.zip"}, "artifact must be paths inside the repo, without commas"),
            ({"build": "make", "artifact": []}, "artifact must be a path or a non-empty list of paths"),
            ({"build": "make", "artifact": ["a/x.zip", "b/x.zip"]}, "every path needs its own file name"),
            ({"watch": "yes"}, "watch must be true or false"),
            ({"deploy": True}, "unknown key 'deploy'"),
        ]
        for value, problem in cases:
            self.assertIn(problem, self.fails("project", "set", "shop", "repos.api.after_merge", json.dumps(value)), value)
        self.assertIn("has no repo web", self.fails("project", "set", "shop", "repos.web.after_merge", "{}"))
        self.assertIn("use link-repo", self.fails("project", "set", "shop", "repos.api.path", '"/tmp"'))
        shown = self.after_merge({"watch": True})
        self.assertEqual(next(repo for repo in shown["repos"] if repo["name"] == "api")["after_merge"], {"watch": True})


if __name__ == "__main__":
    unittest.main()
