import json
import os
import re
import time

from . import FactoryError, adapters, db, runs
from .util import git, git_origin, read_text, write_json, write_text

POLL = 30
VERDICT_RE = re.compile(r"^VERDICT: (PASS|CHANGES)\s*$")
ROUND_RE = re.compile(r"^round-([0-9]+)$")
WAITING = "pending"


def mode_of(project):
    return (project.get("defaults") or {}).get("land", "off")


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
        results = [dict(read_checks(project, repo), repo=repo["repo"], url=repo["pr_url"],
                        after=after.todo(project, run_id, repo["repo"])) for repo in selected]
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


def start(conn, run_id, repo_name):
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
                 mode=mode_of(project))
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


def last_start(conn, run_id, repo_name):
    for (payload,) in conn.execute(
            "SELECT payload FROM events WHERE run_id = ? AND kind = 'land.start' ORDER BY id DESC", (run_id,)):
        data = json.loads(payload)
        if data.get("repo") == repo_name:
            return data
    raise FactoryError(f"repo {repo_name} of run {run_id} has no land round; run land start first")


def save(conn, run_id, repo_name, text):
    runs.get(conn, run_id)
    started = last_start(conn, run_id, repo_name)
    path = round_paths(run_id, repo_name, started["round"])["review_file"]
    write_text(path, text if text.endswith("\n") else text + "\n")
    with db.tx(conn):
        db.event(conn, "land.save", run_id, repo=repo_name, round=started["round"])
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


def apply(conn, run_id, repo_name, dry_run=False):
    from . import after

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
                 action=action, reasons=reasons, dry_run=dry_run)
    result = {"repo": repo_name, "url": repo["pr_url"], "mode": mode, "verdict": verdict, "action": action,
              "reasons": reasons, "round": started["round"], "review_file": paths["review_file"], "issues_done": [],
              "after": after.todo(project, run_id, repo_name) if action in ("merged", "would_merge") else []}
    if action == "merged":
        result["issues_done"] = runs.check_prs(conn, run_id, force=True)["issues_done"]
    return result
