import json
import os
import threading
import time
import unittest

from helpers import QUESTION, FactoryCase, code_job, write

from factory import home, questions


def body(options, extra=""):
    return f"QUESTION: Which one?\n\n**Why I ask:** Two ways.\n\n**Options**\n{options}\n\n**Status:** Waiting.\n{extra}"


class FormatTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(questions.format_problems(QUESTION), [])
        detailed = QUESTION + "\n<details><summary>Details</summary>\n\n" + "word " * 300 + "\n\n</details>\n"
        self.assertEqual(questions.format_problems(detailed), [])
        three = "- **A (Recommended):** one\n- **B:** two\n* **C**: three"
        self.assertEqual(questions.format_problems(body(three)), [])

    def test_each_rule(self):
        two = "- **A (recommended):** one\n- **B:** two"
        cases = [
            ("", "the question is empty"),
            ("Which one?\n", "the first line must be"),
            ("QUESTION:\n\n" + two, "the question line is empty"),
            ("QUESTION: Which one\n\n" + two, "must end with '?'"),
            ("QUESTION: " + "x" * 200 + "?\n\n" + two, "at most 200"),
            (body("- **A (recommended):** one"), "1 options, need 2 to 4"),
            (body(two + "\n- **C:** c\n- **D:** d\n- **E:** e"), "5 options"),
            (body("- **A (recommended):** one\n- **C:** two"), "lettered A, B, C, D in order"),
            (body("- **A:** one\n- **B:** two"), "found 0"),
            (body("- **A (recommended):** one\n- **B (recommended):** two"), "found 2"),
            (body(two, "<details>a</details>\n<details>b</details>\n"), "at most one <details>"),
            (body(two, "<details>open\n"), "not closed"),
            (body(two, "<details>a</details>\nAfter.\n"), "must be the last part"),
            (body(two, "word " * 120), "words under the question line"),
        ]
        for text, message in cases:
            with self.subTest(message=message):
                problems = questions.format_problems(text)
                self.assertTrue(any(message in problem for problem in problems), problems)


class QuestionFlowTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.run = self.started_run([code_job("lead")])
        self.run_id = self.run["id"]

    def test_ask_is_idempotent_and_checked(self):
        first = self.cli("question", "ask", self.run_id, QUESTION, "--job", "lead", "--by", "orchestrator")
        again = self.cli("question", "ask", self.run_id, QUESTION, "--job", "lead", "--by", "orchestrator")
        self.assertEqual(first["id"], again["id"])
        other = self.cli("question", "ask", self.run_id, QUESTION, "--by", "orchestrator")
        self.assertNotEqual(other["id"], first["id"])
        message = self.fails("question", "ask", self.run_id, "QUESTION: Why?\n", code=2)
        self.assertTrue(message.startswith("question format: "), message)
        self.fails("question", "ask", self.run_id, "QUESTION: Why?\n", code=2)
        refused = [entry for entry in self.cli("run", "show", self.run_id)["events"] if entry["kind"] == "question.refused"]
        self.assertEqual(len(refused), 1)
        plain = self.cli("question", "ask", self.run_id, "Approve the plan? Answer yes, or say what to change.", "--plain",
                         "--path", os.path.join(self.run["run_dir"], "plan.md"))
        self.assertEqual(plain["status"], "open")
        self.assertIn("--by must be", self.fails("question", "ask", self.run_id, QUESTION, "--by", "someone", code=2))

    def test_answer_clarify_reply_resolve(self):
        asked = self.cli("question", "ask", self.run_id, QUESTION, "--job", "lead", "--by", "produce:build",
                         "--path", os.path.join(self.run["run_dir"], "jobs", "lead", "groups", "01-build", "output.md"))
        clarified = self.cli("question", "clarify", str(asked["id"]), "What does the client expect?")
        group_dir = os.path.join(self.run["run_dir"], "jobs", "lead", "groups", "01-build")
        self.assertEqual(clarified["reply_file"], os.path.join(group_dir, f"reply-{asked['id']}.md"))
        self.assertEqual(clarified["status"], "clarify")
        self.assertIn("not open", self.fails("question", "clarify", str(asked["id"]), "again"))
        self.assertIn("no reply file", self.fails("question", "reply", str(asked["id"])))
        write(clarified["reply_file"], "Here is why\n")
        self.assertIn("REPLY:", self.fails("question", "reply", str(asked["id"])))
        write(clarified["reply_file"], "REPLY: The client reads the id.\n\nIt maps ids to rows.\n")
        replied = self.cli("question", "reply", str(asked["id"]))
        self.assertEqual(replied["status"], "open")
        self.assertEqual([entry["kind"] for entry in replied["thread"]], ["clarify", "reply"])
        self.assertIn("not answered", self.fails("question", "resolve", str(asked["id"])))
        answered = self.cli("question", "answer", str(asked["id"]), "B: keep the old field")
        self.assertEqual((answered["status"], answered["answer"]), ("answered", "B: keep the old field"))
        self.assertIn("is answered, not open", self.fails("question", "answer", str(asked["id"]), "A"))
        resolved = self.cli("question", "resolve", str(asked["id"]))
        self.assertEqual(resolved["status"], "resolved")
        listed = self.cli("question", "list", "--run", self.run_id, "--status", "resolved")
        self.assertEqual([entry["id"] for entry in listed], [asked["id"]])

    def test_reply_text_for_the_orchestrator(self):
        asked = self.cli("question", "ask", self.run_id, QUESTION)
        clarified = self.cli("question", "clarify", str(asked["id"]), "Why ask?", "--delivered")
        self.assertEqual(clarified["reply_file"], os.path.join(self.run["run_dir"], f"reply-{asked['id']}.md"))
        replied = self.cli("question", "reply", str(asked["id"]), "--text", "Because the issue is unclear.")
        self.assertEqual(replied["thread"][-1]["text"], "Because the issue is unclear.")

    def test_a_resolved_question_can_be_asked_again(self):
        first = self.cli("question", "ask", self.run_id, QUESTION, "--by", "gate:approve", "--job", "lead", "--path", "/x")
        self.cli("question", "answer", str(first["id"]), "A")
        self.cli("question", "resolve", str(first["id"]))
        second = self.cli("question", "ask", self.run_id, QUESTION, "--by", "gate:approve", "--job", "lead", "--path", "/x")
        self.assertNotEqual(first["id"], second["id"])

    def test_wait_returns_answers_and_follow_ups_once(self):
        one = self.cli("question", "ask", self.run_id, QUESTION)
        two = self.cli("question", "ask", self.run_id, QUESTION, "--job", "lead")
        self.cli("question", "answer", str(one["id"]), "A")
        self.cli("question", "clarify", str(two["id"]), "Which client?")
        found = self.cli("question", "wait", self.run_id, "--timeout", "1")
        self.assertEqual([(item["id"], item["kind"]) for item in found], [(one["id"], "answer"), (two["id"], "clarify")])
        self.assertEqual(found[0]["text"], "A")
        self.assertTrue(found[1]["reply_file"].endswith(f"reply-{two['id']}.md"))
        self.assertNotIn("reply_file", found[0])
        self.assertEqual(self.cli("question", "wait", self.run_id, "--timeout", "0"), [])

    def test_an_answer_says_who_wrote_it_for_the_user(self):
        one = self.cli("question", "ask", self.run_id, QUESTION)
        two = self.cli("question", "ask", self.run_id, QUESTION, "--job", "lead")
        answered = self.cli("question", "answer", str(one["id"]), "A", "--by", "bertil")
        self.assertEqual(answered["thread"][-1]["author"], "bertil")
        self.cli("question", "clarify", str(two["id"]), "Which client?")
        found = self.cli("question", "wait", self.run_id, "--timeout", "1")
        self.assertEqual([item["by"] for item in found], ["bertil", "human"])
        self.assertIn("--by is empty", self.fails("question", "answer", str(two["id"]), "A", "--by", " ", code=2))

    def test_delivered_messages_do_not_come_back(self):
        one = self.cli("question", "ask", self.run_id, QUESTION)
        self.cli("question", "answer", str(one["id"]), "A", "--delivered")
        self.assertEqual(self.cli("question", "wait", self.run_id, "--timeout", "0"), [])

    def test_wait_wakes_up_on_an_answer(self):
        one = self.cli("question", "ask", self.run_id, QUESTION)

        def answer_later():
            time.sleep(0.3)
            conn = home.connect()
            try:
                questions.answer(conn, one["id"], "B: later")
            finally:
                conn.close()

        thread = threading.Thread(target=answer_later)
        thread.start()
        conn = home.connect()
        try:
            started = time.monotonic()
            found = questions.wait(conn, self.run_id, timeout=5, interval=0.05)
        finally:
            conn.close()
        thread.join()
        self.assertEqual(found[0]["text"], "B: later")
        self.assertLess(time.monotonic() - started, 4)


if __name__ == "__main__":
    unittest.main()
