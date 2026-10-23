import json
import os

from . import FactoryError, catalogue, db, heads, plans, questions, runs
from .util import append_line, git_commit, read_text, write_text

STAGES = ("queued", "running", "merging", "delivering", "done", "dropped")


def parse_ref(ref):
    run_id, sep, job_id = (ref or "").partition("/")
    if not sep or not run_id or not job_id or "/" in job_id:
        raise FactoryError(f"bad ref {ref!r}: use <run-id>/<job-id>", code=2)
    return run_id, job_id


def job_dir(run_id, job_id):
    return os.path.join(runs.run_dir(run_id), "jobs", job_id)


def group_dir(run_id, job_id, position, step):
    return os.path.join(job_dir(run_id, job_id), "groups", f"{position:02d}-{step}")


def round_dir(folder, number):
    return os.path.join(folder, f"round-{number}")


def decode(job):
    job = dict(job)
    job["depends_on"] = json.loads(job["depends_on"])
    job["reads"] = json.loads(job["reads"])
    job["conflict"] = bool(job["conflict"])
    return job


def job_rows(conn, run_id):
    return [decode(job) for job in db.rows(conn.execute("SELECT * FROM jobs WHERE run_id = ? ORDER BY position", (run_id,)))]


def job_row(conn, run_id, job_id):
    found = db.one(conn.execute("SELECT * FROM jobs WHERE run_id = ? AND id = ?", (run_id, job_id)))
    if not found:
        runs.get(conn, run_id)
        raise FactoryError(f"run {run_id} has no job {job_id}")
    return decode(found)


def group_rows(conn, run_id, job_id):
    found = db.rows(conn.execute(
        "SELECT * FROM job_groups WHERE run_id = ? AND job_id = ? ORDER BY position", (run_id, job_id)
    ))
    for group in found:
        group["gates"] = json.loads(group["gates"])
    return found


def current_group(conn, run_id, job_id):
    job = db.one(conn.execute("SELECT group_pos FROM jobs WHERE run_id = ? AND id = ?", (run_id, job_id)))
    if not job or not job["group_pos"]:
        return None
    for group in group_rows(conn, run_id, job_id):
        if group["position"] == job["group_pos"]:
            return group
    return None


def steps_of(run_id):
    return runs.snapshot(run_id, "catalogue.json").get("steps", {})


def is_human(steps, name):
    return (steps.get(name) or {}).get("agent") == "human"


def repo_of(conn, run_id, name):
    for repo in runs.run_repos(conn, run_id):
        if repo["repo"] == name:
            return repo
    return None


def plan_text(run_id):
    path = runs.run_paths(run_id)["plan"]
    return read_text(path) if os.path.isfile(path) else ""


def comma(values):
    return ",".join(values) if values else "none"


def group_view(run_id, job, group, steps):
    step = steps.get(group["step"], {})
    folder = group_dir(run_id, job["id"], group["position"], group["step"])
    return {
        "position": group["position"],
        "step": group["step"],
        "step_file": step.get("path"),
        "group_dir": folder,
        "round_dir": round_dir(folder, max(group["round"], 1)),
        "agent": step.get("agent"),
        "model": step.get("model"),
        "round": group["round"],
        "max_round": group["max_round"],
        "phase": group["phase"],
        "gates": group["gates"],
        "base_sha": group["base_sha"],
        "producer": group["agent"],
    }


def job_view(conn, run_id, job_id):
    run = runs.get(conn, run_id)
    job = job_row(conn, run_id, job_id)
    steps = steps_of(run_id)
    folder = job_dir(run_id, job_id)
    view = {
        "ref": f"{run_id}/{job_id}",
        "job_dir": folder,
        "kind": job["kind"],
        "title": job["title"],
        "repo": None,
        "repo_path": None,
        "branch": None,
        "worktree": None,
        "feature_branch": None,
        "feature_worktree": None,
        "out_dir": None,
        "sources": "none",
        "setup_hint": None,
    }
    if job["kind"] == "code":
        repo = repo_of(conn, run_id, job["repo"])
        view.update(
            repo=job["repo"],
            repo_path=repo["path"] if repo else None,
            branch=job["branch"],
            worktree=job["worktree"],
            feature_branch=run["feature_branch"],
            feature_worktree=repo["feature_worktree"] if repo else None,
            setup_hint=plans.repo_facts(plan_text(run_id)).get(job["repo"], {}).get("setup"),
        )
    else:
        view["out_dir"] = os.path.join(folder, "out")
        sources = []
        for name in job["reads"]:
            repo = repo_of(conn, run_id, name)
            if repo:
                sources.append(f"{name}={repo['feature_worktree']}")
        view["sources"] = comma(sources)
    group = current_group(conn, run_id, job_id)
    view["group"] = group_view(run_id, job, group, steps) if group else None
    return view


def verdict_rows(conn, run_id, job_id):
    return db.rows(conn.execute(
        "SELECT group_pos, round, gate, verdict, path FROM verdicts WHERE run_id = ? AND job_id = ? "
        "ORDER BY group_pos, round, gate", (run_id, job_id),
    ))


def show(conn, ref):
    run_id, job_id = parse_ref(ref)
    view = job_view(conn, run_id, job_id)
    job = job_row(conn, run_id, job_id)
    steps = steps_of(run_id)
    groups = group_rows(conn, run_id, job_id)
    view["stage"] = job["stage"]
    view["conflict"] = job["conflict"]
    view["depends_on"] = job["depends_on"]
    view["groups"] = [group_view(run_id, job, group, steps) for group in groups]
    view["verdicts"] = verdict_rows(conn, run_id, job_id)
    view["upstream"] = comma([job_dir(run_id, dep) for dep in job["depends_on"]])
    position = job["group_pos"] or 1
    view["previous"] = comma([
        group_dir(run_id, job_id, group["position"], group["step"]) for group in groups if group["position"] < position
    ])
    view["questions"] = [questions.with_thread(conn, question) for question in db.rows(conn.execute(
        "SELECT * FROM questions WHERE run_id = ? AND job_id = ? ORDER BY id", (run_id, job_id)
    ))]
    return view


def summary(conn, run_id, job_id):
    job = job_row(conn, run_id, job_id)
    verdicts = verdict_rows(conn, run_id, job_id)
    groups = []
    for group in group_rows(conn, run_id, job_id):
        groups.append({
            "position": group["position"],
            "step": group["step"],
            "gates": group["gates"],
            "phase": group["phase"],
            "round": group["round"],
            "max_round": group["max_round"],
            "base_sha": group["base_sha"],
            "producer": group["agent"],
            "verdicts": [entry for entry in verdicts if entry["group_pos"] == group["position"]],
        })
    return {
        "id": job["id"],
        "kind": job["kind"],
        "repo": job["repo"],
        "title": job["title"],
        "stage": job["stage"],
        "conflict": job["conflict"],
        "depends_on": job["depends_on"],
        "reads": job["reads"],
        "template": job["template"],
        "group_pos": job["group_pos"],
        "branch": job["branch"],
        "worktree": job["worktree"],
        "groups": groups,
    }


def set_stage(conn, run_id, job_id, old, new, **extra):
    conn.execute("UPDATE jobs SET stage = ? WHERE run_id = ? AND id = ?", (new, run_id, job_id))
    db.event(conn, "job.stage", run_id, job_id, **{"from": old, "to": new}, **extra)


def set_phase(conn, run_id, job_id, group, new, **fields):
    assignments = ", ".join(["phase = ?"] + [f"{key} = ?" for key in fields])
    conn.execute(
        f"UPDATE job_groups SET {assignments} WHERE run_id = ? AND job_id = ? AND position = ?",
        (new, *fields.values(), run_id, job_id, group["position"]),
    )
    if group["phase"] != new:
        db.event(conn, "group.phase", run_id, job_id, group=group["position"], step=group["step"],
                 **{"from": group["phase"], "to": new})


def start_round(conn, run_id, job_id, group, number):
    folder = group_dir(run_id, job_id, group["position"], group["step"])
    os.makedirs(round_dir(folder, number), exist_ok=True)
    db.event(conn, "job.round", run_id, job_id, group=group["position"], step=group["step"], round=number)


def require_running_run(run):
    if run["status"] != "running":
        raise FactoryError(f"run {run['id']} is {run['status']}, not running")


def branch_head(repo_path, branch, label):
    sha = git_commit(repo_path, f"refs/heads/{branch}")
    if not sha:
        raise FactoryError(f"{label} {branch} does not exist in {repo_path}; create its worktree first")
    return sha


def load_plan(conn, run_id):
    run = runs.get(conn, run_id)
    existing = job_rows(conn, run_id)
    if any(job["stage"] != "queued" for job in existing):
        raise FactoryError(f"run {run_id} has started jobs; plan load refuses a new plan")
    paths = runs.run_paths(run_id)
    if not os.path.isfile(paths["plan"]):
        raise FactoryError(f"no plan.md at {paths['plan']}")
    text = read_text(paths["plan"])
    head = heads.parse(text, heads.PLAN)
    if head["kind"] == "QUESTION":
        return questions.from_file(conn, run_id, None, "planner", paths["plan"])
    data, errors = plans.plan_json(text)
    if errors:
        raise FactoryError("plan.md: " + "; ".join(errors))
    project = runs.snapshot(run_id)
    snapshot = runs.snapshot(run_id, "catalogue.json")
    design_dir = paths["design_dir"]
    context = {
        "steps": snapshot.get("steps", {}),
        "pipelines": snapshot.get("pipelines", {}),
        "repos": [repo["name"] for repo in project["repos"]],
        "has_designs": os.path.isdir(design_dir) and any(not name.startswith(".") for name in os.listdir(design_dir)),
        "has_issue": os.path.isfile(paths["issue_file"]),
        "require_code_review": project["defaults"].get("require_code_review", True),
        "facts": plans.repo_facts(text),
    }
    problems = plans.validate(data, context)
    if problems:
        raise FactoryError(f"plan.md has {len(problems)} problem{'s' if len(problems) != 1 else ''}:\n- " + "\n- ".join(problems))
    jobs = data["jobs"]
    order = []
    for job in jobs:
        names = [job["repo"]] if job.get("kind", "code") == "code" else job.get("reads", [])
        for name in names:
            if name not in order:
                order.append(name)
    base = None
    first = conn.execute("SELECT payload FROM events WHERE run_id = ? AND kind = 'run.new' ORDER BY id LIMIT 1", (run_id,)).fetchone()
    if first:
        base = json.loads(first[0]).get("base")
    by_name = {repo["name"]: repo for repo in project["repos"]}
    with db.tx(conn):
        conn.execute("DELETE FROM jobs WHERE run_id = ?", (run_id,))
        present = [repo["repo"] for repo in runs.run_repos(conn, run_id)]
        missing = [by_name[name] for name in order if name not in present]
        added = runs.insert_repos(conn, run_id, project, missing, base, start=len(present))
        ranking = order + [name for name in present if name not in order]
        for position, name in enumerate(ranking, start=1):
            conn.execute("UPDATE run_repos SET position = ? WHERE run_id = ? AND repo = ?", (position, run_id, name))
        for position, job in enumerate(jobs, start=1):
            kind = job.get("kind", "code")
            branch = f"{run['feature_branch']}--{job['id']}" if kind == "code" else None
            worktree = runs.job_worktree(run_id, job["id"]) if kind == "code" else None
            conn.execute(
                "INSERT INTO jobs (run_id, id, position, kind, repo, title, depends_on, reads, template, branch, worktree) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, job["id"], position, kind, job.get("repo") if kind == "code" else None, job["title"].strip(),
                 json.dumps(job.get("depends_on", [])), json.dumps(job.get("reads", [])), job.get("template"),
                 branch, worktree),
            )
            for number, group in enumerate(job["pipeline"], start=1):
                conn.execute(
                    "INSERT INTO job_groups (run_id, job_id, position, step, gates, max_round) VALUES (?, ?, ?, ?, ?, ?)",
                    (run_id, job["id"], number, group["step"], json.dumps(list(dict.fromkeys(group.get("gates", [])))),
                     run["max_rounds"]),
                )
        for dependency, effect in runs.code_host_skips(project, added):
            if dependency == "code_host.push" and "origin" in effect:
                runs.record_skip(conn, run_id, dependency, effect)
        runs.update_no_pr(conn, run_id, project)
        db.event(conn, "plan.load", run_id, jobs=len(jobs), repos=ranking)
    result_jobs = []
    for job in job_rows(conn, run_id):
        spec = os.path.join(job_dir(run_id, job["id"]), "spec.md")
        result_jobs.append({
            "id": job["id"],
            "kind": job["kind"],
            "repo": job["repo"],
            "title": job["title"],
            "depends_on": job["depends_on"],
            "reads": job["reads"],
            "template": job["template"],
            "pipeline": [{"step": group["step"], "gates": group["gates"]} for group in group_rows(conn, run_id, job["id"])],
            "spec": spec,
            "missing": [] if os.path.isfile(spec) else ["spec.md"],
        })
    repos = [{
        "name": repo["repo"],
        "path": repo["path"],
        "base_branch": repo["base_branch"],
        "feature_worktree": repo["feature_worktree"],
        "can_push": repo["can_push"],
    } for repo in runs.run_repos(conn, run_id)]
    return {"jobs": result_jobs, "repos": repos, "no_pr": bool(runs.get(conn, run_id)["no_pr"])}


def producer_slots(conn, run_id):
    count = 0
    for job in job_rows(conn, run_id):
        if job["stage"] != "running":
            continue
        group = current_group(conn, run_id, job["id"])
        if group and group["phase"] == "produce":
            count += 1
    return count


def ready(conn, run_id):
    run = runs.get(conn, run_id)
    require_running_run(run)
    jobs = job_rows(conn, run_id)
    done = {job["id"] for job in jobs if job["stage"] == "done"}
    dropped = {job["id"] for job in jobs if job["stage"] == "dropped"}
    running = producer_slots(conn, run_id)
    free = max(0, run["parallel"] - running)
    candidates = [job["id"] for job in jobs if job["stage"] == "queued" and all(dep in done for dep in job["depends_on"])]
    blocked = [{"job": job["id"], "dropped": [dep for dep in job["depends_on"] if dep in dropped]}
               for job in jobs if job["stage"] == "queued" and any(dep in dropped for dep in job["depends_on"])]
    waiting = [job["id"] for job in jobs if job["stage"] == "queued" and job["id"] not in candidates
               and not any(dep in dropped for dep in job["depends_on"])]
    return {"ready": candidates[:free], "running": running, "parallel": run["parallel"],
            "queued": candidates[free:], "waiting": waiting, "blocked": blocked}


def start(conn, ref):
    run_id, job_id = parse_ref(ref)
    run = runs.get(conn, run_id)
    require_running_run(run)
    job = job_row(conn, run_id, job_id)
    if job["stage"] != "queued":
        raise FactoryError(f"job {job_id} is {job['stage']}, not queued")
    for dep in job["depends_on"]:
        if job_row(conn, run_id, dep)["stage"] != "done":
            raise FactoryError(f"job {job_id} waits for {dep}")
    groups = group_rows(conn, run_id, job_id)
    base_sha = None
    if job["kind"] == "code":
        repo = repo_of(conn, run_id, job["repo"])
        base_sha = branch_head(repo["path"], run["feature_branch"], "feature branch")
    with db.tx(conn):
        set_stage(conn, run_id, job_id, "queued", "running")
        conn.execute("UPDATE jobs SET group_pos = 1 WHERE run_id = ? AND id = ?", (run_id, job_id))
        set_phase(conn, run_id, job_id, groups[0], "produce", round=1, base_sha=base_sha)
        start_round(conn, run_id, job_id, groups[0], 1)
    os.makedirs(job_dir(run_id, job_id), exist_ok=True)
    if job["kind"] == "files":
        os.makedirs(os.path.join(job_dir(run_id, job_id), "out"), exist_ok=True)
    return job_view(conn, run_id, job_id)


def store_agent(conn, ref, name):
    run_id, job_id = parse_ref(ref)
    job_row(conn, run_id, job_id)
    group = current_group(conn, run_id, job_id)
    if not group:
        raise FactoryError(f"job {job_id} has not started")
    if not (name or "").strip():
        raise FactoryError("the agent name is empty", code=2)
    with db.tx(conn):
        conn.execute("UPDATE job_groups SET agent = ? WHERE run_id = ? AND job_id = ? AND position = ?",
                     (name.strip(), run_id, job_id, group["position"]))
        db.event(conn, "job.agent", run_id, job_id, group=group["position"], agent=name.strip())
    return {"ref": ref, "group": group["position"], "producer": name.strip()}


def running_group(conn, run_id, job_id):
    job = job_row(conn, run_id, job_id)
    if job["stage"] != "running":
        raise FactoryError(f"job {job_id} is {job['stage']}, not running")
    group = current_group(conn, run_id, job_id)
    if not group:
        raise FactoryError(f"job {job_id} has no current group")
    return job, group


def gate_list(run_id, job, group, steps):
    folder = round_dir(group_dir(run_id, job["id"], group["position"], group["step"]), group["round"])
    found = []
    for gate in group["gates"]:
        step = steps.get(gate, {})
        found.append({
            "gate": gate,
            "agent": step.get("agent"),
            "model": step.get("model"),
            "gate_file": step.get("path"),
            "review_file": os.path.join(folder, f"{gate}.md"),
            "round_dir": folder,
            "human": step.get("agent") == "human",
        })
    return found


def next_round(conn, ref):
    run_id, job_id = parse_ref(ref)
    job, group = running_group(conn, run_id, job_id)
    if group["phase"] == "gate":
        state = gate_state(conn, run_id, job, group, record=False)
        if state["outcome"] != "CHANGES":
            raise FactoryError(
                f"group {group['position']} round {group['round']} is {state['outcome']}: "
                "a new round starts only after the gates asked for changes (WAIT FOR ALL)"
            )
    elif group["phase"] != "produce":
        raise FactoryError(f"group {group['position']} is {group['phase']}")
    if group["round"] >= group["max_round"]:
        raise FactoryError(f"group {group['position']} is at its round cap ({group['max_round']})")
    number = group["round"] + 1
    with db.tx(conn):
        set_phase(conn, run_id, job_id, group, "produce", round=number)
        start_round(conn, run_id, job_id, group, number)
    view = job_view(conn, run_id, job_id)
    return {"round": number, "round_dir": view["group"]["round_dir"], "group": view["group"]}


def advance(conn, ref):
    run_id, job_id = parse_ref(ref)
    job, group = running_group(conn, run_id, job_id)
    if group["phase"] not in ("passed", "capped"):
        raise FactoryError(f"group {group['position']} is {group['phase']}, not passed or capped")
    groups = group_rows(conn, run_id, job_id)
    later = [entry for entry in groups if entry["position"] == group["position"] + 1]
    if later:
        following = later[0]
        base_sha = None
        if job["kind"] == "code":
            repo = repo_of(conn, run_id, job["repo"])
            base_sha = branch_head(repo["path"], job["branch"], "job branch")
        with db.tx(conn):
            conn.execute("UPDATE jobs SET group_pos = ? WHERE run_id = ? AND id = ?", (following["position"], run_id, job_id))
            set_phase(conn, run_id, job_id, following, "produce", round=1, base_sha=base_sha)
            start_round(conn, run_id, job_id, following, 1)
        view = job_view(conn, run_id, job_id)
        return {"stage": "running", "group": view["group"]}
    stage = "merging" if job["kind"] == "code" else "delivering"
    with db.tx(conn):
        set_stage(conn, run_id, job_id, "running", stage)
    return {"stage": stage, "group": None}


def move_stage(conn, ref, stage):
    run_id, job_id = parse_ref(ref)
    job = job_row(conn, run_id, job_id)
    allowed = {("merging", "done"), ("delivering", "done"), ("merging", "running")}
    if (job["stage"], stage) not in allowed:
        raise FactoryError(f"job stage cannot move from {job['stage']} to {stage} with job stage")
    groups = group_rows(conn, run_id, job_id)
    with db.tx(conn):
        if stage == "done":
            conn.execute("UPDATE jobs SET conflict = 0 WHERE run_id = ? AND id = ?", (run_id, job_id))
            set_stage(conn, run_id, job_id, job["stage"], "done")
        else:
            last = groups[-1]
            conn.execute("UPDATE jobs SET conflict = 1, group_pos = ? WHERE run_id = ? AND id = ?",
                         (last["position"], run_id, job_id))
            set_phase(conn, run_id, job_id, last, "produce")
            set_stage(conn, run_id, job_id, "merging", "running", conflict=True)
    return show(conn, ref)


def rework(conn, ref, note, position=None):
    run_id, job_id = parse_ref(ref)
    run = runs.get(conn, run_id)
    job = job_row(conn, run_id, job_id)
    note = (note or "").strip()
    if not note:
        raise FactoryError("the rework note is empty", code=2)
    if job["stage"] not in ("done", "dropped"):
        raise FactoryError(f"job {job_id} is {job['stage']}; only a done or dropped job can be reworked")
    if run["status"] == "failed":
        raise FactoryError(f"run {run_id} failed; a failed run cannot be reworked")
    groups = group_rows(conn, run_id, job_id)
    position = position or groups[-1]["position"]
    chosen = next((group for group in groups if group["position"] == position), None)
    if not chosen:
        raise FactoryError(f"job {job_id} has no group {position}")
    base_sha = None
    if job["kind"] == "code":
        repo = repo_of(conn, run_id, job["repo"])
        base_sha = branch_head(repo["path"], run["feature_branch"], "feature branch")
    number = chosen["round"] + 1
    cap = max(chosen["max_round"], number)
    folder = group_dir(run_id, job_id, chosen["position"], chosen["step"])
    with db.tx(conn):
        set_phase(conn, run_id, job_id, chosen, "produce", round=number, max_round=cap, base_sha=base_sha)
        for group in groups:
            if group["position"] > position:
                set_phase(conn, run_id, job_id, group, "pending", round=0, base_sha=None, agent=None)
        conn.execute("UPDATE jobs SET group_pos = ?, conflict = 0 WHERE run_id = ? AND id = ?", (position, run_id, job_id))
        set_stage(conn, run_id, job_id, job["stage"], "running", rework=True)
        start_round(conn, run_id, job_id, chosen, number)
        write_text(os.path.join(round_dir(folder, number), "rework.md"), f"# Rework\n\n{note}\n")
        db.event(conn, "job.rework", run_id, job_id, group=position, round=number)
        if run["status"] in ("done", "finishing"):
            runs.move(conn, run_id, "running", reason=f"rework of {job_id}")
    return show(conn, ref)


def restored_phase(conn, run_id, job_id, group):
    found = conn.execute(
        "SELECT 1 FROM verdicts WHERE run_id = ? AND job_id = ? AND group_pos = ? AND round = ? AND verdict = 'CHANGES'",
        (run_id, job_id, group["position"], group["round"]),
    ).fetchone()
    return "capped" if found else "passed"


def step_sync(conn, ref):
    run_id, job_id = parse_ref(ref)
    job, group = running_group(conn, run_id, job_id)
    steps = steps_of(run_id)
    folder = group_dir(run_id, job_id, group["position"], group["step"])
    path = os.path.join(folder, "output.md")
    head = heads.read(path, heads.OUTPUT)
    if group["phase"] == "gate" and head["kind"] == "DONE":
        return {"status": "DONE", "phase": "gate", "gates": gate_list(run_id, job, group, steps), "path": path}
    if group["phase"] != "produce":
        raise FactoryError(f"group {group['position']} is {group['phase']}; step sync reads a producer's output.md in phase produce")
    if head["kind"] == "MISSING":
        return {"status": "MISSING", "path": path}
    if head["kind"] == "INVALID":
        return {"status": "INVALID", "path": path, "line": head["line"]}
    if head["kind"] == "BLOCKED":
        with db.tx(conn):
            seen = conn.execute(
                "SELECT 1 FROM events WHERE run_id = ? AND job_id = ? AND kind = 'step.blocked' "
                "AND json_extract(payload, '$.group') = ? AND json_extract(payload, '$.round') = ?",
                (run_id, job_id, group["position"], group["round"]),
            ).fetchone()
            if not seen:
                db.event(conn, "step.blocked", run_id, job_id, group=group["position"], round=group["round"], reason=head["detail"])
        return {"status": "BLOCKED", "reason": head["detail"], "path": path, "round": group["round"],
                "max_round": group["max_round"]}
    if head["kind"] == "QUESTION":
        return dict({"status": "QUESTION", "path": path}, **questions.from_file(conn, run_id, job_id, f"produce:{group['step']}", path))
    if job["conflict"]:
        with db.tx(conn):
            set_phase(conn, run_id, job_id, group, restored_phase(conn, run_id, job_id, group))
            conn.execute("UPDATE jobs SET conflict = 0 WHERE run_id = ? AND id = ?", (run_id, job_id))
            set_stage(conn, run_id, job_id, "running", "merging", conflict=False)
        return {"status": "DONE", "stage": "merging"}
    gates = gate_list(run_id, job, group, steps)
    phase = "gate" if gates else "passed"
    with db.tx(conn):
        set_phase(conn, run_id, job_id, group, phase)
    return {"status": "DONE", "phase": phase, "gates": gates, "path": path}


def record_verdict(conn, run_id, job_id, group, gate, verdict, path):
    current = conn.execute(
        "SELECT verdict FROM verdicts WHERE run_id = ? AND job_id = ? AND group_pos = ? AND round = ? AND gate = ?",
        (run_id, job_id, group["position"], group["round"], gate),
    ).fetchone()
    if current and current[0] == verdict:
        return
    conn.execute(
        "INSERT INTO verdicts (run_id, job_id, group_pos, round, gate, verdict, path) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (run_id, job_id, group_pos, round, gate) DO UPDATE SET verdict = excluded.verdict, path = excluded.path",
        (run_id, job_id, group["position"], group["round"], gate, verdict, path),
    )
    db.event(conn, "gate.verdict", run_id, job_id, group=group["position"], round=group["round"], gate=gate, verdict=verdict)


def ask_text(steps, gate, job, output):
    step = steps.get(gate, {})
    try:
        _, body = catalogue.front_matter(read_text(step["path"]))
    except (FactoryError, KeyError, OSError):
        return None
    text, _ = catalogue.ask_section(body)
    return catalogue.fill_ask(text, job["title"], output) if text else None


def open_points(texts):
    found = []
    for gate, text in texts:
        parts = heads.sections(text)
        for item in heads.items(parts.get("must fix")):
            found.append({"gate": gate, "kind": "must_fix", "text": item})
        for item in heads.items(parts.get("notes")):
            found.append({"gate": gate, "kind": "note", "text": item})
    return found


def gate_state(conn, run_id, job, group, record=True):
    steps = steps_of(run_id)
    folder = round_dir(group_dir(run_id, job["id"], group["position"], group["step"]), group["round"])
    output = os.path.join(group_dir(run_id, job["id"], group["position"], group["step"]), "output.md")
    entries, asked, errors, texts = [], [], [], []
    for gate in group["gates"]:
        human = is_human(steps, gate)
        path = os.path.join(folder, f"{gate}.md")
        head = heads.read(path, heads.GATE)
        entry = {"gate": gate, "human": human, "review_file": path, "agent": steps.get(gate, {}).get("agent"),
                 "model": steps.get(gate, {}).get("model"), "verdict": head["kind"]}
        if head["kind"] in ("PASS", "CHANGES"):
            texts.append((gate, head["text"]))
            if record:
                record_verdict(conn, run_id, job["id"], group, gate, head["kind"], path)
        elif head["kind"] == "QUESTION" and record and not human:
            stored = questions.from_file(conn, run_id, job["id"], f"gate:{gate}", path)
            if "question" in stored:
                entry["question"] = stored["question"]
                asked.append(stored["question"])
                record_verdict(conn, run_id, job["id"], group, gate, "QUESTION", path)
            else:
                entry["question_error"] = stored["question_error"]
                errors.append({"gate": gate, "problems": stored["question_error"], "path": path})
        entries.append(entry)
    agent_entries = [entry for entry in entries if not entry["human"]]
    human_entries = [entry for entry in entries if entry["human"]]
    agents_in = all(entry["verdict"] in ("PASS", "CHANGES") for entry in agent_entries)
    agents_pass = agents_in and all(entry["verdict"] == "PASS" for entry in agent_entries)
    waiting_humans = []
    if agents_in and not agents_pass:
        outcome, complete = "CHANGES", True
    elif agents_pass:
        pending = [entry for entry in human_entries if entry["verdict"] not in ("PASS", "CHANGES")]
        if pending:
            outcome, complete = "PENDING", False
            for entry in pending:
                text = ask_text(steps, entry["gate"], job, output)
                open_id = None
                if text:
                    open_id = questions.existing(conn, run_id, job["id"], f"gate:{entry['gate']}", output, text)
                waiting_humans.append({"gate": entry["gate"], "question": text, "path": output, "open_question": open_id})
        else:
            outcome = "CHANGES" if any(entry["verdict"] == "CHANGES" for entry in human_entries) else "PASS"
            complete = True
    else:
        outcome, complete = "PENDING", False
    return {"complete": complete, "outcome": outcome, "gates": entries, "questions": asked,
            "question_errors": errors, "human": waiting_humans, "texts": texts}


def gate_sync(conn, ref):
    run_id, job_id = parse_ref(ref)
    job, group = running_group(conn, run_id, job_id)
    if group["phase"] not in ("gate", "passed", "capped"):
        raise FactoryError(f"group {group['position']} is {group['phase']}; gate sync runs in phase gate")
    with db.tx(conn):
        state = gate_state(conn, run_id, job, group, record=group["phase"] == "gate")
        phase = group["phase"]
        if phase == "gate" and state["outcome"] == "PASS":
            phase = "passed"
            set_phase(conn, run_id, job_id, group, phase)
        elif phase == "gate" and state["outcome"] == "CHANGES" and group["round"] >= group["max_round"]:
            phase = "capped"
            set_phase(conn, run_id, job_id, group, phase)
    result = {key: state[key] for key in ("complete", "outcome", "gates", "questions", "question_errors", "human")}
    result.update(phase=phase, round=group["round"], max_round=group["max_round"], group=group["position"],
                  round_dir=round_dir(group_dir(run_id, job_id, group["position"], group["step"]), group["round"]))
    if phase == "capped":
        result["open_points"] = open_points(state["texts"])
    return result


def gate_human(conn, ref, gate, verdict, note=None):
    run_id, job_id = parse_ref(ref)
    job, group = running_group(conn, run_id, job_id)
    steps = steps_of(run_id)
    if verdict not in ("pass", "changes"):
        raise FactoryError("the verdict must be pass or changes", code=2)
    if gate not in group["gates"]:
        raise FactoryError(f"{gate} is not a gate of group {group['position']}")
    if not is_human(steps, gate):
        raise FactoryError(f"{gate} is not a human gate")
    if group["phase"] != "gate":
        raise FactoryError(f"group {group['position']} is {group['phase']}, not waiting for its gates")
    note = (note or "").strip()
    if verdict == "changes" and not note:
        raise FactoryError("changes needs --note with what to change", code=2)
    state = gate_state(conn, run_id, job, group, record=False)
    agents = [entry for entry in state["gates"] if not entry["human"]]
    if not all(entry["verdict"] == "PASS" for entry in agents):
        raise FactoryError("the agent gates of this round have not all passed; a person is asked only after they pass")
    folder = round_dir(group_dir(run_id, job_id, group["position"], group["step"]), group["round"])
    path = os.path.join(folder, f"{gate}.md")
    if verdict == "pass":
        lines = ["VERDICT: PASS", "A person approved the result.", ""]
        if note:
            lines += ["## Notes", f"- [human] {note}", ""]
    else:
        lines = ["VERDICT: CHANGES", "A person asked for changes.", "", "## Must fix", f"- {note}", ""]
    with db.tx(conn):
        write_text(path, "\n".join(lines))
        record_verdict(conn, run_id, job_id, group, gate, verdict.upper(), path)
        db.event(conn, "gate.human", run_id, job_id, group=group["position"], round=group["round"], gate=gate, verdict=verdict)
    return {"gate": gate, "verdict": verdict.upper(), "review_file": path, "round": group["round"]}


def drop(conn, ref, reason):
    run_id, job_id = parse_ref(ref)
    runs.get(conn, run_id)
    job = job_row(conn, run_id, job_id)
    reason = " ".join((reason or "").split())
    if not reason:
        raise FactoryError("say why the job is dropped", code=2)
    if job["stage"] in ("done", "dropped"):
        raise FactoryError(f"job {job_id} is {job['stage']}; only an unfinished job can be dropped")
    with db.tx(conn):
        conn.execute("UPDATE jobs SET conflict = 0 WHERE run_id = ? AND id = ?", (run_id, job_id))
        set_stage(conn, run_id, job_id, job["stage"], "dropped", reason=reason)
        db.event(conn, "job.drop", run_id, job_id, reason=reason)
        append_line(runs.retro_notes(run_id), f"- dropped the job: {reason} ({job_id})")
    dependants = [other["id"] for other in job_rows(conn, run_id)
                  if job_id in other["depends_on"] and other["stage"] not in ("done", "dropped")]
    return dict(show(conn, ref), dependants=dependants, reason=reason)


def save_targets(run_id, job, group):
    folder = group_dir(run_id, job["id"], group["position"], group["step"])
    targets = {os.path.join(folder, "output.md")}
    for gate in group["gates"]:
        targets.add(os.path.join(round_dir(folder, group["round"]), f"{gate}.md"))
    return folder, targets


def save(conn, ref, target, text, by=None):
    run_id, job_id = parse_ref(ref)
    job, group = running_group(conn, run_id, job_id)
    if not (text or "").strip():
        raise FactoryError("nothing to save: the text on stdin is empty", code=2)
    folder, targets = save_targets(run_id, job, group)
    out_dir = os.path.join(job_dir(run_id, job_id), "out")
    raw = (target or "").strip()
    if os.path.isabs(raw):
        path = os.path.normpath(raw)
    elif raw.startswith("out/"):
        path = os.path.normpath(os.path.join(job_dir(run_id, job_id), raw))
    else:
        path = os.path.normpath(os.path.join(folder, raw))
    inside_out = job["kind"] == "files" and path.startswith(out_dir + os.sep)
    if path not in targets and not inside_out:
        allowed = ["output.md"] + [os.path.relpath(item, folder) for item in sorted(targets) if not item.endswith("output.md")]
        if job["kind"] == "files":
            allowed.append("out/<file>")
        raise FactoryError(f"{raw} is not a file this step may save; use one of: {', '.join(allowed)}")
    if os.path.islink(path) or (inside_out and not os.path.realpath(path).startswith(os.path.realpath(out_dir) + os.sep)):
        raise FactoryError(f"{raw} leaves the job folder")
    write_text(path, text if text.endswith("\n") else text + "\n")
    with db.tx(conn):
        db.event(conn, "step.save", run_id, job_id, path=path, bytes=len(text.encode("utf-8")), by=by,
                 group=group["position"], round=group["round"])
    return {"ref": ref, "path": path, "bytes": len(text.encode("utf-8"))}
