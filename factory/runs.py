import datetime
import json
import os
import re
import shutil

from . import FactoryError, catalogue, connected, db, home, issues, projects
from .util import (
    append_line, expand, git, git_branch_exists, git_default_branch, git_origin, git_toplevel,
    now, read_json, same_path, slugify, valid_branch_name, write_json, write_text,
)

STATUSES = ("planning", "confirm", "running", "finishing", "done", "failed")
ACTIVE = ("planning", "confirm", "running", "finishing")
MOVES = {
    "planning": ("confirm", "running", "failed"),
    "confirm": ("running", "planning", "failed"),
    "running": ("finishing", "failed"),
    "finishing": ("done", "running", "failed"),
    "done": ("running",),
    "failed": (),
}
RUN_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9-]{1,40}$")
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,63}$")
DEPENDENCY_RE = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_]*)?$")
EVENT_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def run_dir(run_id):
    return home.path("runs", run_id)


def feature_worktree(run_id, repo):
    return home.path("worktrees", run_id, "_feature", repo)


def job_worktree(run_id, job_id):
    return home.path("worktrees", run_id, job_id)


def retro_notes(run_id):
    return os.path.join(run_dir(run_id), "retro-notes.md")


def get(conn, run_id):
    run = db.one(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)))
    if not run:
        raise FactoryError(f"no run {run_id}")
    return run


def run_repos(conn, run_id):
    found = db.rows(conn.execute("SELECT * FROM run_repos WHERE run_id = ? ORDER BY position", (run_id,)))
    for repo in found:
        repo["can_push"] = bool(repo["can_push"])
        repo["feature_worktree"] = feature_worktree(run_id, repo["repo"])
    return found


def repo_summary(repo):
    return {
        "name": repo["repo"],
        "path": repo["path"],
        "base_branch": repo["base_branch"],
        "feature_worktree": repo["feature_worktree"],
        "can_push": repo["can_push"],
        "pr_url": repo["pr_url"],
        "pr_state": repo["pr_state"],
    }


def snapshot(run_id, name="project.json"):
    return read_json(os.path.join(run_dir(run_id), name))


def new_id(conn, feature):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = slugify(feature, 24) or "run"
    candidate, number = f"{stamp}-{slug}", 2
    while conn.execute("SELECT 1 FROM runs WHERE id = ?", (candidate,)).fetchone() or os.path.exists(run_dir(candidate)):
        candidate = f"{stamp}-{slug}-{number}"
        number += 1
    return candidate


def check_session(session):
    if session and not SESSION_RE.match(session):
        raise FactoryError(f"bad session id {session!r}", code=2)
    return session


def session_id(value):
    if value:
        return check_session(value)
    fallback = os.environ.get("CLAUDE_CODE_SESSION_ID") or None
    return check_session(fallback) if fallback else None


def add_session(conn, run_id, session):
    cursor = conn.execute(
        "INSERT OR IGNORE INTO sessions (run_id, session_id, added_at) VALUES (?, ?, ?)", (run_id, session, now())
    )
    if cursor.rowcount:
        db.event(conn, "run.session", run_id, session=session)


def push_state(code_host, repo):
    push = (code_host or {}).get("capabilities", {}).get("push")
    if not push or push.get("status") in ("missing", "not built"):
        return False
    if push.get("via") == "git":
        return bool(repo.get("has_origin"))
    return push.get("status") in ("available", "check_in_session", "partial")


def run_repo_entry(project, repo, base):
    if not repo["is_git"]:
        raise FactoryError(f"repo {repo['name']}: {repo['path']} is not a git checkout")
    branch = base or repo["default_branch"] or git_default_branch(repo["path"])
    if not branch:
        raise FactoryError(f"repo {repo['name']}: no default branch found; pass --base")
    return {
        "repo": repo["name"],
        "path": repo["path"],
        "base_branch": branch,
        "can_push": push_state(project["integrations"].get("code_host"), repo),
    }


def pick_repos(project, wanted, cwd):
    by_name = {repo["name"]: repo for repo in project["repos"]}
    picked = []
    for item in wanted or []:
        repo = by_name.get(item)
        if repo is None:
            candidate = expand(item, base=cwd) if ("/" in item or item.startswith((".", "~"))) else None
            top = git_toplevel(candidate) if candidate else None
            repo = next((entry for entry in project["repos"] if top and same_path(entry["path"], top)), None)
        if repo is None:
            raise FactoryError(f"{item!r} is not a repo of project {project['name']}")
        if repo not in picked:
            picked.append(repo)
    return picked


def insert_repos(conn, run_id, project, repos, base, start=0):
    added = []
    for offset, repo in enumerate(repos):
        entry = run_repo_entry(project, repo, base)
        conn.execute(
            "INSERT INTO run_repos (run_id, repo, position, path, base_branch, can_push) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, entry["repo"], start + offset + 1, entry["path"], entry["base_branch"], int(entry["can_push"])),
        )
        db.event(conn, "run.repo", run_id, repo=entry["repo"], base=entry["base_branch"], can_push=entry["can_push"])
        added.append(entry)
    return added


def code_host_skips(project, repos):
    found = []
    host = project["integrations"].get("code_host") or {}
    capabilities = host.get("capabilities", {})
    push = capabilities.get("push")
    if host.get("adapter") == "none" or not push:
        found.append(("code_host.push", f"no code host that can push ({host.get('adapter', 'none')}): nothing is pushed, no PR is opened"))
        return found
    if push.get("status") in ("missing", "not built") and push.get("via") != "git":
        found.append(("code_host.push", f"push is not available: {push.get('reason')}"))
        return found
    for repo in repos:
        if not repo["can_push"]:
            found.append(("code_host.push", f"{repo['repo']} has no origin remote, nothing is pushed for it"))
    open_pr = capabilities.get("open_pr")
    if not open_pr:
        found.append(("code_host.open_pr", f"the code host {host['adapter']} cannot open PRs: branches are pushed for a person to open them"))
    elif open_pr.get("status") in ("missing", "not built"):
        found.append(("code_host.open_pr", f"open_pr is not available ({open_pr.get('reason')}): branches are pushed for a person to open them"))
    return found


def record_skip(conn, run_id, dependency, effect, pr=False, job_id=None):
    if not DEPENDENCY_RE.match(dependency or ""):
        raise FactoryError(f"bad dependency {dependency!r}: use <slot>.<capability> or a plain name", code=2)
    effect = " ".join((effect or "").split())
    if not effect:
        raise FactoryError("the effect is empty", code=2)
    seen = conn.execute(
        "SELECT id FROM events WHERE run_id = ? AND kind = 'skip' AND COALESCE(job_id, '') = ? "
        "AND json_extract(payload, '$.dependency') = ? AND json_extract(payload, '$.effect') = ?",
        (run_id, job_id or "", dependency, effect),
    ).fetchone()
    if seen:
        return False
    db.event(conn, "skip", run_id, job_id, dependency=dependency, effect=effect, pr=bool(pr))
    suffix = f" ({job_id})" if job_id else ""
    append_line(retro_notes(run_id), f"- skipped {dependency}: {effect}{suffix}")
    return True


def skips(conn, run_id, pr_only=False):
    found = []
    for row in db.rows(conn.execute("SELECT job_id, at, payload FROM events WHERE run_id = ? AND kind = 'skip' ORDER BY id", (run_id,))):
        payload = json.loads(row["payload"])
        if pr_only and not payload.get("pr"):
            continue
        found.append({"dependency": payload.get("dependency"), "effect": payload.get("effect"),
                      "pr": bool(payload.get("pr")), "job_id": row["job_id"], "at": row["at"]})
    return found


def repo_path_argument(repo_args, cwd):
    for item in repo_args or []:
        if "/" in item or item.startswith((".", "~")):
            top = git_toplevel(expand(item, base=cwd))
            if top:
                return top
    return None


def resolve_project(conn, flag, cwd, issue, repo_args=None):
    if issue and not flag:
        return issue["project"], False
    found = projects.resolution(conn, flag, cwd)
    if not found["project"] and not flag and repo_path_argument(repo_args, cwd):
        found = projects.resolution(conn, None, repo_path_argument(repo_args, cwd))
    if issue and found["project"] != issue["project"]:
        raise FactoryError(f"issue {issue['key']} belongs to project {issue['project']}, not {found['project']}")
    if found["project"]:
        return found["project"], False
    if not found["toplevel"]:
        raise FactoryError("no project: not in a git checkout and no --project given")
    return projects.register_adhoc(conn, found["toplevel"]), True


def feature_branch(prefix, key, title, given):
    if given:
        branch = given
    else:
        slug = slugify(title, 40) or "change"
        branch = f"{prefix}{key}-{slug}" if key else f"{prefix}{slug}"
    if not valid_branch_name(branch):
        raise FactoryError(f"{branch!r} is not a valid branch name")
    return branch


def check_int(value, low, high, label):
    if value is None:
        return None
    if not (low <= value <= high):
        raise FactoryError(f"{label} must be from {low} to {high}", code=2)
    return value


def active_run(conn, issue_key):
    return db.one(conn.execute(
        "SELECT r.id, r.status FROM issue_links l JOIN runs r ON r.id = l.ref "
        "WHERE l.issue_key = ? AND l.kind = 'run' AND r.status NOT IN ('done', 'failed') AND r.archived_at IS NULL "
        "ORDER BY r.created_at DESC, r.id DESC LIMIT 1",
        (issue_key,),
    ))


def check_issue_free(conn, issue):
    found = active_run(conn, issue["key"])
    if found:
        raise FactoryError(
            f"issue {issue['key']} already has run {found['id']} ({found['status']}); resume it, or pass --again to start another",
            code=2,
        )


def new(conn, feature, project_flag=None, repo_args=None, issue_key=None, external_key=None, external_url=None,
        branch=None, base=None, parallel=None, max_rounds=None, confirm_plan=False, no_pr=False, session=None, cwd=None,
        again=False, author="human"):
    cwd = cwd or os.getcwd()
    feature = " ".join((feature or "").split())
    if not feature:
        raise FactoryError("the feature text is empty", code=2)
    if issue_key and external_key:
        raise FactoryError("give --issue or --external-key, not both", code=2)
    if external_url and not external_key:
        raise FactoryError("--external-url needs --external-key", code=2)
    check_int(parallel, 1, 64, "--parallel")
    check_int(max_rounds, 1, 10, "--max-rounds")
    if base and not valid_branch_name(base):
        raise FactoryError(f"{base!r} is not a valid branch name")
    session = session_id(session)
    author = connected.check_author(author)
    local_issue = issues.get(conn, issue_key) if issue_key else None
    if local_issue and not again:
        check_issue_free(conn, local_issue)
    project_name, adhoc = resolve_project(conn, project_flag, cwd, local_issue, repo_args)
    config = home.load_config()
    project = projects.resolved(projects.load(project_name, config), config, adhoc=adhoc)
    if author not in ("human", "dashboard") and not local_issue:
        raise FactoryError(f"{author} starts runs for the user only from a ready local issue; give --issue", code=2)
    picked = pick_repos(project, repo_args, cwd)
    mirror_issue = None
    if external_key:
        mirror_issue, _ = issues.mirror(conn, project_name, external_key, feature, url=external_url)
    issue = local_issue or mirror_issue
    if mirror_issue and not again:
        check_issue_free(conn, mirror_issue)
    defaults = project["defaults"]
    branch_key = (mirror_issue or {}).get("external_key") or (local_issue or {}).get("key")
    title = local_issue["title"] if local_issue else feature
    branch_name = feature_branch(defaults["branch_prefix"], branch_key, title, branch)
    confirm = bool(confirm_plan or issue or defaults["confirm_plan"])
    run_id = new_id(conn, title)
    folder = run_dir(run_id)
    paths = run_paths(run_id)
    catalogue_snapshot = catalogue.show(config, project)
    with db.tx(conn):
        if author not in ("human", "dashboard"):
            check_agent_start(conn, author, issues.get(conn, local_issue["key"]), project)
        conn.execute(
            "INSERT INTO runs (id, project, feature, issue_key, issue_url, slug, feature_branch, parallel, max_rounds, "
            "confirm_plan, no_pr, status, created_at, started_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'planning', ?, ?)",
            (run_id, project_name, feature, issue["key"] if issue else None,
             (issue or {}).get("external_url"), slugify(title, 40) or "change", branch_name,
             parallel or defaults["parallel"], max_rounds or defaults["max_rounds"], int(confirm), int(bool(no_pr)), now(),
             author),
        )
        db.event(conn, "run.new", run_id, project=project_name, issue=issue["key"] if issue else None,
                 branch=branch_name, base=base, by=author)
        connected.seen(conn, author)
        os.makedirs(os.path.join(folder, "jobs"), exist_ok=True)
        os.makedirs(paths["design_dir"], exist_ok=True)
        write_json(paths["project_file"], project)
        write_json(paths["catalogue_file"], catalogue_snapshot)
        added = insert_repos(conn, run_id, project, picked, base)
        if session:
            add_session(conn, run_id, session)
        if issue:
            conn.execute(
                "INSERT OR IGNORE INTO issue_links (issue_key, kind, ref, created_at) VALUES (?, 'run', ?, ?)",
                (issue["key"], run_id, now()),
            )
            issues.event(conn, "issue.link", issue["key"], link="run", ref=run_id)
            issues.move(conn, issue["key"], "in_progress", run=run_id)
        if local_issue:
            write_text(paths["issue_file"], issues.issue_markdown(conn, issues.show(conn, local_issue["key"])))
        if adhoc:
            record_skip(conn, run_id, "project", "ad-hoc project from the current repo")
        for entry in project["skipped"]:
            record_skip(conn, run_id, entry["what"].split(".")[0], f"{entry['what']} {entry['path']} does not exist")
        for dependency, effect in code_host_skips(project, added):
            record_skip(conn, run_id, dependency, effect)
        update_no_pr(conn, run_id, project)
    return run_result(conn, run_id)


def active_runs(conn, project_name):
    placeholders = ", ".join("?" for _ in ACTIVE)
    return conn.execute(
        f"SELECT COUNT(*) FROM runs WHERE project = ? AND archived_at IS NULL AND status IN ({placeholders})",
        (project_name, *ACTIVE),
    ).fetchone()[0]


def check_agent_start(conn, author, issue, project):
    if issue["status"] != "ready":
        raise FactoryError(f"{issue['key']} is {issue['status']}, not ready; {author} starts only ready issues")
    limit = project["defaults"]["agent_runs"]
    if limit is None:
        return
    running = active_runs(conn, project["name"])
    if running >= limit:
        raise FactoryError(
            f"project {project['name']} has {running} active runs and lets agents start runs up to {limit} "
            f"(defaults.agent_runs); {issue['key']} waits"
        )


def update_no_pr(conn, run_id, project):
    repos = run_repos(conn, run_id)
    host = project["integrations"].get("code_host") or {}
    push = host.get("capabilities", {}).get("push")
    cannot = not push or (push.get("status") in ("missing", "not built") and push.get("via") != "git")
    if repos and not any(repo["can_push"] for repo in repos):
        cannot = True
    if cannot and not get(conn, run_id)["no_pr"]:
        conn.execute("UPDATE runs SET no_pr = 1 WHERE id = ?", (run_id,))
        db.event(conn, "run.no_pr", run_id, reason="the code host cannot push for any repo of the run")


def run_result(conn, run_id):
    run = get(conn, run_id)
    paths = run_paths(run_id)
    return {
        "id": run_id,
        "status": run["status"],
        "project": run["project"],
        "feature_branch": run["feature_branch"],
        "run_dir": paths["run_dir"],
        "plan": paths["plan"],
        "issue_key": run["issue_key"],
        "issue_file": paths["issue_file"] if run["issue_key"] else None,
        "repos": [repo_summary(repo) for repo in run_repos(conn, run_id)],
        "project_file": paths["project_file"],
        "catalogue_file": paths["catalogue_file"],
        "skipped": skips(conn, run_id),
        "no_pr": bool(run["no_pr"]),
        "confirm_plan": bool(run["confirm_plan"]),
        "started_by": run["started_by"],
    }


def run_paths(run_id):
    folder = run_dir(run_id)
    return {
        "run_dir": folder,
        "plan": os.path.join(folder, "plan.md"),
        "issue_file": os.path.join(folder, "issue.md"),
        "design_dir": os.path.join(folder, "design"),
        "project_file": os.path.join(folder, "project.json"),
        "catalogue_file": os.path.join(folder, "catalogue.json"),
        "retro_file": os.path.join(folder, "retro.md"),
        "retro_notes": os.path.join(folder, "retro-notes.md"),
        "report": os.path.join(folder, "report.md"),
        "jobs_dir": os.path.join(folder, "jobs"),
    }


def paths(conn, run_id):
    get(conn, run_id)
    found = run_paths(run_id)
    found["feature_worktrees"] = {repo["repo"]: repo["feature_worktree"] for repo in run_repos(conn, run_id)}
    return found


def move(conn, run_id, status, reason=None):
    if status not in STATUSES:
        raise FactoryError(f"status must be one of {', '.join(STATUSES)}", code=2)
    run = get(conn, run_id)
    if run["status"] == status:
        return False
    if status not in MOVES[run["status"]]:
        raise FactoryError(f"run {run_id} cannot move from {run['status']} to {status}")
    if status in ("finishing", "done"):
        open_jobs = [row[0] for row in conn.execute(
            "SELECT id FROM jobs WHERE run_id = ? AND stage NOT IN ('done', 'dropped') ORDER BY position", (run_id,))]
        if open_jobs:
            raise FactoryError(
                f"run {run_id} still has jobs that are neither done nor dropped: {', '.join(open_jobs)}; "
                "finish them, or drop them with job drop"
            )
    conn.execute("UPDATE runs SET status = ? WHERE id = ?", (status, run_id))
    payload = {"from": run["status"], "to": status}
    if reason:
        payload["reason"] = reason
    db.event(conn, "run.status", run_id, **payload)
    if status == "failed" and run["issue_key"]:
        issues.event(conn, "issue.run_failed", run["issue_key"], run=run_id)
    return True


def set_status(conn, run_id, status):
    with db.tx(conn):
        changed = move(conn, run_id, status)
    return {"id": run_id, "status": get(conn, run_id)["status"], "changed": changed}


def add_session_command(conn, run_id, session):
    get(conn, run_id)
    session = session_id(session)
    if not session:
        raise FactoryError("no session id: pass one, or set CLAUDE_CODE_SESSION_ID", code=2)
    with db.tx(conn):
        add_session(conn, run_id, session)
    return {"id": run_id, "sessions": [row[0] for row in conn.execute(
        "SELECT session_id FROM sessions WHERE run_id = ? ORDER BY added_at", (run_id,))]}


def set_rounds(conn, run_id, rounds):
    check_int(rounds, 1, 10, "the round cap")
    get(conn, run_id)
    with db.tx(conn):
        conn.execute("UPDATE runs SET max_rounds = ? WHERE id = ?", (rounds, run_id))
        cursor = conn.execute(
            "UPDATE job_groups SET max_round = ? WHERE run_id = ? AND phase NOT IN ('passed', 'capped')", (rounds, run_id)
        )
        db.event(conn, "run.rounds", run_id, max_rounds=rounds, groups=cursor.rowcount)
    return {"id": run_id, "max_rounds": rounds, "groups": cursor.rowcount}


def events(conn, run_id):
    found = db.rows(conn.execute("SELECT id, job_id, at, kind, payload FROM events WHERE run_id = ? ORDER BY id", (run_id,)))
    for entry in found:
        entry["payload"] = json.loads(entry["payload"])
    return found


def show(conn, run_id):
    from . import jobs, questions

    run = get(conn, run_id)
    found = events(conn, run_id)
    result = dict(run)
    result["confirm_plan"] = bool(run["confirm_plan"])
    result["no_pr"] = bool(run["no_pr"])
    result["paths"] = paths(conn, run_id)
    result["repos"] = [repo_summary(repo) for repo in run_repos(conn, run_id)]
    result["jobs"] = [jobs.summary(conn, run_id, job["id"]) for job in jobs.job_rows(conn, run_id)]
    result["questions"] = questions.listing(conn, run_id)
    result["skips"] = skips(conn, run_id)
    result["sessions"] = [row[0] for row in conn.execute("SELECT session_id FROM sessions WHERE run_id = ? ORDER BY added_at", (run_id,))]
    result["history"] = [{"at": entry["at"], "from": entry["payload"].get("from"), "to": entry["payload"].get("to")}
                         for entry in found if entry["kind"] == "run.status"]
    result["events"] = found
    return result


def listing(conn, project=None, archived=False):
    from . import usage

    if archived == "all":
        sql = "SELECT * FROM runs WHERE 1 = 1"
    else:
        sql = "SELECT * FROM runs WHERE " + ("archived_at IS NOT NULL" if archived else "archived_at IS NULL")
    values = []
    if project:
        sql += " AND project = ?"
        values.append(project)
    found = []
    for run in db.rows(conn.execute(sql + " ORDER BY created_at DESC, id DESC", values)):
        open_questions = conn.execute(
            "SELECT COUNT(*) FROM questions WHERE run_id = ? AND status IN ('open', 'clarify')", (run["id"],)
        ).fetchone()[0]
        total, done = conn.execute("SELECT COUNT(*), COALESCE(SUM(stage = 'done'), 0) FROM jobs WHERE run_id = ?",
                                   (run["id"],)).fetchone()
        found.append({
            "id": run["id"],
            "project": run["project"],
            "status": run["status"],
            "feature": run["feature"],
            "issue_key": run["issue_key"],
            "feature_branch": run["feature_branch"],
            "started_by": run["started_by"],
            "jobs": total,
            "jobs_done": done,
            "open_questions": open_questions,
            "cost": usage.run_cost(conn, run["id"]),
            "archived": run["archived_at"] is not None,
            "created_at": run["created_at"],
        })
    return found


def remove_worktrees(conn, run_id, include_feature_branches=False):
    from . import jobs

    warnings = []
    repos = run_repos(conn, run_id)
    by_name = {repo["repo"]: repo for repo in repos}
    for job in jobs.job_rows(conn, run_id):
        repo = by_name.get(job["repo"])
        if job["kind"] != "code" or not repo or not os.path.isdir(repo["path"]):
            continue
        worktree = job["worktree"] or job_worktree(run_id, job["id"])
        if os.path.exists(worktree):
            if git(["worktree", "remove", "--force", worktree], repo["path"], check=False) is None:
                warnings.append(f"could not remove worktree {worktree}")
        branch = job["branch"]
        if branch and git_branch_exists(repo["path"], branch):
            if git(["branch", "-D", branch], repo["path"], check=False) is None:
                warnings.append(f"could not delete branch {branch} in {repo['repo']}")
    run = get(conn, run_id)
    for repo in repos:
        if not os.path.isdir(repo["path"]):
            continue
        for worktree in (repo["feature_worktree"], home.path("worktrees", run_id, "_after", repo["repo"])):
            if os.path.exists(worktree):
                if git(["worktree", "remove", "--force", worktree], repo["path"], check=False) is None:
                    warnings.append(f"could not remove worktree {worktree}")
        git(["worktree", "prune"], repo["path"], check=False)
        if include_feature_branches and git_branch_exists(repo["path"], run["feature_branch"]):
            if git(["branch", "-D", run["feature_branch"]], repo["path"], check=False) is None:
                warnings.append(f"could not delete branch {run['feature_branch']} in {repo['repo']}")
    folder = home.path("worktrees", run_id)
    if os.path.isdir(folder):
        shutil.rmtree(folder, ignore_errors=True)
    return warnings


def archive(conn, run_id, force=False):
    run = get(conn, run_id)
    if run["status"] not in ("done", "failed") and not force:
        raise FactoryError(f"run {run_id} is {run['status']}; archive a done or failed run, or pass --force")
    warnings = remove_worktrees(conn, run_id)
    with db.tx(conn):
        conn.execute("UPDATE runs SET archived_at = ? WHERE id = ?", (now(), run_id))
        db.event(conn, "run.archive", run_id, force=bool(force))
    return {"id": run_id, "archived": True, "warnings": warnings}


def unarchive(conn, run_id):
    get(conn, run_id)
    with db.tx(conn):
        conn.execute("UPDATE runs SET archived_at = NULL WHERE id = ?", (run_id,))
        db.event(conn, "run.unarchive", run_id)
    return {"id": run_id, "archived": False}


def delete(conn, run_id, force=False):
    run = get(conn, run_id)
    pending = conn.execute(
        "SELECT COUNT(*) FROM improvements WHERE run_id = ? AND status = 'accepted' AND pr_url IS NULL", (run_id,)
    ).fetchone()[0]
    if pending:
        raise FactoryError(f"run {run_id} has {pending} accepted improvements not applied yet; apply or reject them first")
    if run["status"] not in ("done", "failed") and not force:
        raise FactoryError(f"run {run_id} is {run['status']}; delete a done or failed run, or pass --force")
    warnings = remove_worktrees(conn, run_id, include_feature_branches=True)
    with db.tx(conn):
        conn.execute("DELETE FROM issue_links WHERE kind = 'run' AND ref = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        db.event(conn, "run.delete", None, None, run=run_id, project=run["project"])
    shutil.rmtree(run_dir(run_id), ignore_errors=True)
    return {"id": run_id, "deleted": True, "warnings": warnings}


def skip_add(conn, run_id, dependency, effect, pr=False, job_id=None):
    get(conn, run_id)
    if job_id and not conn.execute("SELECT 1 FROM jobs WHERE run_id = ? AND id = ?", (run_id, job_id)).fetchone():
        raise FactoryError(f"run {run_id} has no job {job_id}")
    with db.tx(conn):
        added = record_skip(conn, run_id, dependency, effect, pr, job_id)
    return {"id": run_id, "added": added, "skips": skips(conn, run_id)}


def free_event(conn, run_id, kind, job_id=None, payload=None):
    get(conn, run_id)
    if not EVENT_RE.match(kind or ""):
        raise FactoryError(f"bad event kind {kind!r}", code=2)
    if payload is not None and not isinstance(payload, dict):
        raise FactoryError("--payload must be a JSON object", code=2)
    with db.tx(conn):
        db.event(conn, kind, run_id, job_id, **(payload or {}))
    return {"id": run_id, "kind": kind, "job_id": job_id}


def current_project(run):
    try:
        return projects.resolved(projects.load(run["project"]))
    except FactoryError:
        return snapshot(run["id"])


def store_pr(conn, run_id, url, repo_name):
    from . import adapters

    run = get(conn, run_id)
    repo = next((entry for entry in run_repos(conn, run_id) if entry["repo"] == repo_name), None)
    if not repo:
        raise FactoryError(f"run {run_id} has no repo {repo_name}")
    url = (url or "").strip()
    adapters.check_pr_url(current_project(run), url)
    with db.tx(conn):
        conn.execute("UPDATE run_repos SET pr_url = ?, pr_state = NULL, pr_checked_at = NULL WHERE run_id = ? AND repo = ?",
                     (url, run_id, repo_name))
        db.event(conn, "run.pr", run_id, repo=repo_name, url=url)
        if run["no_pr"]:
            conn.execute("UPDATE runs SET no_pr = 0 WHERE id = ?", (run_id,))
            db.event(conn, "run.no_pr", run_id, cleared=True)
        if run["issue_key"]:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO issue_links (issue_key, kind, ref, created_at) VALUES (?, 'pr', ?, ?)",
                (run["issue_key"], url, now()),
            )
            if cursor.rowcount:
                issues.event(conn, "issue.link", run["issue_key"], link="pr", ref=url)
            if issues.get(conn, run["issue_key"])["status"] in ("open", "ready", "in_progress"):
                issues.move(conn, run["issue_key"], "in_review", run=run_id)
    return run_result(conn, run_id)


def latest_run_of(conn, issue_key):
    found = conn.execute(
        "SELECT id FROM runs WHERE issue_key = ? ORDER BY created_at DESC, id DESC LIMIT 1", (issue_key,)
    ).fetchone()
    return found[0] if found else None


def finish_issue(conn, run):
    if not run["issue_key"] or latest_run_of(conn, run["issue_key"]) != run["id"]:
        return None
    with_prs = [repo for repo in run_repos(conn, run["id"]) if repo["pr_url"]]
    if not with_prs or not all(repo["pr_state"] == "MERGED" for repo in with_prs):
        return None
    issue = issues.get(conn, run["issue_key"])
    if issue["status"] in ("done", "closed"):
        return None
    issues.move(conn, run["issue_key"], "done", run=run["id"], reason="every PR of the run is merged")
    return run["issue_key"]


def check_prs(conn, run_id=None, force=False):
    from . import adapters

    if run_id:
        selected = [get(conn, run_id)]
    else:
        selected = db.rows(conn.execute(
            "SELECT * FROM runs WHERE archived_at IS NULL AND id IN (SELECT run_id FROM run_repos WHERE pr_url IS NOT NULL) "
            "ORDER BY created_at"
        ))
    results, done = [], []
    for run in selected:
        project = current_project(run)
        for repo in run_repos(conn, run["id"]):
            if not repo["pr_url"]:
                continue
            entry = {"run": run["id"], "repo": repo["repo"], "url": repo["pr_url"], "previous": repo["pr_state"]}
            if repo["pr_state"] in adapters.FINAL_STATES and not force:
                results.append(dict(entry, state=repo["pr_state"], checked=False, reason="final state"))
                continue
            repo_entry = {"name": repo["repo"], "path": repo["path"], "remote": git_origin(repo["path"])}
            found = adapters.read_pr_state(project, repo["pr_url"], repo_entry)
            checked = found["state"] in ("OPEN", "MERGED", "DECLINED", "SUPERSEDED")
            if checked:
                with db.tx(conn):
                    conn.execute("UPDATE run_repos SET pr_state = ?, pr_checked_at = ? WHERE run_id = ? AND repo = ?",
                                 (found["state"], now(), run["id"], repo["repo"]))
                    if found["state"] != repo["pr_state"]:
                        db.event(conn, "run.pr_state", run["id"], repo=repo["repo"], state=found["state"],
                                 previous=repo["pr_state"])
            results.append(dict(entry, state=found["state"], checked=checked, reason=found.get("reason")))
        with db.tx(conn):
            finished = finish_issue(conn, get(conn, run["id"]))
        if finished:
            done.append(finished)
    return {"prs": results, "issues_done": done}
