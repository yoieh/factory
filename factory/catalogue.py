import json
import os
import re

from . import FactoryError, adapters, home
from .util import read_text

STEP_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
RESERVED = ("rework", "output", "reply", "round")
ROLES = ("produce", "gate")
KINDS = ("code", "files")
REQUIRES = ("designs", "upstream", "previous", "repo-tests", "issue")
GENERIC = {"produce": "factory-worker", "gate": "factory-reviewer"}
GENERIC_AGENTS = (
    "factory-planner", "factory-worker", "factory-reviewer", "factory-retro", "factory-improver", "factory-lander",
)
STEP_KEYS = (
    "name", "role", "kinds", "agent", "model", "summary", "when", "requires",
    "gates_optional", "code_review", "skills_key", "version",
)
TEMPLATE_KEYS = ("name", "description", "kinds", "when", "pipeline")
GROUP_KEYS = ("step", "gates", "optional_gates")
MAX_GROUPS = 6
MAX_GATES = 6


def dirs(config, project=None):
    found = [("built-in", os.path.join(home.core_root(), "catalogue"), "all")]
    found.append(("home", home.path("catalogue"), "all"))
    for entry in config.get("catalogue_dirs", []):
        found.append(("config", entry, "all"))
    if project:
        for entry in project.get("catalogue_dirs", []):
            found.append(("project", entry, "all"))
        for repo in project.get("repos", []):
            found.append((f"repo:{repo['name']}", os.path.join(repo["path"], ".factory"), "steps"))
    return found


def parse_value(raw):
    value = raw.strip()
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [item.strip() for item in inner.split(",")] if inner else []
    if value in ("true", "false"):
        return value == "true"
    if value.isdigit():
        return int(value)
    return value


def front_matter(text):
    lines = text.lstrip("﻿").split("\n")
    if not lines or lines[0].strip() != "---":
        raise FactoryError("no front matter: the file must start with a line ---")
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        raise FactoryError("front matter is not closed with a line ---")
    meta = {}
    for number, line in enumerate(lines[1:end], start=2):
        if not line.strip():
            continue
        key, sep, raw = line.partition(": ")
        if not sep or not re.match(r"^[a-z_]+$", key):
            raise FactoryError(f"front matter line {number}: expected 'key: value'")
        if key in meta:
            raise FactoryError(f"front matter line {number}: duplicate key {key!r}")
        if not raw.strip():
            raise FactoryError(f"front matter line {number}: {key} has no value")
        meta[key] = parse_value(raw)
    return meta, "\n".join(lines[end + 1:])


def ask_section(body):
    lines = body.split("\n")
    asks = [index for index, line in enumerate(lines) if line.rstrip() == "## Ask"]
    if len(asks) != 1:
        return None, "a human gate needs exactly one '## Ask' section"
    later = [line for line in lines[asks[0] + 1:] if re.match(r"^##[ \t]", line)]
    if later:
        return None, "'## Ask' must be the last section"
    text = "\n".join(lines[asks[0] + 1:]).strip()
    if not text:
        return None, "'## Ask' is empty"
    return text, None


def fill_ask(text, job_title, output):
    return text.replace("{job_title}", job_title).replace("{output}", output)


def step_problems(meta, body, file_name):
    from . import questions

    problems = [f"unknown key {key!r}" for key in meta if key not in STEP_KEYS]
    name = meta.get("name")
    if not (isinstance(name, str) and STEP_RE.match(name)):
        problems.append("name must match ^[a-z][a-z0-9-]{1,39}$")
    elif name != file_name:
        problems.append(f"name {name!r} must equal the file name {file_name!r}")
    elif name in RESERVED:
        problems.append(f"name {name!r} is reserved")
    role = meta.get("role")
    if role not in ROLES:
        problems.append("role must be produce or gate")
    kinds = meta.get("kinds")
    if not (isinstance(kinds, list) and kinds and all(kind in KINDS for kind in kinds)):
        problems.append("kinds must be a list of code and files")
    for key in ("summary", "when"):
        if not (isinstance(meta.get(key), str) and meta[key].strip()):
            problems.append(f"{key} is required")
    agent = meta.get("agent")
    if agent is not None and not (isinstance(agent, str) and re.match(r"^[a-z0-9][a-z0-9_-]*$", agent)):
        problems.append("agent must be an agent name or human")
    if agent == "human" and role != "gate":
        problems.append("agent human is only for gates")
    if "model" in meta and not isinstance(meta["model"], str):
        problems.append("model must be a model alias or id")
    requires = meta.get("requires", [])
    if not (isinstance(requires, list) and all(item in REQUIRES for item in requires)):
        problems.append(f"requires must be a list of {', '.join(REQUIRES)}")
    if "gates_optional" in meta:
        if role != "produce":
            problems.append("gates_optional is only for produce steps")
        elif not isinstance(meta["gates_optional"], bool):
            problems.append("gates_optional must be true or false")
    if "code_review" in meta:
        if role != "produce":
            problems.append("code_review is only for produce steps")
        elif meta["code_review"] not in ("required", "optional"):
            problems.append("code_review must be required or optional")
    skills = meta.get("skills_key", [])
    if not (isinstance(skills, list) and all(isinstance(item, str) and item for item in skills)):
        problems.append("skills_key must be a list of names")
    if "version" in meta and not (isinstance(meta["version"], int) and not isinstance(meta["version"], bool)):
        problems.append("version must be an integer")
    if agent == "human":
        text, problem = ask_section(body)
        if problem:
            problems.append(problem)
        else:
            sample = fill_ask(text, "the job", "/output.md")
            for issue in questions.format_problems(sample):
                problems.append(f"'## Ask' breaks the question format: {issue}")
    return problems


def step_entry(meta, path, label, folder):
    role = meta["role"]
    entry = {
        "name": meta["name"],
        "role": role,
        "kinds": meta["kinds"],
        "agent": meta.get("agent") or GENERIC[role],
        "model": meta.get("model"),
        "summary": meta["summary"],
        "when": meta["when"],
        "requires": meta.get("requires", []),
        "skills_key": meta.get("skills_key", []),
        "version": meta.get("version", 1),
    }
    if role == "produce":
        entry["gates_optional"] = meta.get("gates_optional", False)
        entry["code_review"] = meta.get("code_review", "required")
    entry.update(path=path, source=folder, level=label)
    return entry


def load_step(path, label, folder):
    file_name = os.path.basename(path)[:-3]
    try:
        meta, body = front_matter(read_text(path))
    except (FactoryError, UnicodeDecodeError, OSError) as error:
        return None, [str(getattr(error, "message", error))]
    problems = step_problems(meta, body, file_name)
    if problems:
        return None, problems
    return step_entry(meta, path, label, folder), []


def template_problems(data, file_name):
    if not isinstance(data, dict):
        return ["a template must be a JSON object"]
    problems = [f"unknown key {key!r}" for key in data if key not in TEMPLATE_KEYS]
    if data.get("name") != file_name:
        problems.append(f"name must equal the file name {file_name!r}")
    for key in ("description", "when"):
        if not (isinstance(data.get(key), str) and data[key].strip()):
            problems.append(f"{key} is required")
    kinds = data.get("kinds")
    if not (isinstance(kinds, list) and kinds and all(kind in KINDS for kind in kinds)):
        problems.append("kinds must be a list of code and files")
    pipeline = data.get("pipeline")
    if not (isinstance(pipeline, list) and 1 <= len(pipeline) <= MAX_GROUPS):
        problems.append(f"pipeline must be a list of 1 to {MAX_GROUPS} groups")
        return problems
    for number, group in enumerate(pipeline, start=1):
        if not isinstance(group, dict):
            problems.append(f"group {number} must be an object")
            continue
        problems.extend(f"group {number}: unknown key {key!r}" for key in group if key not in GROUP_KEYS)
        if not isinstance(group.get("step"), str):
            problems.append(f"group {number}: step is required")
        for key in ("gates", "optional_gates"):
            value = group.get(key, [])
            if not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
                problems.append(f"group {number}: {key} must be a list of step names")
    return problems


def load_template(path, label, folder):
    file_name = os.path.basename(path)[:-5]
    try:
        data = json.loads(read_text(path))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as error:
        return None, [f"invalid JSON: {error}"]
    problems = template_problems(data, file_name)
    if problems:
        return None, problems
    return dict(data, path=path, source=folder, level=label), []


def listing(folder, suffix):
    if not os.path.isdir(folder):
        return []
    return sorted(name for name in os.listdir(folder) if name.endswith(suffix) and not name.startswith("."))


def build(config, project=None):
    steps = {}
    pipelines = {}
    adapter_dirs = {}
    agent_files = {}
    problems = []
    for label, folder, scope in dirs(config, project):
        for name in listing(os.path.join(folder, "steps"), ".md"):
            path = os.path.join(folder, "steps", name)
            entry, found = load_step(path, label, folder)
            problems.extend({"path": path, "problem": problem} for problem in found)
            if entry:
                steps[entry["name"]] = entry
        for name in listing(os.path.join(folder, "pipelines"), ".json"):
            path = os.path.join(folder, "pipelines", name)
            entry, found = load_template(path, label, folder)
            problems.extend({"path": path, "problem": problem} for problem in found)
            if entry:
                pipelines[entry["name"]] = entry
        if scope != "all":
            continue
        adapters_folder = os.path.join(folder, "adapters")
        if os.path.isdir(adapters_folder):
            for name in sorted(os.listdir(adapters_folder)):
                if os.path.isfile(os.path.join(adapters_folder, name, "adapter.json")):
                    adapter_dirs[name] = {"dir": os.path.join(adapters_folder, name), "level": label}
        for name in listing(os.path.join(folder, "agents"), ".md"):
            agent_files[name[:-3]] = os.path.join(folder, "agents", name)
    return {"steps": steps, "pipelines": pipelines, "adapters": adapter_dirs, "agents": agent_files, "problems": problems}


def template_reference_problems(template, steps):
    problems = []
    kinds = set(template["kinds"])
    for number, group in enumerate(template["pipeline"], start=1):
        step = steps.get(group["step"])
        if step is None:
            problems.append(f"group {number}: unknown step {group['step']!r}")
        elif step["role"] != "produce":
            problems.append(f"group {number}: {group['step']} is a gate, not a produce step")
        elif not kinds & set(step["kinds"]):
            problems.append(f"group {number}: {group['step']} fits none of the template's kinds")
        for key in ("gates", "optional_gates"):
            for gate in group.get(key, []):
                found = steps.get(gate)
                if found is None:
                    problems.append(f"group {number}: unknown gate {gate!r}")
                elif found["role"] != "gate":
                    problems.append(f"group {number}: {gate} in {key} is not a gate step")
                elif not kinds & set(found["kinds"]):
                    problems.append(f"group {number}: {gate} fits none of the template's kinds")
    return problems


def installed_agent_dirs(project=None):
    found = [os.path.join(os.path.expanduser("~"), ".claude", "agents")]
    if home.plugin_root():
        found.append(os.path.join(home.plugin_root(), "agents"))
    if project:
        for repo in project.get("repos", []):
            found.append(os.path.join(repo["path"], ".claude", "agents"))
    return found


def agent_installed(name, project=None):
    return any(os.path.isfile(os.path.join(folder, f"{name}.md")) for folder in installed_agent_dirs(project))


def dedicated_agents(steps):
    names = set()
    for step in steps.values():
        if step["agent"] not in GENERIC_AGENTS and step["agent"] != "human":
            names.add(step["agent"])
    return sorted(names)


def show(config, project=None):
    built = build(config, project)
    return {
        "steps": built["steps"],
        "pipelines": built["pipelines"],
        "dirs": [{"level": label, "path": folder} for label, folder, _ in dirs(config, project)],
        "problems": built["problems"],
    }


def check(config, project=None):
    built = build(config, project)
    problems = list(built["problems"])
    for template in built["pipelines"].values():
        problems.extend({"path": template["path"], "problem": problem}
                        for problem in template_reference_problems(template, built["steps"]))
    for name in dedicated_agents(built["steps"]):
        if not agent_installed(name, project):
            users = [step["path"] for step in built["steps"].values() if step["agent"] == name]
            for path in users:
                problems.append({"path": path, "problem": f"agent {name} is not installed (factory skill install)"})
    for name, found in built["adapters"].items():
        path = os.path.join(found["dir"], "adapter.json")
        try:
            data, adapter_problems = adapters.load(found["dir"])
        except FactoryError as error:
            problems.append({"path": path, "problem": error.message})
            continue
        problems.extend({"path": path, "problem": problem} for problem in adapter_problems)
        for file_path, count in adapters.unfilled(found["dir"]):
            problems.append({"path": file_path,
                             "problem": f"{count} placeholder{'s' if count != 1 else ''} still to fill in (<fill in: ...>)"})
    return {
        "ok": not problems,
        "steps": len(built["steps"]),
        "pipelines": len(built["pipelines"]),
        "adapters": len(built["adapters"]),
        "problems": problems,
    }
