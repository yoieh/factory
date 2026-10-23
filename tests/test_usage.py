import json
import os
import unittest

from helpers import FactoryCase, code_job, write

from factory import usage

SESSION = "session-orchestrator-1"


def user(text):
    return {"type": "user", "timestamp": "2000-01-01T00:00:00.000Z", "message": {"role": "user", "content": text}}


def assistant(message_id, model="claude-opus-5-5", stamp="2000-01-01T00:00:01.000Z", **tokens):
    counts = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    counts.update(tokens)
    return {"type": "assistant", "timestamp": stamp,
            "message": {"id": message_id, "model": model, "role": "assistant", "content": [{"type": "text", "text": "x"}],
                        "usage": counts}}


def prompt(**keys):
    return "\n".join(f"{key}: {value}" for key, value in keys.items())


class UsageTest(FactoryCase):
    def setUp(self):
        super().setUp()
        self.run = self.started_run([code_job("lead")], session=SESSION)
        self.folder = os.path.join(self.user, ".claude", "projects", "-home-user-repo")
        started = self.cli("job", "start", f"{self.run['id']}/lead")
        self.group = started["group"]
        self.job_dir = started["job_dir"]

    def transcript(self, name, entries, raw_tail=""):
        path = os.path.join(self.folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for entry in entries:
                handle.write((entry if isinstance(entry, str) else json.dumps(entry)) + "\n")
            handle.write(raw_tail)
        return path

    def subagent(self, name, entries):
        return self.transcript(os.path.join(SESSION, "subagents", f"agent-{name}.jsonl"), entries)

    def rows(self):
        return {(row["job_id"], row["node"], row["round"], row["model"]): row
                for row in self.cli("usage", "show", self.run["id"])["rows"]}

    def test_sync_counts_each_message_once_per_node_and_round(self):
        self.transcript(f"{SESSION}.jsonl", [
            user("/factory api Build the thing"),
            assistant("m1", input_tokens=10, output_tokens=5),
            assistant("m1", input_tokens=10, output_tokens=40),
            "this line is not json",
            assistant("m2", output_tokens=7, cache_read_input_tokens=1000,
                      cache_creation={"ephemeral_5m_input_tokens": 100, "ephemeral_1h_input_tokens": 50}),
            assistant("m3", model="<synthetic>", output_tokens=9),
        ])
        self.subagent("planner", [
            user(prompt(node="planner", feature="Build the thing", issue="none", designs="none", repos="api",
                        run_dir=self.run["run_dir"], catalogue="x", project="y", factory_bin="/bin/factory")),
            assistant("p1", input_tokens=200, output_tokens=20),
        ])
        self.subagent("producer", [
            {"type": "user", "timestamp": "2000-01-01T00:00:00.000Z", "message": {"role": "user", "content": [
                {"type": "text", "text": prompt(node="produce:build", job_dir=self.job_dir, group_dir=self.group["group_dir"],
                                                round="1", run_dir=self.run["run_dir"])}]}},
            assistant("b1", input_tokens=50, output_tokens=10),
        ])
        self.subagent("gate", [
            user(prompt(node="gate:review-code", job_dir=self.job_dir, round="1", run_dir=self.run["run_dir"])),
            assistant("g1", model="claude-sonnet-5-5", input_tokens=30, output_tokens=3),
        ])
        self.subagent("improver", [
            user(prompt(node="improver", items="/x", run_dir="none")),
            assistant("i1", input_tokens=999, output_tokens=999),
        ])
        result = self.cli("usage", "sync", self.run["id"])
        self.assertEqual((result["sessions"], result["files"], result["messages"]), (1, 5, 5))
        rows = self.rows()
        orchestrator = rows[("", "orchestrator", 0, "claude-opus-5-5")]
        self.assertEqual((orchestrator["input"], orchestrator["output"], orchestrator["cache_read"],
                          orchestrator["cache_write_5m"], orchestrator["cache_write_1h"]), (10, 47, 1000, 100, 50))
        self.assertEqual(rows[("", "planner", 0, "claude-opus-5-5")]["input"], 200)
        self.assertEqual(rows[("lead", "produce:build", 1, "claude-opus-5-5")]["output"], 10)
        self.assertEqual(rows[("lead", "gate:review-code", 1, "claude-sonnet-5-5")]["input"], 30)
        self.assertNotIn(999, [row["input"] for row in rows.values()])
        self.assertEqual(self.cli("usage", "sync", self.run["id"])["messages"], 0)

    def test_offsets_boundary_and_partial_lines(self):
        path = self.transcript(f"{SESSION}.jsonl", [user("start"), assistant("m1", output_tokens=5)],
                               raw_tail=json.dumps(assistant("m2", output_tokens=8))[:40])
        self.cli("usage", "sync")
        self.assertEqual(self.rows()[("", "orchestrator", 0, "claude-opus-5-5")]["output"], 5)
        with open(path, "r+", encoding="utf-8") as handle:
            text = handle.read()
            handle.seek(0)
            handle.truncate()
            handle.write(text[:text.rfind("\n") + 1])
        self.transcript(f"{SESSION}.jsonl", [assistant("m1", output_tokens=50), assistant("m2", output_tokens=8)])
        self.cli("usage", "sync")
        self.assertEqual(self.rows()[("", "orchestrator", 0, "claude-opus-5-5")]["output"], 13)

    def test_a_resumed_producer_is_split_at_round_events(self):
        self.output(self.group, "STATUS: BLOCKED tests do not start\n")
        self.cli("step", "sync", f"{self.run['id']}/lead")
        self.cli("job", "round", f"{self.run['id']}/lead")
        self.subagent("producer", [
            user(prompt(node="produce:build", job_dir=self.job_dir, group_dir=self.group["group_dir"], round="1",
                        run_dir=self.run["run_dir"])),
            assistant("b1", stamp="2000-01-01T00:00:01.000Z", output_tokens=10),
            assistant("b2", stamp="2999-01-01T00:00:01.000Z", output_tokens=20),
        ])
        self.cli("usage", "sync", self.run["id"])
        rows = self.rows()
        self.assertEqual(rows[("lead", "produce:build", 1, "claude-opus-5-5")]["output"], 10)
        self.assertEqual(rows[("lead", "produce:build", 2, "claude-opus-5-5")]["output"], 20)

    def test_show_costs_and_unknown_models(self):
        self.transcript(f"{SESSION}.jsonl", [
            user("start"),
            assistant("m1", input_tokens=1_000_000, output_tokens=1_000_000, cache_read_input_tokens=1_000_000,
                      cache_creation={"ephemeral_5m_input_tokens": 1_000_000, "ephemeral_1h_input_tokens": 1_000_000}),
            assistant("m2", model="claude-made-up-1", input_tokens=10),
        ])
        self.cli("usage", "sync", self.run["id"])
        shown = self.cli("usage", "show", self.run["id"])
        self.assertEqual(shown["prices"], {"date": usage.PRICES_DATE, "source": "built-in"})
        known = next(row for row in shown["rows"] if row["model"] == "claude-opus-5-5")
        self.assertAlmostEqual(known["cost"], 4 + 20 + 0.2 + 4 * 1.25 + 4 * 2)
        unknown = next(row for row in shown["rows"] if row["model"] == "claude-made-up-1")
        self.assertIsNone(unknown["cost"])
        total = shown["total"]
        self.assertEqual((total["cost_complete"], total["unknown_models"]), (False, ["claude-made-up-1"]))
        self.assertAlmostEqual(total["cost"], 37.2)
        self.assertEqual(total["tokens"], 5_000_010)
        self.assertEqual(set(shown["by_node"]), {"orchestrator"})
        listed = self.cli("run", "list")[0]["cost"]
        self.assertEqual((listed["tokens"], listed["cost_complete"], listed["prices_date"]), (5_000_010, False, usage.PRICES_DATE))

    def test_prices_json_overrides_the_table(self):
        self.transcript(f"{SESSION}.jsonl", [user("start"), assistant("m1", model="claude-made-up-1", output_tokens=1_000_000)])
        self.cli("usage", "sync", self.run["id"])
        write(os.path.join(self.home, "prices.json"), json.dumps(
            {"date": "2026-11-01", "models": {"claude-made-up": {"input": 1, "output": 2, "cache_read": 0.1}}}))
        shown = self.cli("usage", "show", self.run["id"])
        self.assertEqual(shown["prices"]["date"], "2026-11-01")
        self.assertEqual(shown["total"]["cost"], 2.0)
        write(os.path.join(self.home, "prices.json"), json.dumps({"models": {}}))
        self.assertIn("date must be YYYY-MM-DD", self.fails("usage", "show", self.run["id"]))

    def test_model_ids_match_by_prefix(self):
        table = {"models": {"claude-opus-5": {"input": 5, "output": 25, "cache_read": 0.5},
                            "claude-opus-5-5": {"input": 4, "output": 20, "cache_read": 0.2}}}
        self.assertEqual(usage.prices_for(table, "claude-opus-5-5")["input"], 4)
        self.assertEqual(usage.prices_for(table, "claude-opus-5-20260101")["input"], 5)
        self.assertEqual(usage.prices_for(table, "claude-opus-5-5[1m]")["input"], 4)
        self.assertIsNone(usage.prices_for(table, "claude-opus-55"))

    def test_claude_config_dir_is_honoured(self):
        os.environ["CLAUDE_CONFIG_DIR"] = os.path.join(self.tmp, "claude-config")
        self.folder = os.path.join(self.tmp, "claude-config", "projects", "repo")
        self.transcript(f"{SESSION}.jsonl", [user("start"), assistant("m1", output_tokens=4)])
        self.assertEqual(self.cli("usage", "sync", self.run["id"])["files"], 1)

    def test_nothing_to_read(self):
        self.assertEqual(self.cli("usage", "sync", self.run["id"])["files"], 0)
        shown = self.cli("usage", "show", self.run["id"])
        self.assertEqual((shown["rows"], shown["total"]["tokens"]), ([], 0))
        self.assertIsNone(self.cli("run", "list")[0]["cost"])


if __name__ == "__main__":
    unittest.main()
