import glob
import os
import re
import shlex
import unittest

from helpers import ROOT, QUESTION, FactoryCase, code_job, files_job, read, write
from test_retro import item, retro_text

from factory import cli

SKILL = os.path.join(ROOT, "skills", "factory", "SKILL.md")
AGENTS = os.path.join(ROOT, "agents")
SPEC = os.path.join(ROOT, "factory-spec")
AGENT_NODES = {
    "planner": "factory-planner",
    "producer": "factory-worker",
    "gate": "factory-reviewer",
    "retro": "factory-retro",
    "improver": "factory-improver",
}


def placeholder(match):
    name = match.group(1).lower()
    if name in ("n", "id", "question-id") or name.startswith("ids"):
        return "1"
    if name == "ref":
        return "run/job"
    return "word"


def cli_calls(path, marker):
    found = []
    inside = False
    for number, line in enumerate(read(path).splitlines(), start=1):
        if line.strip().startswith("```"):
            inside = not inside
            continue
        pieces = [line[line.index(marker):]] if inside and marker in line else [
            span[span.index(marker):] for span in re.findall(r"`([^`]+)`", line) if marker in span and not inside]
        for piece in pieces:
            if "$(" in piece:
                continue
            body = re.sub(r"<([^<>]+)>", placeholder, piece[len(marker):].strip())
            words = []
            for word in shlex.split(body):
                if word in ("|", "||", "&&", ";"):
                    break
                if word.startswith(("<", ">", "2>")):
                    continue
                words.append(word.split("|")[0] if "|" in word and not word.startswith("-") else word)
            words = [word for word in words if word != "..." and not word.startswith("[")]
            if words:
                found.append((number, words))
    return found


def backticked(text):
    return re.findall(r"`([a-z_]+)`", text)


def sentence(text, start):
    index = text.index(start)
    end = text.find("\n", index)
    return text[index:end if end >= 0 else None]


def agent_inputs(name):
    text = read(os.path.join(AGENTS, f"{name}.md"))
    section = text.split("## Inputs (in your prompt)", 1)[1].split("\n## ", 1)[0]
    return re.findall(r"^- `([a-z_]+)`:", section, re.M)


def skill_prompt_keys():
    text = read(SKILL)
    keys = {}
    for node in AGENT_NODES:
        line = re.search(rf"^- {node}: (.+)$", text, re.M).group(1)
        keys[node] = backticked(line)
    return keys


class CliCallsTest(unittest.TestCase):
    def sources(self):
        found = [(SKILL, '"$F"')]
        found += [(path, '"<factory_bin>"') for path in sorted(glob.glob(os.path.join(AGENTS, "*.md")))]
        found += [(path, '"$F"') for path in sorted(glob.glob(os.path.join(ROOT, "catalogue", "adapters", "*", "adapter.md")))]
        return found

    def test_every_call_parses(self):
        failures, count = [], 0
        for path, marker in self.sources():
            for number, words in cli_calls(path, marker):
                count += 1
                try:
                    cli.build().parse_args(words)
                except cli.ArgumentError as error:
                    failures.append(f"{os.path.relpath(path, ROOT)}:{number}: factory {' '.join(words)}: {error}")
                except SystemExit:
                    pass
        self.assertGreater(count, 80)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_every_command_group_the_skill_uses_exists(self):
        commands = {words[0] for _, words in cli_calls(SKILL, '"$F"')}
        for name in ("agents", "project", "run", "plan", "job", "step", "gate", "question", "skip", "design",
                     "adapter", "retro", "improvement", "usage", "issue", "paths", "doctor", "event"):
            self.assertIn(name, commands)


class PromptKeysTest(unittest.TestCase):
    def test_skill_and_agents_list_the_same_keys(self):
        keys = skill_prompt_keys()
        for node, agent in AGENT_NODES.items():
            self.assertEqual(agent_inputs(agent), keys[node], agent)

    @unittest.skipUnless(os.path.isdir(SPEC), "factory-spec/ is not here")
    def test_spec_table_matches(self):
        table = read(os.path.join(SPEC, "spec", "05-file-contracts.md"))
        keys = skill_prompt_keys()
        for node, agent in AGENT_NODES.items():
            row = re.search(rf"^\| `{agent}`[^|]*\| (.+) \|$", table, re.M).group(1)
            self.assertEqual(backticked(row), keys[node], agent)

    def test_required_and_usage_keys(self):
        keys = skill_prompt_keys()
        for node, found in keys.items():
            for key in ("node", "run_dir", "project", "factory_bin"):
                self.assertIn(key, found, node)
            self.assertEqual(found[0], "node")
        for key in ("job_dir", "group_dir", "round"):
            self.assertIn(key, keys["producer"])
            self.assertIn(key, keys["gate"])


class SkillAndAgentCopiesTest(unittest.TestCase):
    @unittest.skipUnless(os.path.isdir(SPEC), "factory-spec/ is not here")
    def test_shipped_prompts_match_the_spec(self):
        self.assertEqual(read(SKILL), read(os.path.join(SPEC, "prompts", "skill", "factory", "SKILL.md")))
        spec_agents = sorted(os.listdir(os.path.join(SPEC, "prompts", "agents")))
        self.assertEqual(sorted(os.listdir(AGENTS)), spec_agents)
        for name in spec_agents:
            self.assertEqual(read(os.path.join(AGENTS, name)), read(os.path.join(SPEC, "prompts", "agents", name)), name)

    def test_agent_names_match_their_files(self):
        for path in glob.glob(os.path.join(AGENTS, "*.md")):
            self.assertIn(f"\nname: {os.path.basename(path)[:-3]}\n", read(path))


class ResultFieldsTest(FactoryCase):
    def test_fields_the_skill_reads(self):
        text = read(SKILL)
        run = self.started_run([code_job("lead", steps=[{"step": "build", "gates": ["review-code", "approve"]}]),
                                files_job("notes")], session="session-0001")
        new_keys = backticked(re.search(r"Keep the JSON: (.*?)\.(?: |$)", text, re.M).group(1))
        self.assertIn("confirm_plan", new_keys)
        fresh = self.cli("run", "new", "Another", "--project", "shop")
        for key in new_keys:
            self.assertIn(key, fresh, f"run new: {key}")
        started = self.cli("job", "start", f"{run['id']}/lead")
        line = sentence(text, "`job start` returns")
        top, group = line.split("and `group`", 1)
        for key in backticked(top.split("returns", 1)[1]):
            self.assertIn(key, started, f"job start: {key}")
        for key in backticked(group.split(")", 1)[0]):
            self.assertIn(key, started["group"], f"job start group: {key}")
        self.job_worktree(started)
        self.output(started["group"])
        synced = self.cli("step", "sync", f"{run['id']}/lead")
        for key in backticked(sentence(text, "the result's `gates` lists per gate").split("per gate", 1)[1].split(".", 1)[0]):
            self.assertIn(key, synced["gates"][0], f"step sync gates: {key}")
        self.verdict(started["group"]["round_dir"], "review-code")
        gated = self.cli("gate", "sync", f"{run['id']}/lead")
        for key in ("complete", "outcome", "questions", "question_errors", "human"):
            self.assertIn(key, gated)
        self.assertIn("question", gated["human"][0])
        paths = self.cli("paths", run["id"])
        for key in backticked(sentence(text, "gives every run path again").split("again:", 1)[1]):
            self.assertIn(key, paths, f"paths: {key}")
        self.assertIn("feature_worktrees", paths)
        agents = self.cli("agents")
        for key in ("scope", "roles", "dedicated", "missing"):
            self.assertIn(key, agents)
        self.assertEqual(sorted(agents["roles"]), sorted(["planner", "worker", "reviewer", "retro", "improver"]))
        asked = self.cli("question", "ask", run["id"], QUESTION, "--job", "lead")
        self.cli("question", "clarify", str(asked["id"]), "Why?")
        waited = self.cli("question", "wait", run["id"], "--timeout", "0")
        for key in ("id", "job_id", "asked_by", "status", "kind", "text", "reply_file"):
            self.assertIn(key, waited[0])
        shown = self.cli("job", "show", f"{run['id']}/lead")
        for key in ("upstream", "previous", "groups", "stage", "conflict"):
            self.assertIn(key, shown)
        self.assertIn("producer", shown["group"])
        usage = self.cli("usage", "show", run["id"])
        self.assertIn("total", usage)

    def test_improvement_start_fields(self):
        text = read(SKILL)
        api = self.repo("api")
        self.project(repos=[api])
        run = self.cli("run", "new", "Change", "--project", "shop")
        write(os.path.join(run["run_dir"], "retro.md"), retro_text([item("a", "repo", repo="api")]))
        loaded = self.cli("retro", "load", run["id"])
        self.cli("improvement", "decide", str(loaded["new"][0]), "accepted")
        started = self.cli("improvement", "start")
        line = sentence(text, "Per group it writes `items.md` and `project.json` and returns")
        listed = backticked(re.sub(r"\([^)]*\)", "", line.split("returns", 1)[1].split(": the resolved", 1)[0]))
        self.assertIn("project_file", listed)
        for key in listed:
            self.assertIn(key, started["groups"][0], f"improvement start: {key}")
        for key in ("process", "skipped"):
            self.assertIn(key, started)


if __name__ == "__main__":
    unittest.main()
