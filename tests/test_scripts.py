import json
import os
import unittest
from unittest import mock

from fake_adapters import LOCKED_SCRIPT, PR_URL, host, install, tracker
from helpers import FactoryCase, read, write

from factory import adapters


class ScriptProtocolTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.tmp, "acme.log")
        os.environ["ACME_LOG"] = self.log
        self.api = self.repo("api")
        self.project(repos=[self.api])

    def use(self, spec, settings=""):
        install(self.home, spec)
        self.cli("project", "set", "shop", "integrations.code_host",
                 '{"adapter": "%s"%s}' % (spec["data"]["name"], settings))

    def calls(self):
        with open(self.log, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def test_placeholders_settings_and_repo(self):
        self.use(host(), ', "draft": false')
        result = self.cli("adapter", "run", "acme-host", "pr_state", "--project", "shop", "--repo", "api", "--pr_url", PR_URL)
        self.assertEqual(result, {"state": "OPEN", "url": PR_URL})
        args = self.calls()[-1]["args"]
        self.assertEqual(args, ["--draft", "false", "--reviewers", "team-a,team-b", "--project", "shop", "--repo", "api",
                                "--url", PR_URL])
        self.cli("adapter", "run", "acme-host", "pr_state", "--project", "shop", "--pr-url", PR_URL, "--repo_path", "/x",
                 "--unused", "ignored")
        self.assertEqual(self.calls()[-1]["args"][7], "")

    def test_a_placeholder_without_a_value_stops_before_the_script(self):
        self.use(host(extra_settings={"site": {"type": "string", "default": None}},
                      command=["python3", "scripts/pr_state.py", "--site", "{site}", "--url", "{pr_url}"]))
        message = self.fails("adapter", "run", "acme-host", "pr_state", "--project", "shop", "--pr_url", PR_URL)
        self.assertIn("{site} has no value", message)
        self.assertFalse(os.path.exists(self.log))

    def test_exit_codes_and_bad_output(self):
        self.use(host())
        os.environ["ACME_MODE"] = "fail"
        self.assertIn("failed, nothing changed", self.fails("adapter", "run", "acme-host", "pr_state", "--pr_url", PR_URL,
                                                            "--project", "shop"))
        os.environ["ACME_MODE"] = "garbage"
        self.assertIn("did not print one JSON object",
                      self.fails("adapter", "run", "acme-host", "pr_state", "--pr_url", PR_URL, "--project", "shop"))
        os.environ["ACME_MODE"] = "ok"
        os.environ["ACME_PR_JSON"] = json.dumps({"state": "MAYBE", "url": PR_URL})
        self.assertIn("not a valid pr_state result",
                      self.fails("adapter", "run", "acme-host", "pr_state", "--pr_url", PR_URL, "--project", "shop"))
        install(self.home, host(name="acme-locked", script=LOCKED_SCRIPT))
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "acme-locked"}')
        self.assertIn("credentials missing",
                      self.fails("adapter", "run", "acme-locked", "pr_state", "--pr_url", PR_URL, "--project", "shop", code=3))

    def test_timeout(self):
        self.use(host())
        os.environ["ACME_MODE"] = "sleep"
        with mock.patch.object(adapters, "TIMEOUT", 0.5):
            message = self.fails("adapter", "run", "acme-host", "pr_state", "--pr_url", PR_URL, "--project", "shop")
        self.assertIn("timed out", message)

    def test_env_from_listed_names_and_no_secret_in_output(self):
        secret = "s3cret-value-from-file"
        write(os.path.join(self.user, ".acme.env"), f"export ACME_TOKEN='{secret}'\nACME_OTHER=not-listed\n")
        self.use(host(env=["ACME_TOKEN"]))
        self.cli("adapter", "run", "acme-host", "pr_state", "--project", "shop", "--pr_url", PR_URL)
        call = self.calls()[-1]
        self.assertEqual((call["token"], call["other"]), (secret, None))
        os.environ["ACME_MODE"] = "fail"
        code, out, err = self.call("adapter", "run", "acme-host", "pr_state", "--project", "shop", "--pr_url", PR_URL)
        self.assertEqual(code, 1)
        self.assertIn("request failed for ***", err)
        self.assertNotIn(secret, out + err)
        self.assertNotIn(secret, self.database_text())

    def database_text(self):
        with open(os.path.join(self.home, "factory.db"), "rb") as handle:
            data = handle.read()
        wal = os.path.join(self.home, "factory.db-wal")
        if os.path.exists(wal):
            with open(wal, "rb") as handle:
                data += handle.read()
        return data.decode("utf-8", errors="replace")

    def test_unknown_adapter_and_capability(self):
        self.use(host())
        self.assertIn("does not use adapter nothing", self.fails("adapter", "run", "nothing", "pr_state", "--project", "shop"))
        self.assertIn("has no open_pr", self.fails("adapter", "run", "acme-host", "open_pr", "--project", "shop"))
        self.assertIn("not a repo of project shop",
                      self.fails("adapter", "run", "acme-host", "pr_state", "--project", "shop", "--repo", "web"))
        os.unlink(os.path.join(self.home, "catalogue", "adapters", "acme-host", "scripts", "pr_state.py"))
        self.assertIn("is not built", self.fails("adapter", "run", "acme-host", "pr_state", "--project", "shop",
                                                 "--pr_url", PR_URL))

    def test_run_prs_with_a_fake_host(self):
        self.use(host())
        run = self.cli("run", "new", "Change", "--project", "shop", "--repo", "api")
        self.assertIn("does not match the acme-host pr_url pattern",
                      self.fails("run", "pr", run["id"], "https://other.example.com/x/pull/1", "--repo", "api"))
        self.assertIn("has no repo web", self.fails("run", "pr", run["id"], PR_URL, "--repo", "web"))
        self.cli("run", "pr", run["id"], PR_URL, "--repo", "api")
        os.environ["ACME_PR_JSON"] = json.dumps({"state": "DECLINED", "url": PR_URL})
        checked = self.cli("run", "prs", run["id"])
        self.assertEqual(checked["prs"][0]["state"], "DECLINED")
        self.assertEqual(self.cli("run", "show", run["id"])["repos"][0]["pr_state"], "DECLINED")
        self.assertEqual(self.cli("run", "prs", run["id"])["prs"][0]["reason"], "final state")
        os.environ["ACME_MODE"] = "fail"
        unknown = self.cli("run", "prs", run["id"], "--force")
        self.assertEqual(unknown["prs"][0]["state"], "unknown")
        self.assertEqual(self.cli("run", "show", run["id"])["repos"][0]["pr_state"], "DECLINED")

    def test_issue_moves_on_first_pr_and_when_all_are_merged(self):
        self.use(host())
        web = self.repo("web")
        self.cli("project", "link-repo", "shop", web)
        self.cli("issue", "add", "shop", "Lead form")
        run = self.cli("run", "new", "Lead form", "--issue", "SHOP-1", "--repo", "api", "--repo", "web")
        self.cli("run", "pr", run["id"], PR_URL, "--repo", "api")
        self.assertEqual(self.cli("issue", "show", "SHOP-1")["status"], "in_review")
        other = "https://code.example.com/shop/web/pull/8"
        self.cli("run", "pr", run["id"], other, "--repo", "web")
        state = {PR_URL: "MERGED", other: "OPEN"}
        script = os.path.join(self.home, "catalogue", "adapters", "acme-host", "scripts", "pr_state.py")
        write(script, "import json, sys\nstates = " + json.dumps(state) + "\nprint(json.dumps({'state': states[sys.argv[-1]], 'url': sys.argv[-1]}))\n")
        self.assertEqual(self.cli("run", "prs", run["id"])["issues_done"], [])
        self.assertEqual(self.cli("issue", "show", "SHOP-1")["status"], "in_review")
        state[other] = "MERGED"
        write(script, "import json, sys\nstates = " + json.dumps(state) + "\nprint(json.dumps({'state': states[sys.argv[-1]], 'url': sys.argv[-1]}))\n")
        later = self.cli("run", "new", "Lead form again", "--issue", "SHOP-1", "--repo", "api", "--again")
        self.assertEqual(self.cli("run", "prs", run["id"])["issues_done"], [])
        self.cli("run", "delete", later["id"], "--force")
        self.assertEqual(self.cli("run", "prs", run["id"], "--force")["issues_done"], ["SHOP-1"])
        issue = self.cli("issue", "show", "SHOP-1")
        self.assertEqual(issue["status"], "done")
        self.assertEqual(sorted(link["ref"] for link in issue["links"] if link["kind"] == "pr"), sorted([PR_URL, other]))

    def test_run_pr_clears_no_pr(self):
        run = self.cli("run", "new", "Change", "--project", "shop", "--repo", "api")
        self.assertTrue(run["no_pr"])
        self.use(host())
        stored = self.cli("run", "pr", run["id"], PR_URL, "--repo", "api")
        self.assertFalse(stored["no_pr"])
        checked = self.cli("run", "prs", run["id"])
        self.assertEqual(checked["prs"][0]["state"], "OPEN")

    def test_code_host_without_pr_state_is_unchecked(self):
        self.cli("project", "set", "shop", "integrations.code_host", '{"adapter": "git"}')
        run = self.cli("run", "new", "Change", "--project", "shop", "--repo", "api")
        self.cli("run", "pr", run["id"], "https://code.example.com/shop/api/pull/1", "--repo", "api")
        checked = self.cli("run", "prs", run["id"])
        self.assertEqual((checked["prs"][0]["state"], checked["prs"][0]["reason"]),
                         ("unchecked", "the code host git has no pr_state"))


class IssueSyncTest(FactoryCase):
    def test_sync_updates_mirrors_only(self):
        install(self.home, tracker())
        self.project()
        self.cli("project", "set", "shop", "integrations.tracker", '{"adapter": "acme-tracker"}')
        self.cli("issue", "import", "shop", "ACME-1", "--title", "First")
        self.cli("issue", "import", "shop", "https://tracker.example.com/ACME-2", "--title", "Second")
        self.cli("issue", "add", "shop", "Local")
        self.cli("issue", "set", "SHOP-1", "--status", "ready")
        os.environ["ACME_SYNC_JSON"] = json.dumps({"issues": [
            {"external_key": "ACME-1", "title": "First, renamed", "url": "https://tracker.example.com/ACME-1",
             "external_status": "In Progress", "updated_at": "2026-10-21T10:00:00Z"},
            {"external_key": "ACME-9", "title": "Not mirrored", "url": "x", "external_status": "Done"},
        ]})
        result = self.cli("issue", "sync", "shop")
        self.assertEqual((result["updated"], result["not_returned"]), (["SHOP-1"], ["SHOP-2"]))
        first = self.cli("issue", "show", "SHOP-1")
        self.assertEqual((first["title"], first["external_status"], first["status"]), ("First, renamed", "In Progress", "ready"))
        self.assertEqual(len(self.cli("issue", "list")), 3)
        self.cli("project", "add", "blog", "--key", "BLOG")
        self.assertIn("uses the local tracker", self.fails("issue", "sync", "blog"))


class AdapterNewTest(FactoryCase):
    def test_each_slot_until_filled(self):
        for slot, capabilities, sections in (
            ("tracker", {"fetch_issue": "mcp", "import": "core"}, ["## Setup", "## fetch_issue", "## Never"]),
            ("code_host", {"push": "git", "open_pr": "mcp"}, ["## Setup", "## push", "## open_pr", "## Never"]),
            ("design", {"fetch_design": "mcp"}, ["## Setup", "## fetch_design", "## Never"]),
        ):
            with self.subTest(slot=slot):
                name = f"acme-{slot.replace('_', '-')}"
                made = self.cli("adapter", "new", name, "--slot", slot)
                self.assertEqual(made["dir"], os.path.join(self.home, "catalogue", "adapters", name))
                data = json.loads(read(made["adapter_json"]))
                self.assertEqual({key: spec["via"] for key, spec in data["capabilities"].items()}, capabilities)
                self.assertEqual((data["vendors"], data["settings"], data["patterns"]), ([], {}, {}))
                text = read(made["adapter_md"])
                headings = [line for line in text.splitlines() if line.startswith("## ")]
                self.assertEqual(headings, sections)
                code, out, _ = self.call("catalogue", "check")
                problems = [entry for entry in json.loads(out)["problems"] if f"/{name}/" in entry["path"]]
                self.assertEqual(code, 1)
                self.assertEqual(sorted(os.path.basename(entry["path"]) for entry in problems), ["adapter.json", "adapter.md"])
                self.assertTrue(all("still to fill in" in entry["problem"] for entry in problems))
                data = json.loads(read(made["adapter_json"]).replace("<fill in: server>", "acme").replace(
                    "<fill in: tool that reads one issue>", "get_issue").replace(
                    "<fill in: tool that opens or updates a draft PR>", "open_pr").replace(
                    "<fill in: tool that reads one frame>", "get_frame"))
                write(made["adapter_json"], json.dumps(data))
                write(made["adapter_md"], "\n".join(line for line in text.splitlines() if "<fill in" not in line) + "\n")
                code, out, _ = self.call("catalogue", "check")
                self.assertEqual(json.loads(out)["problems"], [])
                self.assertEqual(code, 0)

    def test_refusals_and_dir(self):
        folder = os.path.join(self.tmp, "team-catalogue")
        made = self.cli("adapter", "new", "acme-tracker", "--slot", "tracker", "--dir", folder)
        self.assertEqual(made["dir"], os.path.join(folder, "adapters", "acme-tracker"))
        self.assertIn("exists", self.fails("adapter", "new", "acme-tracker", "--slot", "tracker", "--dir", folder))
        self.assertIn("adapter name", self.fails("adapter", "new", "Bad Name", "--slot", "tracker", code=2))
        self.assertEqual(self.call("adapter", "new", "x-y", "--slot", "chat")[0], 2)

    def test_a_new_adapter_can_be_used_once_filled(self):
        made = self.cli("adapter", "new", "acme-tracker", "--slot", "tracker")
        data = json.loads(read(made["adapter_json"]))
        data["capabilities"]["fetch_issue"]["requires"]["mcp"] = ["mcp__acme__get_issue"]
        data["patterns"] = {"key_pattern": "^ACME-[0-9]+$"}
        write(made["adapter_json"], json.dumps(data))
        self.project()
        shown = self.cli("project", "set", "shop", "integrations.tracker", '{"adapter": "acme-tracker"}')
        fetch = shown["integrations"]["tracker"]["capabilities"]["fetch_issue"]
        self.assertEqual((fetch["status"], fetch["requires"]["mcp"]), ("check_in_session", ["mcp__acme__get_issue"]))


if __name__ == "__main__":
    unittest.main()
