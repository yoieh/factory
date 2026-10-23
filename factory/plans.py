import json
import re

JOB_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
JOB_KEYS = ("id", "kind", "repo", "title", "depends_on", "reads", "template", "pipeline")
GROUP_KEYS = ("step", "gates")
MAX_GROUPS = 6
MAX_GATES = 6


def json_blocks(text):
    return re.findall(r"^```json[ \t]*\n(.*?)^```[ \t]*$", text, re.M | re.S)


def plan_json(text):
    errors = []
    for block in json_blocks(text):
        try:
            data = json.loads(block)
        except json.JSONDecodeError as error:
            errors.append(f"the json block does not parse: {error}")
            continue
        if isinstance(data, dict) and "jobs" in data:
            return data, []
    return None, errors or ["plan.md has no ```json block with \"jobs\""]


NOTHING = re.compile(r"^(none|n/a|nothing|not needed|no setup|no install|-)(?![a-z0-9])", re.I)


def command_value(text):
    stripped = text.strip()
    if not stripped or NOTHING.match(stripped.strip("`").strip()):
        return None
    found = re.search(r"`([^`]+)`", stripped)
    value = (found.group(1) if found else stripped).strip()
    if not value or NOTHING.match(value):
        return None
    return value


def repo_facts(text):
    facts = {}
    inside = False
    current = None
    for line in text.splitlines():
        if re.match(r"^##[ \t]", line):
            inside = line.strip().lower() == "## repo facts"
            current = None
            continue
        if not inside:
            continue
        heading = re.match(r"^###[ \t]+(.+?)[ \t]*$", line)
        if heading:
            current = heading.group(1).strip().strip("`")
            facts[current] = {"test": None, "setup": None}
            continue
        if current is None:
            continue
        fact = re.match(r"^\s*[-*][ \t]+(Test command|Worktree setup)[ \t]*:[ \t]*(.*)$", line, re.I)
        if fact:
            key = "test" if fact.group(1).lower() == "test command" else "setup"
            facts[current][key] = command_value(fact.group(2))
    return facts


def cycle(jobs):
    graph = {job["id"]: [dep for dep in job.get("depends_on", []) if isinstance(dep, str)] for job in jobs}
    state = {}
    order = []

    def visit(node, trail):
        if state.get(node) == "done":
            return None
        if state.get(node) == "active":
            return trail[trail.index(node):] + [node]
        state[node] = "active"
        for dep in graph.get(node, []):
            if dep in graph:
                found = visit(dep, trail + [node])
                if found:
                    return found
        state[node] = "done"
        order.append(node)
        return None

    for node in graph:
        found = visit(node, [])
        if found:
            return found
    return None


def requires_problems(where, step, job, position, context):
    problems = []
    for need in step.get("requires", []):
        if need == "designs" and not context["has_designs"]:
            problems.append(f"{where}: {step['name']} requires designs, and the run's design/ folder is empty")
        elif need == "upstream" and not job.get("depends_on"):
            problems.append(f"{where}: {step['name']} requires upstream, and the job has no depends_on")
        elif need == "previous" and position == 1:
            problems.append(f"{where}: {step['name']} requires previous, so it cannot be the job's first group")
        elif need == "repo-tests":
            facts = context["facts"].get(job.get("repo") or "", {})
            if job.get("kind", "code") != "code" or not facts.get("test"):
                problems.append(
                    f"{where}: {step['name']} requires repo-tests, and plan.md names no test command for repo {job.get('repo')}"
                )
        elif need == "issue" and not context["has_issue"]:
            problems.append(f"{where}: {step['name']} requires issue, and the run has no issue.md")
    return problems


def validate(data, context):
    problems = []
    steps = context["steps"]
    repos = context["repos"]
    jobs = data.get("jobs")
    for key in data:
        if key != "jobs":
            problems.append(f"plan json: unknown key {key!r}")
    if not isinstance(jobs, list) or not jobs:
        return problems + ["plan json: jobs must be a non-empty list"]
    ids = []
    for number, job in enumerate(jobs, start=1):
        if isinstance(job, dict) and isinstance(job.get("id"), str):
            ids.append(job["id"])
    for number, job in enumerate(jobs, start=1):
        if not isinstance(job, dict):
            problems.append(f"job {number}: must be an object")
            continue
        label = f"job {job.get('id', number)}"
        problems.extend(f"{label}: unknown key {key!r}" for key in job if key not in JOB_KEYS)
        job_id = job.get("id")
        if not (isinstance(job_id, str) and JOB_RE.match(job_id)):
            problems.append(f"{label}: id must match ^[a-z0-9][a-z0-9-]{{0,39}}$")
        elif ids.count(job_id) > 1:
            problems.append(f"{label}: duplicate id")
        kind = job.get("kind", "code")
        if kind not in ("code", "files"):
            problems.append(f"{label}: kind must be code or files")
            kind = None
        if kind == "code":
            if not job.get("repo"):
                problems.append(f"{label}: a code job needs repo")
            elif job["repo"] not in repos:
                problems.append(f"{label}: repo {job['repo']!r} is not a repo of the project")
            if "reads" in job:
                problems.append(f"{label}: reads is only for files jobs")
        if kind == "files":
            if "repo" in job:
                problems.append(f"{label}: a files job has no repo")
            reads = job.get("reads", [])
            if not (isinstance(reads, list) and all(isinstance(item, str) for item in reads)):
                problems.append(f"{label}: reads must be a list of repo names")
            else:
                for repo in reads:
                    if repo not in repos:
                        problems.append(f"{label}: reads names {repo!r}, not a repo of the project")
        title = job.get("title")
        if not (isinstance(title, str) and title.strip()):
            problems.append(f"{label}: title is required")
        elif "\n" in title or len(title) > 200:
            problems.append(f"{label}: title must be one line of at most 200 characters")
        depends = job.get("depends_on", [])
        if not (isinstance(depends, list) and all(isinstance(item, str) for item in depends)):
            problems.append(f"{label}: depends_on must be a list of job ids")
        else:
            for dep in depends:
                if dep == job_id:
                    problems.append(f"{label}: depends on itself")
                elif dep not in ids:
                    problems.append(f"{label}: depends_on names an unknown job {dep!r}")
        template = job.get("template")
        if template is not None and template not in context["pipelines"]:
            problems.append(f"{label}: unknown template {template!r}")
        pipeline = job.get("pipeline")
        if not isinstance(pipeline, list) or not pipeline:
            problems.append(f"{label}: pipeline must be a list of 1 to {MAX_GROUPS} groups")
            continue
        if len(pipeline) > MAX_GROUPS:
            problems.append(f"{label}: {len(pipeline)} groups, at most {MAX_GROUPS}")
        for position, group in enumerate(pipeline, start=1):
            where = f"{label} group {position}"
            if not isinstance(group, dict):
                problems.append(f"{where}: must be an object")
                continue
            if "optional_gates" in group:
                problems.append(f"{where}: optional_gates belongs to templates only; list the chosen gates in gates")
            problems.extend(f"{where}: unknown key {key!r}" for key in group if key not in GROUP_KEYS + ("optional_gates",))
            name = group.get("step")
            step = steps.get(name) if isinstance(name, str) else None
            if step is None:
                problems.append(f"{where}: unknown step {name!r}")
            elif step["role"] != "produce":
                problems.append(f"{where}: {name} is a gate; a group's step must be a produce step")
                step = None
            elif kind and kind not in step["kinds"]:
                problems.append(f"{where}: {name} does not fit a {kind} job")
            if step:
                problems.extend(requires_problems(where, step, job, position, context))
            gates = group.get("gates", [])
            if not (isinstance(gates, list) and all(isinstance(item, str) for item in gates)):
                problems.append(f"{where}: gates must be a list of gate steps")
                continue
            if len(gates) > MAX_GATES:
                problems.append(f"{where}: {len(gates)} gates, at most {MAX_GATES}")
            for gate in sorted(set(gate for gate in gates if gates.count(gate) > 1)):
                problems.append(f"{where}: gate {gate} appears twice")
            for gate in dict.fromkeys(gates):
                found = steps.get(gate)
                if found is None:
                    problems.append(f"{where}: unknown gate {gate!r}")
                elif found["role"] != "gate":
                    problems.append(f"{where}: {gate} is a produce step; gates must be gate steps")
                else:
                    if kind and kind not in found["kinds"]:
                        problems.append(f"{where}: gate {gate} does not fit a {kind} job")
                    problems.extend(requires_problems(where, found, job, position, context))
            if step and not gates and not step.get("gates_optional"):
                problems.append(f"{where}: {step['name']} needs at least one gate (it is not gates_optional)")
            if (step and kind == "code" and step.get("code_review") == "required"
                    and context["require_code_review"] and "review-code" not in gates):
                problems.append(f"{where}: a code group of {step['name']} needs review-code (defaults.require_code_review)")
    if all(isinstance(job, dict) and isinstance(job.get("id"), str) for job in jobs):
        found = cycle(jobs)
        if found:
            problems.append(f"depends_on forms a cycle: {' -> '.join(found)}")
    return problems
