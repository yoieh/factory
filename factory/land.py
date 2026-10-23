import datetime
import json
import os
import re
import time

from . import FactoryError, adapters, connected, db, jobs, runs
from .util import git, git_origin, read_text, write_json, write_text

POLL = 30
VERDICT_RE = re.compile(r"^VERDICT: (PASS|CHANGES)\s*$")
ROUND_RE = re.compile(r"^round-([0-9]+)$")
WAITING = "pending"
FIXES = 2
FRESH_HOURS = 24


def mode_of(project):
    return (project.get("defaults") or {}).get("land", "off")


def fixes_of(project):
    return (project.get("defaults") or {}).get("land_fixes", FIXES)


def host_capability(project, capability):
    host = project["integrations"].get("code_host")
    if not host:
        return None, "the project has no code host"
    status = host.get("capabilities", {}).get(capability)
    if not status:
        return None, f"the code host {host['adapter']} has no {capability}"
    if status["via"] != "script":
        return None, f"{capability} of {host['adapter']} is via {status['via']}; the core runs only scripts"
    if status["status"] != "available":
        return None, f"{capability} of {host['adapter']} is {status['status']}: {status.get('reason')}"
    return host, None


def repo_entry(repo):
    return {"name": repo["repo"], "path": repo["path"], "remote": git_origin(repo["path"])}


def read_checks(project, repo):
    host, reason = host_capability(project, "pr_checks")
    if not host:
        return {"checks": "unchecked", "reason": reason}
    outcome = adapters.run_capability(project, host, "pr_checks", repo_entry(repo), {"pr_url": repo["pr_url"]})
    if outcome["status"] == "ok":
        return outcome["data"]
    if outcome["status"] == "credentials":
        return {"checks": "unchecked", "reason": f"pr_checks of {host['adapter']}: credentials missing"}
    return {"checks": "unknown", "reason": outcome["reason"]}


def is_waiting(found):
    return found.get("state") == "OPEN" and (found["checks"] == WAITING or found.get("mergeable") == "UNKNOWN")


def with_prs(conn, run_id, repo_name=None):
    found = [repo for repo in runs.run_repos(conn, run_id) if repo["pr_url"]]
    if repo_name:
        found = [repo for repo in found if repo["repo"] == repo_name]
        if not found:
            raise FactoryError(f"run {run_id} has no PR for repo {repo_name}")
    return found


def check(conn, run_id, repo_name=None, wait=0):
    from . import after

    run = runs.get(conn, run_id)
    project = runs.current_project(run)
    selected = with_prs(conn, run_id, repo_name)
    deadline = time.monotonic() + max(wait or 0, 0)
    while True:
        results = []
        for repo in selected:
            found = read_checks(project, repo)
            results.append(dict(found, repo=repo["repo"], url=repo["pr_url"], after=after.todo(project, run_id, repo["repo"]),
                                next=work(conn, run, repo, project, found.get("state"))))
        waiting = [found["repo"] for found in results if is_waiting(found)]
        left = deadline - time.monotonic()
        if not waiting or left <= 0:
            break
        time.sleep(min(POLL, left))
    return {"run": run_id, "project": project["name"], "mode": mode_of(project), "repos": results, "waiting": waiting}


def land_dir(run_id, repo_name):
    return os.path.join(runs.run_dir(run_id), "land", repo_name)


def rounds(run_id, repo_name):
    folder = land_dir(run_id, repo_name)
    found = [ROUND_RE.match(name) for name in os.listdir(folder)] if os.path.isdir(folder) else []
    return sorted(int(match.group(1)) for match in found if match)


def round_paths(run_id, repo_name, number):
    folder = os.path.join(land_dir(run_id, repo_name), f"round-{number}")
    return {"round_dir": folder, "review_file": os.path.join(folder, "review.md"),
            "checks_file": os.path.join(folder, "checks.json")}


def repo_test(project, repo_name):
    entry = next((repo for repo in project.get("repos", []) if repo["name"] == repo_name), None)
    return (entry or {}).get("test") or "none"


def start(conn, run_id, repo_name, by="human"):
    by = connected.allow(conn, by, "review")
    run = runs.get(conn, run_id)
    if run["status"] != "done":
        raise FactoryError(f"run {run_id} is {run['status']}; land a run only when it is done")
    repo = with_prs(conn, run_id, repo_name)[0]
    project = runs.current_project(run)
    worktree = repo["feature_worktree"]
    if not os.path.isdir(worktree):
        raise FactoryError(f"the feature worktree {worktree} is gone; recreate it as in Resume, then land again")
    local = git(["rev-parse", "HEAD"], worktree)
    found = read_checks(project, repo)
    if found.get("state") not in (None, "OPEN"):
        raise FactoryError(f"the PR of {repo_name} is {found['state']}; only an open PR lands")
    head = found.get("head_sha") or local
    if head != local:
        raise FactoryError(f"the PR head {head[:12]} is not the feature worktree's HEAD {local[:12]}; "
                           "push the feature branch or bring the worktree up to date, then land again")
    done = rounds(run_id, repo_name)
    number = (done[-1] if done else 0) + 1
    paths = round_paths(run_id, repo_name, number)
    os.makedirs(paths["round_dir"])
    write_json(paths["checks_file"], dict(found, repo=repo_name, url=repo["pr_url"]))
    pr_file = os.path.join(runs.run_dir(run_id), f"pr-{repo_name}.md")
    previous = [round_paths(run_id, repo_name, earlier)["round_dir"] for earlier in done]
    with db.tx(conn):
        db.event(conn, "land.start", run_id, repo=repo_name, round=number, head_sha=head, checks=found["checks"],
                 mode=mode_of(project), by=by)
    return {
        "mode": mode_of(project),
        "repo": repo_name,
        "pr_url": repo["pr_url"],
        "pr_file": pr_file if os.path.isfile(pr_file) else "none",
        "feature_worktree": worktree,
        "feature_branch": run["feature_branch"],
        "base_branch": repo["base_branch"],
        "head_sha": head,
        "test": repo_test(project, repo_name),
        "checks": found["checks"],
        "round": number,
        "previous": ",".join(previous) or "none",
        **paths,
    }


def last_event(conn, run_id, kind, repo_name, round_number=None):
    for at, payload in conn.execute(
            "SELECT at, payload FROM events WHERE run_id = ? AND kind = ? ORDER BY id DESC", (run_id, kind)):
        data = json.loads(payload)
        if data.get("repo") == repo_name and round_number in (None, data.get("round")) and not data.get("dry_run"):
            return dict(data, at=at)
    return None


def merged_lately(conn, run_id, repo_name):
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=FRESH_HOURS)).strftime("%Y-%m-%dT%H:%M:%S")
    for kind, field, value in (("run.pr_state", "state", "MERGED"), ("land.apply", "action", "merged")):
        found = last_event(conn, run_id, kind, repo_name)
        if found and found.get(field) == value and found["at"] >= since:
            return True
    return False


def last_start(conn, run_id, repo_name):
    found = last_event(conn, run_id, "land.start", repo_name)
    if not found:
        raise FactoryError(f"repo {repo_name} of run {run_id} has no land round; run land start first")
    return found


def work(conn, run, repo, project, state=None):
    """What a PR waits for next, and which role does it: None when it waits for a person or for nothing."""
    from . import after

    run_id, repo_name = run["id"], repo["repo"]

    def waits(next_step, role, reason, **extra):
        return dict({"next": next_step, "role": role, "reason": reason}, **extra)

    state = state or repo.get("pr_state") or "OPEN"
    if state == "MERGED":
        todo = after.todo(project, run_id, repo_name)
        if todo and not os.path.isfile(after.state_file(run_id, repo_name)) and not merged_lately(conn, run_id, repo_name):
            return waits("none", None, f"merged more than {FRESH_HOURS} hours ago, before any step after merge ran")
        if todo:
            return waits("deploy", "deploy", f"merged; steps after merge left: {', '.join(todo)}", steps=todo)
        return waits("none", None, "merged")
    if state != "OPEN":
        return waits("none", None, f"the PR is {state}")
    started = last_event(conn, run_id, "land.start", repo_name)
    if run["status"] != "done":
        fixing = started and last_event(conn, run_id, "land.fix", repo_name, started["round"])
        if fixing:
            return waits("fixing", None, f"job {fixing['job']} fixes land round {started['round']} "
                                         f"(fix {fixing['fix']} of {fixing['fixes']})", round=started["round"])
        return waits("none", None, f"the run is {run['status']}")
    if not started:
        return waits("review", "review", "no land review yet", round=None)
    paths = round_paths(run_id, repo_name, started["round"])
    verdict = verdict_of(paths["review_file"])
    seen = dict(round=started["round"], verdict=verdict, review_file=paths["review_file"], head_sha=started["head_sha"])
    if verdict in ("MISSING", "INVALID"):
        return waits("review", "review", f"land round {started['round']} has no verdict yet",
                     busy={"by": started.get("by", "human"), "at": started["at"]}, **seen)
    head = git(["rev-parse", "HEAD"], repo["feature_worktree"], check=False) if os.path.isdir(repo["feature_worktree"]) else None
    if head and head != started["head_sha"]:
        return waits("review", "review", f"new commits since land round {started['round']}", **seen)
    mode = mode_of(project)
    if verdict == "CHANGES":
        reason = fixable(conn, run_id, repo_name, project, paths["review_file"])
        if reason:
            return waits("decide", None, reason, **seen)
        items = must_fix(paths["review_file"])
        return waits("fix", "fix", f"{len(items)} Must fix item(s); fix {fixes_done(conn, run_id, repo_name) + 1} "
                                   f"of {fixes_of(project)}", **seen)
    applied = last_event(conn, run_id, "land.apply", repo_name, started["round"])
    if mode == "off":
        return waits("merge", None, f"the review passed; landing is off for project {project['name']}, "
                                    "so a person merges the PR", **seen)
    if applied and applied["action"] == "ready":
        return waits("merge", None, "marked ready for review; a person merges the PR", **seen)
    reasons = (applied or {}).get("reasons") or []
    return waits("merge", "merge", "; ".join(reasons) or "the review passed", **seen)


def save(conn, run_id, repo_name, text, by="human"):
    by = connected.allow(conn, by, "review")
    runs.get(conn, run_id)
    started = last_start(conn, run_id, repo_name)
    path = round_paths(run_id, repo_name, started["round"])["review_file"]
    write_text(path, text if text.endswith("\n") else text + "\n")
    with db.tx(conn):
        db.event(conn, "land.save", run_id, repo=repo_name, round=started["round"], by=by)
    return {"saved": path}


def verdict_of(path):
    if not os.path.isfile(path):
        return "MISSING"
    lines = [line for line in read_text(path).splitlines() if line.strip()]
    match = VERDICT_RE.match(lines[0]) if lines else None
    return match.group(1) if match else "INVALID"


def blockers(project, mode, verdict, found, head):
    if verdict != "PASS":
        return [f"the review says {verdict}"]
    if mode == "off":
        return [f"landing is off for project {project['name']} (defaults.land)"]
    if found["checks"] in ("unchecked", "unknown"):
        return [f"the PR checks are {found['checks']}: {found.get('reason')}"]
    if found["state"] != "OPEN":
        return [f"the PR is {found['state']}"]
    reasons = []
    if found["head_sha"] != head:
        reasons.append("the PR has commits the review did not see; land again")
    if found["checks"] == "failing":
        reasons.append(f"checks fail: {', '.join(found['failing'])}")
    if found["checks"] == WAITING:
        reasons.append(f"checks still run: {', '.join(found['pending'])}")
    if found["mergeable"] == "CONFLICTING":
        reasons.append("the PR conflicts with its base branch")
    if found["mergeable"] == "UNKNOWN":
        reasons.append("the code host has not yet said whether the PR merges cleanly")
    if found.get("review") == "CHANGES_REQUESTED":
        reasons.append("a reviewer asked for changes on the PR")
    if mode == "ready" and not found["draft"] and not reasons:
        reasons.append("the PR is already ready for review")
    return reasons


def apply(conn, run_id, repo_name, dry_run=False, by="human"):
    from . import after

    by = connected.check_author(by) if dry_run else connected.allow(conn, by, "merge")
    run = runs.get(conn, run_id)
    project = runs.current_project(run)
    repo = with_prs(conn, run_id, repo_name)[0]
    started = last_start(conn, run_id, repo_name)
    paths = round_paths(run_id, repo_name, started["round"])
    verdict = verdict_of(paths["review_file"])
    if verdict in ("MISSING", "INVALID"):
        return {"repo": repo_name, "verdict": verdict, "action": "none", "review_file": paths["review_file"],
                "reasons": ["the review file is missing" if verdict == "MISSING"
                            else "the review file's first line is not VERDICT: PASS or VERDICT: CHANGES"]}
    mode = mode_of(project)
    found = read_checks(project, repo) if verdict == "PASS" and mode != "off" else {"checks": "unchecked"}
    reasons = blockers(project, mode, verdict, found, started["head_sha"])
    action = "none"
    wanted = "merge" if mode == "merge" else "ready"
    if not reasons:
        host, reason = host_capability(project, "land_pr")
        if not host:
            reasons.append(reason)
        elif dry_run:
            action = f"would_{wanted}"
        else:
            outcome = adapters.run_capability(project, host, "land_pr", repo_entry(repo),
                                              {"pr_url": repo["pr_url"], "action": wanted, "head_sha": started["head_sha"]})
            if outcome["status"] == "ok":
                action = outcome["data"]["action"]
            else:
                reasons.append(f"land_pr of {host['adapter']}: {outcome['reason']}")
    with db.tx(conn):
        db.event(conn, "land.apply", run_id, repo=repo_name, round=started["round"], verdict=verdict, mode=mode,
                 action=action, reasons=reasons, dry_run=dry_run, by=by)
    result = {"repo": repo_name, "url": repo["pr_url"], "mode": mode, "verdict": verdict, "action": action,
              "reasons": reasons, "round": started["round"], "review_file": paths["review_file"], "issues_done": [],
              "after": after.todo(project, run_id, repo_name) if action in ("merged", "would_merge") else []}
    if action == "merged":
        result["issues_done"] = runs.check_prs(conn, run_id, force=True)["issues_done"]
    return result


def must_fix(path):
    found, inside = [], False
    for line in read_text(path).splitlines():
        if line.startswith("## "):
            inside = line.strip().lower() == "## must fix"
        elif inside and line.lstrip().startswith("- "):
            found.append(line.lstrip()[2:].strip())
    return found


def fixes_done(conn, run_id, repo_name):
    return conn.execute(
        "SELECT COUNT(*) FROM events WHERE run_id = ? AND kind = 'land.fix' AND json_extract(payload, '$.repo') = ?",
        (run_id, repo_name),
    ).fetchone()[0]


def fixable(conn, run_id, repo_name, project, review_file):
    """Why a CHANGES review cannot be fixed by the factory itself, or None when it can."""
    items = must_fix(review_file)
    human = [item for item in items if item.startswith("[human]")]
    if human:
        return f"the review has {len(human)} point(s) a person must decide first"
    if not items:
        return "the review says CHANGES but lists no Must fix item"
    allowed, done = fixes_of(project), fixes_done(conn, run_id, repo_name)
    if done >= allowed:
        return f"the factory already fixed this PR {done} time(s), the most defaults.land_fixes allows ({allowed})"
    return None


def fix_job(conn, run_id, repo_name, job_id):
    found = [job for job in jobs.job_rows(conn, run_id) if job["kind"] == "code" and job["repo"] == repo_name]
    if job_id:
        found = [job for job in found if job["id"] == job_id]
        if not found:
            raise FactoryError(f"run {run_id} has no code job {job_id} in repo {repo_name}")
    found = [job for job in found if job["stage"] in ("done", "dropped")]
    if not found:
        raise FactoryError(f"repo {repo_name} of run {run_id} has no done code job to rework")
    done = [job for job in found if job["stage"] == "done"]
    return (done or found)[-1]


def fix(conn, run_id, repo_name, job_id=None, by="human"):
    by = connected.allow(conn, by, "fix")
    run = runs.get(conn, run_id)
    project = runs.current_project(run)
    with_prs(conn, run_id, repo_name)
    started = last_start(conn, run_id, repo_name)
    review_file = round_paths(run_id, repo_name, started["round"])["review_file"]
    verdict = verdict_of(review_file)
    if verdict != "CHANGES":
        raise FactoryError(f"land round {started['round']} of {repo_name} says {verdict}; land fix acts on CHANGES")
    for (payload,) in conn.execute("SELECT payload FROM events WHERE run_id = ? AND kind = 'land.fix'", (run_id,)):
        data = json.loads(payload)
        if data.get("repo") == repo_name and data.get("round") == started["round"]:
            raise FactoryError(f"land round {started['round']} of {repo_name} is already being fixed by job {data['job']}; "
                               "land again once the run is done")
    reason = fixable(conn, run_id, repo_name, project, review_file)
    if reason:
        raise FactoryError(f"{reason}; a person decides, then /factory rework")
    job = fix_job(conn, run_id, repo_name, job_id)
    items = "\n".join(f"- {item}" for item in must_fix(review_file))
    note = (f"The review of the whole PR asks for changes before it lands (land round {started['round']}). "
            f"Fix every Must fix item below, run the repo's full test suite, and commit. "
            f"The whole review is in {review_file}.\n\n## Must fix\n\n{items}")
    shown = jobs.rework(conn, f"{run_id}/{job['id']}", note)
    number = fixes_done(conn, run_id, repo_name) + 1
    with db.tx(conn):
        db.event(conn, "land.fix", run_id, job["id"], repo=repo_name, round=started["round"], job=job["id"], fix=number,
                 fixes=fixes_of(project), by=by)
    return {"repo": repo_name, "round": started["round"], "job": job["id"], "fix": number, "fixes": fixes_of(project),
            "review_file": review_file, "rework": shown}
