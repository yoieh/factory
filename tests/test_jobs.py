import io
import os
import unittest
from unittest import mock

from helpers import QUESTION, FactoryCase, code_job, files_job, git, read, write


class JobFlowTest(FactoryCase):
    def start(self, run, job_id):
        started = self.cli("job", "start", f"{run['id']}/{job_id}")
        if started["kind"] == "code":
            self.job_worktree(started)
        return started

    def test_ready_respects_depends_and_parallel(self):
        run = self.started_run([code_job("a"), code_job("b"), code_job("c"), code_job("d", depends=["a"])], parallel="2")
        ready = self.cli("job", "ready", run["id"])
        self.assertEqual(ready["ready"], ["a", "b"])
        self.assertEqual(ready["queued"], ["c"])
        self.assertEqual(ready["waiting"], ["d"])
        self.start(run, "a")
        self.assertEqual(self.cli("job", "ready", run["id"])["ready"], ["b"])
        self.start(run, "b")
        self.assertEqual(self.cli("job", "ready", run["id"])["ready"], [])
        self.assertIn("waits for a", self.fails("job", "start", f"{run['id']}/d"))
        group = self.cli("job", "show", f"{run['id']}/a")["group"]
        self.output(group)
        self.cli("step", "sync", f"{run['id']}/a")
        self.assertEqual(self.cli("job", "ready", run["id"])["ready"], ["c"])

    def test_job_ready_needs_a_running_run(self):
        self.repo("api")
        self.project(repos=[os.path.join(self.tmp, "repos", "api")])
        run = self.cli("run", "new", "Not yet", "--project", "shop")
        self.assertIn("is planning, not running", self.fails("job", "ready", run["id"]))

    def test_start_returns_paths(self):
        run = self.started_run([code_job("lead"), files_job("notes", reads=["api"])])
        started = self.cli("job", "start", f"{run['id']}/lead")
        feature = next(repo for repo in run["repos"] if repo["name"] == "api")
        self.assertEqual(started["kind"], "code")
        self.assertEqual(started["repo"], "api")
        self.assertEqual(started["branch"], f"{run['feature_branch']}--lead")
        self.assertEqual(started["worktree"], os.path.join(self.home, "worktrees", run["id"], "lead"))
        self.assertEqual(started["feature_worktree"], feature["feature_worktree"])
        self.assertEqual(started["setup_hint"], "make deps")
        self.assertEqual(started["out_dir"], None)
        self.assertEqual(started["sources"], "none")
        group = started["group"]
        self.assertEqual(group["base_sha"], git(feature["feature_worktree"], "rev-parse", "HEAD"))
        self.assertEqual((group["position"], group["step"], group["round"], group["agent"]), (1, "build", 1, "factory-worker"))
        self.assertTrue(group["group_dir"].endswith("jobs/lead/groups/01-build"))
        self.assertTrue(os.path.isdir(group["round_dir"]))
        self.assertTrue(group["step_file"].endswith("catalogue/steps/build.md"))
        notes = self.cli("job", "start", f"{run['id']}/notes")
        self.assertEqual(notes["sources"], f"api={feature['feature_worktree']}")
        self.assertTrue(os.path.isdir(notes["out_dir"]))
        self.assertEqual((notes["repo"], notes["branch"], notes["setup_hint"]), (None, None, None))
        self.assertIsNone(notes["group"]["base_sha"])
        self.assertIn("not queued", self.fails("job", "start", f"{run['id']}/lead"))

    def test_start_needs_the_feature_branch(self):
        api = self.repo("api")
        self.project(repos=[api])
        run = self.cli("run", "new", "No worktree", "--project", "shop")
        self.write_plan(run["id"], [code_job("lead")])
        self.cli("plan", "load", run["id"])
        self.cli("run", "status", run["id"], "running")
        self.assertIn("create its worktree first", self.fails("job", "start", f"{run['id']}/lead"))

    def test_step_sync_statuses(self):
        run = self.started_run([code_job("lead")])
        ref = f"{run['id']}/lead"
        group = self.start(run, "lead")["group"]
        self.assertEqual(self.cli("step", "sync", ref)["status"], "MISSING")
        self.output(group, "Done, I think.\n")
        self.assertEqual(self.cli("step", "sync", ref)["status"], "INVALID")
        self.output(group, "STATUS: BLOCKED the database does not start\n")
        blocked = self.cli("step", "sync", ref)
        self.assertEqual((blocked["status"], blocked["reason"]), ("BLOCKED", "the database does not start"))
        self.cli("step", "sync", ref)
        events = [entry["kind"] for entry in self.cli("run", "show", run["id"])["events"]]
        self.assertEqual(events.count("step.blocked"), 1)
        self.output(group, QUESTION)
        asked = self.cli("step", "sync", ref)
        self.assertEqual(asked["status"], "QUESTION")
        self.assertEqual(self.cli("step", "sync", ref)["question"], asked["question"])
        question = self.cli("question", "list", "--run", run["id"])[0]
        self.assertEqual((question["asked_by"], question["job_id"]), ("produce:build", "lead"))
        self.output(group, "QUESTION: Which?\n")
        self.assertIn("question_error", self.cli("step", "sync", ref))
        self.output(group, "status: done\n")
        done = self.cli("step", "sync", ref)
        self.assertEqual((done["status"], done["phase"]), ("DONE", "gate"))
        self.assertEqual(done["gates"], [{
            "gate": "review-code", "agent": "factory-reviewer", "model": None,
            "gate_file": self.cli("catalogue", "show")["steps"]["review-code"]["path"],
            "review_file": os.path.join(group["round_dir"], "review-code.md"), "round_dir": group["round_dir"],
            "human": False,
        }])
        self.assertEqual(self.cli("step", "sync", ref)["gates"], done["gates"])

    def test_gate_sync_wait_for_all_pass_and_changes(self):
        run = self.started_run([code_job("lead", steps=[{"step": "build", "gates": ["review-code", "review-security"]}])])
        ref = f"{run['id']}/lead"
        group = self.start(run, "lead")["group"]
        self.output(group)
        self.cli("step", "sync", ref)
        pending = self.cli("gate", "sync", ref)
        self.assertEqual((pending["complete"], pending["outcome"]), (False, "PENDING"))
        self.verdict(group["round_dir"], "review-code", "CHANGES")
        pending = self.cli("gate", "sync", ref)
        self.assertEqual((pending["complete"], pending["outcome"]), (False, "PENDING"))
        self.assertIn("WAIT FOR ALL", self.fails("job", "round", ref))
        write(os.path.join(group["round_dir"], "review-security.md"), "Looks fine\n")
        invalid = self.cli("gate", "sync", ref)
        self.assertEqual([entry["verdict"] for entry in invalid["gates"]], ["CHANGES", "INVALID"])
        self.verdict(group["round_dir"], "review-security", "PASS")
        changes = self.cli("gate", "sync", ref)
        self.assertEqual((changes["complete"], changes["outcome"], changes["phase"]), (True, "CHANGES", "gate"))
        second = self.cli("job", "round", ref)
        self.assertEqual(second["round"], 2)
        self.assertTrue(second["round_dir"].endswith("round-2"))
        self.output(group)
        resynced = self.cli("step", "sync", ref)
        self.assertTrue(resynced["gates"][0]["review_file"].endswith("round-2/review-code.md"))
        self.verdict(second["round_dir"], "review-code")
        self.verdict(second["round_dir"], "review-security")
        passed = self.cli("gate", "sync", ref)
        self.assertEqual((passed["outcome"], passed["phase"]), ("PASS", "passed"))
        verdicts = self.cli("job", "show", ref)["verdicts"]
        self.assertEqual([(entry["round"], entry["gate"], entry["verdict"]) for entry in verdicts], [
            (1, "review-code", "CHANGES"), (1, "review-security", "PASS"),
            (2, "review-code", "PASS"), (2, "review-security", "PASS"),
        ])
        rounds = [entry for entry in self.cli("run", "show", run["id"])["events"] if entry["kind"] == "job.round"]
        self.assertEqual([entry["payload"]["round"] for entry in rounds], [1, 2])

    def test_gate_question(self):
        run = self.started_run([code_job("lead")])
        ref = f"{run['id']}/lead"
        group = self.start(run, "lead")["group"]
        self.output(group)
        self.cli("step", "sync", ref)
        write(os.path.join(group["round_dir"], "review-code.md"), QUESTION)
        result = self.cli("gate", "sync", ref)
        self.assertEqual(len(result["questions"]), 1)
        self.assertEqual(self.cli("gate", "sync", ref)["questions"], result["questions"])
        self.assertEqual(result["gates"][0]["verdict"], "QUESTION")
        write(os.path.join(group["round_dir"], "review-code.md"), "QUESTION: Really?\n")
        errors = self.cli("gate", "sync", ref)["question_errors"]
        self.assertEqual(errors[0]["gate"], "review-code")

    def test_cap_and_round_refusal(self):
        run = self.started_run([code_job("lead")], max_rounds="2")
        ref = f"{run['id']}/lead"
        group = self.start(run, "lead")["group"]
        for number in (1, 2):
            self.output(group)
            self.cli("step", "sync", ref)
            self.verdict(os.path.join(group["group_dir"], f"round-{number}"), "review-code", "CHANGES")
            synced = self.cli("gate", "sync", ref)
            if number == 1:
                self.cli("job", "round", ref)
        self.assertEqual(synced["phase"], "capped")
        self.assertEqual([point["kind"] for point in synced["open_points"]], ["must_fix", "note"])
        self.assertEqual(synced["open_points"][1]["text"], "[human] check the copy")
        self.assertIn("is capped", self.fails("job", "round", ref))
        self.assertEqual(self.cli("job", "next", ref)["stage"], "merging")

    def test_blocked_retry_is_refused_at_the_cap(self):
        run = self.started_run([code_job("lead")], max_rounds="1")
        ref = f"{run['id']}/lead"
        group = self.start(run, "lead")["group"]
        self.output(group, "STATUS: BLOCKED no network\n")
        self.cli("step", "sync", ref)
        self.assertIn("at its round cap (1)", self.fails("job", "round", ref))
        self.cli("run", "rounds", run["id"], "3")
        self.assertEqual(self.cli("job", "round", ref)["round"], 2)

    def test_next_through_two_groups_to_merging(self):
        run = self.started_run([code_job("bug", steps=[
            {"step": "reproduce", "gates": ["review-code"]}, {"step": "fix", "gates": ["review-code", "verify"]},
        ])])
        ref = f"{run['id']}/bug"
        started = self.start(run, "bug")
        self.assertIn("not passed or capped", self.fails("job", "next", ref))
        self.cli("job", "agent", ref, "bug-01")
        head = self.commit(started["worktree"], "test_bug.py", "assert False\n", "test: show the bug")
        self.output(started["group"])
        self.cli("step", "sync", ref)
        self.verdict(started["group"]["round_dir"], "review-code")
        self.cli("gate", "sync", ref)
        moved = self.cli("job", "next", ref)
        self.assertEqual(moved["stage"], "running")
        second = moved["group"]
        self.assertEqual((second["position"], second["step"], second["round"], second["base_sha"]), (2, "fix", 1, head))
        shown = self.cli("job", "show", ref)
        self.assertEqual(shown["previous"], started["group"]["group_dir"])
        self.assertEqual(shown["groups"][0]["producer"], "bug-01")
        self.assertIsNone(shown["groups"][1]["producer"])
        self.output(second)
        self.cli("step", "sync", ref)
        self.verdict(second["round_dir"], "review-code")
        self.verdict(second["round_dir"], "verify")
        self.cli("gate", "sync", ref)
        self.assertEqual(self.cli("job", "next", ref), {"stage": "merging", "group": None})
        self.assertIn("cannot move from merging to delivering", self.fails("job", "stage", ref, "delivering"))
        self.assertEqual(self.cli("job", "stage", ref, "done")["stage"], "done")

    def test_files_job_to_delivering_with_upstream(self):
        run = self.started_run([files_job("research"), files_job("write-up", depends=["research"],
                                                                 steps=[{"step": "write-doc", "gates": ["review-docs"]}])])
        ref = f"{run['id']}/research"
        group = self.cli("job", "start", ref)["group"]
        self.output(group, "STATUS: DONE\nTest command: none\nResult: findings written\n")
        self.cli("step", "sync", ref)
        self.verdict(group["round_dir"], "review-findings")
        self.cli("gate", "sync", ref)
        self.assertEqual(self.cli("job", "next", ref)["stage"], "delivering")
        self.cli("job", "stage", ref, "done")
        later = self.cli("job", "start", f"{run['id']}/write-up")
        shown = self.cli("job", "show", f"{run['id']}/write-up")
        self.assertEqual(shown["upstream"], os.path.join(run["run_dir"], "jobs", "research"))
        self.assertEqual(shown["previous"], "none")
        self.assertEqual(later["group"]["step"], "write-doc")

    def test_group_without_gates_passes_on_done(self):
        folder = os.path.join(self.home, "catalogue", "steps")
        write(os.path.join(folder, "sketch.md"),
              "---\nname: sketch\nrole: produce\nkinds: [files]\nsummary: s\nwhen: w\ngates_optional: true\n---\n")
        run = self.started_run([files_job("idea", steps=[{"step": "sketch", "gates": []}])])
        ref = f"{run['id']}/idea"
        group = self.cli("job", "start", ref)["group"]
        self.output(group)
        done = self.cli("step", "sync", ref)
        self.assertEqual((done["phase"], done["gates"]), ("passed", []))
        self.assertEqual(self.cli("job", "next", ref)["stage"], "delivering")

    def finish_job(self, run, job_id):
        ref = f"{run['id']}/{job_id}"
        started = self.start(run, job_id)
        self.commit(started["worktree"], f"{job_id}.txt", f"{job_id}\n", f"feat: {job_id}")
        self.output(started["group"])
        self.cli("step", "sync", ref)
        self.verdict(started["group"]["round_dir"], "review-code")
        self.cli("gate", "sync", ref)
        self.cli("job", "next", ref)
        feature = started["feature_worktree"]
        git(feature, "merge", "-q", "--squash", started["branch"])
        git(feature, "commit", "-q", "-m", f"feat: {job_id}")
        self.cli("job", "stage", ref, "done")
        git(started["repo_path"], "worktree", "remove", "--force", started["worktree"])
        git(started["repo_path"], "branch", "-D", started["branch"])
        return started

    def test_merge_conflict_goes_back_to_the_producer(self):
        run = self.started_run([code_job("lead")])
        ref = f"{run['id']}/lead"
        started = self.start(run, "lead")
        self.commit(started["worktree"])
        self.output(started["group"])
        self.cli("step", "sync", ref)
        self.verdict(started["group"]["round_dir"], "review-code")
        self.cli("gate", "sync", ref)
        self.cli("job", "next", ref)
        self.cli("event", run["id"], "merge.conflict", "--job", "lead", "--payload", '{"files": ["a.py"]}')
        back = self.cli("job", "stage", ref, "running")
        self.assertTrue(back["conflict"])
        self.assertEqual((back["stage"], back["group"]["phase"], back["group"]["round"]), ("running", "produce", 1))
        self.assertEqual(self.cli("step", "sync", ref), {"status": "DONE", "stage": "merging"})
        shown = self.cli("job", "show", ref)
        self.assertEqual((shown["stage"], shown["conflict"], shown["group"]["phase"]), ("merging", False, "passed"))

    def test_rework_reopens_a_done_job_and_its_run(self):
        run = self.started_run([code_job("bug", steps=[
            {"step": "build", "gates": ["review-code"]}, {"step": "build", "gates": ["review-code"]},
        ])])
        ref = f"{run['id']}/bug"
        started = self.start(run, "bug")
        for position in (1, 2):
            group = self.cli("job", "show", ref)["group"]
            self.commit(started["worktree"], f"part{position}.txt", "x\n", f"feat: part {position}")
            self.output(group)
            self.cli("step", "sync", ref)
            self.verdict(group["round_dir"], "review-code")
            self.cli("gate", "sync", ref)
            self.cli("job", "next", ref)
        git(started["feature_worktree"], "merge", "-q", "--squash", started["branch"])
        git(started["feature_worktree"], "commit", "-q", "-m", "feat: bug")
        self.cli("job", "stage", ref, "done")
        self.cli("run", "status", run["id"], "finishing")
        self.cli("run", "status", run["id"], "done")
        feature_head = git(started["feature_worktree"], "rev-parse", "HEAD")
        reworked = self.cli("job", "rework", ref, "Keep the old field name", "--group", "1")
        self.assertEqual(reworked["stage"], "running")
        first, second = reworked["groups"]
        self.assertEqual((first["phase"], first["round"], first["base_sha"]), ("produce", 2, feature_head))
        self.assertEqual((second["phase"], second["round"], second["base_sha"]), ("pending", 0, None))
        self.assertEqual(reworked["group"]["position"], 1)
        rework_file = os.path.join(first["round_dir"], "rework.md")
        self.assertIn("Keep the old field name", read(rework_file))
        self.assertEqual(self.cli("run", "show", run["id"])["status"], "running")
        self.assertIn("is running; only a done or dropped job", self.fails("job", "rework", ref, "again"))

    def test_rework_grows_the_cap(self):
        run = self.started_run([code_job("lead")], max_rounds="1")
        ref = f"{run['id']}/lead"
        started = self.start(run, "lead")
        self.commit(started["worktree"])
        self.output(started["group"])
        self.cli("step", "sync", ref)
        self.verdict(started["group"]["round_dir"], "review-code")
        self.cli("gate", "sync", ref)
        self.cli("job", "next", ref)
        self.cli("job", "stage", ref, "done")
        reworked = self.cli("job", "rework", ref, "Rename the flag")
        self.assertEqual((reworked["group"]["round"], reworked["group"]["max_round"]), (2, 2))

    def test_drop_a_job(self):
        run = self.started_run([files_job("research"), files_job("write-up", depends=["research"]), code_job("lead")])
        ref = f"{run['id']}/research"
        self.cli("job", "start", ref)
        self.assertIn("neither done nor dropped: research, write-up, lead",
                      self.fails("run", "status", run["id"], "finishing"))
        self.assertIn("say why", self.fails("job", "drop", ref, "  ", code=2))
        dropped = self.cli("job", "drop", ref, "the harness refused the write-up")
        self.assertEqual((dropped["stage"], dropped["dependants"]), ("dropped", ["write-up"]))
        notes = read(os.path.join(run["run_dir"], "retro-notes.md"))
        self.assertIn("- dropped the job: the harness refused the write-up (research)", notes)
        ready = self.cli("job", "ready", run["id"])
        self.assertEqual(ready["blocked"], [{"job": "write-up", "dropped": ["research"]}])
        self.assertNotIn("write-up", ready["ready"] + ready["waiting"])
        self.assertIn("only an unfinished job", self.fails("job", "drop", ref, "again"))
        self.cli("job", "drop", f"{run['id']}/write-up", "it needed the research")
        self.assertIn("neither done nor dropped: lead", self.fails("run", "status", run["id"], "finishing"))
        lead = self.cli("job", "start", f"{run['id']}/lead")
        self.job_worktree(lead)
        self.output(lead["group"])
        self.cli("step", "sync", f"{run['id']}/lead")
        self.verdict(lead["group"]["round_dir"], "review-code")
        self.cli("gate", "sync", f"{run['id']}/lead")
        self.cli("job", "next", f"{run['id']}/lead")
        self.cli("job", "stage", f"{run['id']}/lead", "done")
        self.assertTrue(self.cli("run", "status", run["id"], "finishing")["changed"])
        restarted = self.cli("job", "rework", ref, "try the write-up again")
        self.assertEqual((restarted["stage"], restarted["group"]["round"]), ("running", 2))
        self.assertEqual(self.cli("run", "show", run["id"])["status"], "running")

    def test_step_save_stores_a_refused_deliverable(self):
        run = self.started_run([files_job("research"), code_job("lead", steps=[{"step": "build", "gates": ["review-code"]}])])
        research = self.cli("job", "start", f"{run['id']}/research")
        ref = f"{run['id']}/research"
        saved = self.save(ref, "out/findings.md", "# Findings\n\n- one\n")
        self.assertEqual(saved["path"], os.path.join(research["out_dir"], "findings.md"))
        self.assertEqual(read(saved["path"]), "# Findings\n\n- one\n")
        self.save(ref, os.path.join(research["group"]["group_dir"], "output.md"), "STATUS: DONE\nTest command: none\nResult: findings\n")
        self.assertEqual(self.cli("step", "sync", ref)["status"], "DONE")
        gate = self.save(ref, "round-1/review-findings.md", "VERDICT: PASS\nFine.")
        self.assertTrue(read(gate["path"]).endswith("Fine.\n"))
        self.assertEqual(self.cli("gate", "sync", ref)["outcome"], "PASS")
        self.cli("job", "start", f"{run['id']}/lead")
        for target, message in (("../../plan.md", "is not a file this step may save"),
                                ("out/notes.md", "is not a file this step may save"),
                                ("round-1/review-security.md", "is not a file this step may save"),
                                (os.path.join(run["run_dir"], "plan.md"), "is not a file this step may save")):
            with self.subTest(target=target):
                self.assertIn(message, self.save(f"{run['id']}/lead", target, "x", code=1))
        self.assertIn("is empty", self.save(f"{run['id']}/lead", "output.md", "  ", code=2))
        events = [entry for entry in self.cli("run", "show", run["id"])["events"] if entry["kind"] == "step.save"]
        self.assertEqual(len(events), 3)

    def save(self, ref, target, text, code=0):
        with mock.patch("sys.stdin", io.StringIO(text)):
            if code:
                return self.fails("step", "save", ref, target, code=code)
            return self.cli("step", "save", ref, target, "--by", "orchestrator")

    def test_bad_refs(self):
        run = self.started_run([code_job("lead")])
        self.assertIn("bad ref", self.fails("job", "show", "nonsense", code=2))
        self.assertIn("has no job ghost", self.fails("job", "show", f"{run['id']}/ghost"))
        self.assertIn("no run nope", self.fails("job", "show", "nope/lead"))


if __name__ == "__main__":
    unittest.main()
