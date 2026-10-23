import os
import unittest

from helpers import FactoryCase, code_job, read

from factory import questions


class HumanGateTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.run = self.started_run(
            [code_job("migrate-db", title="chore: move the orders table",
                      steps=[{"step": "build", "gates": ["review-code", "approve"]}])],
            max_rounds="2",
        )
        self.ref = f"{self.run['id']}/migrate-db"
        started = self.cli("job", "start", self.ref)
        self.job_worktree(started)
        self.group = started["group"]
        self.output(self.group)
        self.cli("step", "sync", self.ref)

    def test_human_gate_is_asked_only_after_agent_gates_pass(self):
        synced = self.cli("gate", "sync", self.ref)
        self.assertEqual(synced["human"], [])
        self.verdict(self.group["round_dir"], "review-code", "CHANGES")
        synced = self.cli("gate", "sync", self.ref)
        self.assertEqual((synced["outcome"], synced["complete"], synced["human"]), ("CHANGES", True, []))
        self.assertIn("have not all passed", self.fails("gate", "human", self.ref, "approve", "pass"))

    def test_gate_sync_reports_a_valid_question(self):
        self.verdict(self.group["round_dir"], "review-code")
        synced = self.cli("gate", "sync", self.ref)
        self.assertEqual((synced["outcome"], synced["complete"]), ("PENDING", False))
        human = synced["human"][0]
        self.assertEqual(human["gate"], "approve")
        self.assertTrue(human["question"].startswith("QUESTION: Approve chore: move the orders table?"))
        self.assertIn(os.path.join(self.group["group_dir"], "output.md"), human["question"])
        self.assertEqual(questions.format_problems(human["question"]), [])
        self.assertIsNone(human["open_question"])
        asked = self.cli("question", "ask", self.run["id"], human["question"], "--job", "migrate-db",
                         "--by", "gate:approve", "--path", human["path"])
        self.assertEqual(self.cli("gate", "sync", self.ref)["human"][0]["open_question"], asked["id"])

    def test_pass_with_a_note(self):
        self.verdict(self.group["round_dir"], "review-code")
        self.cli("gate", "sync", self.ref)
        result = self.cli("gate", "human", self.ref, "approve", "pass", "--note", "fix the label later")
        text = read(result["review_file"])
        self.assertTrue(text.startswith("VERDICT: PASS"))
        self.assertIn("## Notes\n- [human] fix the label later", text)
        synced = self.cli("gate", "sync", self.ref)
        self.assertEqual((synced["outcome"], synced["phase"]), ("PASS", "passed"))

    def test_changes_starts_a_new_round_then_caps(self):
        self.verdict(self.group["round_dir"], "review-code")
        self.cli("gate", "sync", self.ref)
        self.assertIn("needs --note", self.fails("gate", "human", self.ref, "approve", "changes", code=2))
        result = self.cli("gate", "human", self.ref, "approve", "changes", "--note", "keep the old column")
        self.assertIn("## Must fix\n- keep the old column", read(result["review_file"]))
        synced = self.cli("gate", "sync", self.ref)
        self.assertEqual((synced["outcome"], synced["phase"]), ("CHANGES", "gate"))
        second = self.cli("job", "round", self.ref)
        self.output(self.group)
        self.cli("step", "sync", self.ref)
        self.verdict(second["round_dir"], "review-code")
        self.assertEqual(self.cli("gate", "sync", self.ref)["human"][0]["gate"], "approve")
        self.cli("gate", "human", self.ref, "approve", "changes", "--note", "still wrong")
        capped = self.cli("gate", "sync", self.ref)
        self.assertEqual(capped["phase"], "capped")
        self.assertEqual(capped["open_points"], [{"gate": "approve", "kind": "must_fix", "text": "still wrong"}])

    def test_only_human_gates_of_the_group(self):
        self.verdict(self.group["round_dir"], "review-code")
        self.assertIn("not a human gate", self.fails("gate", "human", self.ref, "review-code", "pass"))
        self.assertIn("not a gate of group 1", self.fails("gate", "human", self.ref, "verify", "pass"))


if __name__ == "__main__":
    unittest.main()
