import os
import sqlite3
import threading
import time
import unittest

from helpers import QUESTION, FactoryCase, code_job

from factory import db, home, questions


class AgentRegistryTest(FactoryCase):
    def test_add_list_show_remove(self):
        self.assertEqual(self.cli("agent", "list"), [])
        added = self.cli("agent", "add", "helper", "--description", "Answers while I am away")
        self.assertEqual((added["name"], added["description"], added["seen_at"]), ("helper", "Answers while I am away", None))
        self.assertEqual((added["answers"], added["follow_ups"], added["recent"]), (0, 0, []))
        again = self.cli("agent", "add", "helper")
        self.assertEqual(again["description"], "Answers while I am away")
        changed = self.cli("agent", "add", "helper", "--description", "Reads the inbox")
        self.assertEqual(changed["description"], "Reads the inbox")
        self.assertEqual([agent["name"] for agent in self.cli("agent", "list")], ["helper"])
        self.assertIn("helper", self.cli("agent", "list", "--table"))
        self.assertEqual(self.cli("agent", "remove", "helper"), {"removed": "helper"})
        self.assertIn("no connected agent helper", self.fails("agent", "show", "helper"))
        kinds = [event["kind"] for event in self.cli("events")["events"]]
        self.assertEqual([kind for kind in kinds if kind.startswith("agent.")], ["agent.add", "agent.set", "agent.remove"])

    def test_names_are_checked(self):
        for name in ("", "has space", ".dot", "x" * 41, "a/b"):
            with self.subTest(name=name):
                self.assertIn("an agent name is", self.fails("agent", "add", name, code=2))
        for name in ("human", "dashboard", "Orchestrator"):
            with self.subTest(name=name):
                self.assertIn("reserved", self.fails("agent", "add", name, code=2))


class AnsweringAgentTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.run = self.started_run([code_job("lead")])
        self.run_id = self.run["id"]

    def test_an_answer_with_by_connects_the_agent(self):
        one = self.cli("question", "ask", self.run_id, QUESTION)
        two = self.cli("question", "ask", self.run_id, QUESTION, "--job", "lead")
        self.cli("question", "answer", str(one["id"]), "A", "--by", "assistant")
        self.cli("question", "clarify", str(two["id"]), "Which client?", "--by", "assistant")
        shown = self.cli("agent", "show", "assistant")
        self.assertEqual((shown["answers"], shown["follow_ups"]), (1, 1))
        self.assertIsNotNone(shown["seen_at"])
        self.assertEqual([(item["question_id"], item["kind"]) for item in shown["recent"]],
                         [(two["id"], "clarify"), (one["id"], "answer")])
        self.assertEqual(shown["recent"][0]["question"], "Which field should the endpoint return?")
        self.assertEqual(shown["recent"][0]["run_id"], self.run_id)
        self.assertIn("an agent name is", self.fails("question", "answer", str(two["id"]), "A", "--by", "two words", code=2))
        self.cli("question", "answer", str(two["id"]), "A")
        self.assertEqual([agent["name"] for agent in self.cli("agent", "list")], ["assistant"])

    def test_inbox_lists_what_waits_for_the_user(self):
        plan = os.path.join(self.run["run_dir"], "plan.md")
        confirm = self.cli("question", "ask", self.run_id, "Approve the plan? Answer yes, or say what to change.", "--plain",
                           "--path", plan)
        asked = self.cli("question", "ask", self.run_id, QUESTION, "--job", "lead", "--by", "produce:build")
        answered = self.cli("question", "ask", self.run_id, QUESTION)
        self.cli("question", "answer", str(answered["id"]), "A")
        found = self.cli("inbox")
        self.assertEqual(found["count"], 2)
        items = {item["id"]: item for item in found["items"]}
        self.assertEqual(set(items), {confirm["id"], asked["id"]})
        plan_item = items[confirm["id"]]
        self.assertEqual((plan_item["type"], plan_item["kind"], plan_item["expects"]),
                         ("question", "plan_confirm", "yes, or say what to change"))
        self.assertEqual(plan_item["plan"], plan)
        self.assertEqual(plan_item["repos"], ["api"])
        self.assertEqual([(job["id"], job["pipeline"]) for job in plan_item["jobs"]],
                         [("lead", [{"step": "build", "gates": ["review-code"]}])])
        question = items[asked["id"]]
        self.assertEqual(question["kind"], "question")
        self.assertEqual(question["decision"], "Which field should the endpoint return?")
        self.assertEqual([(option["letter"], option["recommended"]) for option in question["options"]], [("A", True), ("B", False)])
        self.assertEqual(question["job"]["title"], "feat: lead")
        self.assertEqual(question["run"]["id"], self.run_id)
        self.assertEqual(question["answer"][:4], ["factory", "question", "answer", str(asked["id"])])
        self.assertEqual(question["answer"][-1], "<your agent name>")
        self.assertIsInstance(found["cursor"], int)
        self.assertEqual(self.cli("inbox", "--project", "other")["count"], 0)

    def test_inbox_as_an_agent_marks_it_seen(self):
        self.cli("question", "ask", self.run_id, QUESTION)
        found = self.cli("inbox", "--as", "assistant")
        self.assertEqual(found["items"][0]["answer"][-2:], ["--by", "assistant"])
        self.assertIsNotNone(self.cli("agent", "show", "assistant")["seen_at"])

    def test_events_since_a_cursor(self):
        start = self.cli("events", "--limit", "1")["cursor"]
        asked = self.cli("question", "ask", self.run_id, QUESTION)
        self.cli("question", "answer", str(asked["id"]), "A")
        found = self.cli("events", "--since", start)
        self.assertEqual([event["kind"] for event in found["events"]], ["question.ask", "question.status"])
        self.assertEqual(found["events"][0]["payload"]["question"], asked["id"])
        self.assertEqual(found["cursor"], found["events"][-1]["id"])
        self.assertEqual(self.cli("events", "--since", found["cursor"]), {"events": [], "cursor": found["cursor"]})
        only = self.cli("events", "--since", start, "--kind", "question.status")
        self.assertEqual([event["kind"] for event in only["events"]], ["question.status"])
        prefix = self.cli("events", "--since", start, "--kind", "question", "--run", self.run_id)
        self.assertEqual(len(prefix["events"]), 2)
        self.assertEqual(self.cli("events", "--since", start, "--limit", "1")["cursor"], found["events"][0]["id"])
        self.assertIn("--limit", self.fails("events", "--limit", "0", code=2))
        self.assertEqual(self.cli("events", "--since", found["cursor"], "--wait", "--timeout", "0")["events"], [])

    def test_events_wait_wakes_up_on_a_new_event(self):
        start = self.cli("events", "--limit", "1")["cursor"]

        def ask_later():
            time.sleep(0.3)
            conn = home.connect()
            try:
                questions.ask(conn, self.run_id, QUESTION)
            finally:
                conn.close()

        thread = threading.Thread(target=ask_later)
        thread.start()
        found = self.cli("events", "--since", start, "--wait", "--timeout", "10")
        thread.join()
        self.assertEqual(found["events"][0]["kind"], "question.ask")


class HumanGateInboxTest(FactoryCase):
    def test_a_human_gate_question_has_its_own_kind(self):
        run = self.started_run([code_job("migrate-db", steps=[{"step": "build", "gates": ["review-code", "approve"]}])])
        ref = f"{run['id']}/migrate-db"
        started = self.cli("job", "start", ref)
        self.job_worktree(started)
        self.output(started["group"])
        self.cli("step", "sync", ref)
        self.verdict(started["group"]["round_dir"], "review-code")
        human = self.cli("gate", "sync", ref)["human"][0]
        self.cli("question", "ask", run["id"], human["question"], "--job", "migrate-db", "--by", "gate:approve", "--path", human["path"])
        item = self.cli("inbox")["items"][0]
        self.assertEqual((item["kind"], item["expects"]), ("human_gate", "A to approve, or what to change"))


class MigrationBackfillTest(FactoryCase):
    def test_earlier_answer_authors_become_connected_agents(self):
        path = os.path.join(self.tmp, "factory.db")
        conn = db.connect(path, target=2)
        conn.execute("INSERT INTO projects (name, key, config_path, created_at) VALUES ('shop', 'SHOP', '/p', 'now')")
        conn.execute("INSERT INTO runs (id, project, feature, slug, feature_branch, created_at) "
                     "VALUES ('r1', 'shop', 'f', 's', 'feature/s', 'now')")
        conn.execute("INSERT INTO questions (run_id, asked_by, text, status, asked_at) VALUES ('r1', 'orchestrator', 'q', 'open', 'now')")
        for kind, author, at in (("answer", "assistant", "t1"), ("clarify", "assistant", "t2"), ("answer", "human", "t3"),
                                 ("answer", "dashboard", "t4"), ("reply", "planner", "t5")):
            conn.execute("INSERT INTO question_messages (question_id, kind, author, text, at) VALUES (1, ?, ?, 'x', ?)",
                         (kind, author, at))
        conn.close()
        conn = db.connect(path)
        conn.row_factory = sqlite3.Row
        found = [dict(row) for row in conn.execute("SELECT name, added_at, seen_at FROM connected_agents")]
        conn.close()
        self.assertEqual(found, [{"name": "assistant", "added_at": "t1", "seen_at": "t2"}])


if __name__ == "__main__":
    unittest.main()
