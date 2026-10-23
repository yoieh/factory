import datetime
import os
import re
import shutil
import subprocess

from . import FactoryError, adapters, db, home, land, runs
from .util import git, now, read_json, read_text, write_json, write_text

BUILD_TIMEOUT = 3600
PUBLISH_TIMEOUT = 1800
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
WATCH_RE = re.compile(r"^DEPLOY: (VERIFIED|UNVERIFIED|NONE|FAILED|RESTORED|ESCALATED|UNKNOWN)\s*$")
STEPS = ("watch", "build", "release", "package")


def settings_of(project, repo_name):
    entry = next((repo for repo in project.get("repos", []) if repo["name"] == repo_name), None)
    return (entry or {}).get("after_merge") or {}


def steps_of(settings):
    wanted = {
        "watch": bool(settings.get("watch")),
        "build": bool(settings.get("build")),
        "release": settings.get("release", "off") != "off",
        "package": settings.get("package", "off") != "off",
    }
    return [step for step in STEPS if wanted[step]]


def after_dir(run_id, repo_name):
    return os.path.join(land.land_dir(run_id, repo_name), "after")


def state_file(run_id, repo_name):
    return os.path.join(after_dir(run_id, repo_name), "after.json")


def build_worktree(run_id, repo_name):
    return home.path("worktrees", run_id, "_after", repo_name)


def todo(project, run_id, repo_name):
    path = state_file(run_id, repo_name)
    if os.path.isfile(path):
        state = read_json(path)
        return [step for step in state["order"] if state["steps"][step]["status"] != "done"]
    return steps_of(settings_of(project, repo_name))


def load(conn, run_id, repo_name):
    run = runs.get(conn, run_id)
    path = state_file(run_id, repo_name)
    if not os.path.isfile(path):
        raise FactoryError(f"repo {repo_name} of run {run_id} has no steps after merge yet; run land after first")
    return run, read_json(path)


def store(run_id, repo_name, state):
    write_json(state_file(run_id, repo_name), state)


def pr_title(run_id, repo_name, fallback):
    path = os.path.join(runs.run_dir(run_id), f"pr-{repo_name}.md")
    lines = read_text(path).splitlines() if os.path.isfile(path) else []
    first = lines[0].strip() if lines else ""
    return first[2:].strip() if first.startswith("# ") else first or fallback


def ensure_worktree(repo, run_id, merge_sha):
    worktree = build_worktree(run_id, repo["repo"])
    if os.path.isdir(worktree):
        return worktree
    git(["fetch", "origin", repo["base_branch"]], repo["path"])
    if git(["cat-file", "-e", f"{merge_sha}^{{commit}}"], repo["path"], check=False) is None:
        raise FactoryError(f"the merge commit {merge_sha[:12]} is not on origin/{repo['base_branch']} of {repo['repo']}")
    os.makedirs(os.path.dirname(worktree), exist_ok=True)
    git(["worktree", "add", "--detach", worktree, merge_sha], repo["path"])
    return worktree


def describe(run_id, repo, state):
    settings = state["settings"]
    folder = after_dir(run_id, repo["repo"])
    checkout = repo["feature_worktree"] if os.path.isdir(repo["feature_worktree"]) else repo["path"]
    return {
        "repo": repo["repo"],
        "pr_url": repo["pr_url"],
        "base_branch": repo["base_branch"],
        "merge_sha": state["merge_sha"],
        "tag": state["tag"],
        "steps": {step: state["steps"][step]["status"] for step in state["order"]},
        "todo": [step for step in state["order"] if state["steps"][step]["status"] != "done"],
        "after_dir": folder,
        "checkout": checkout,
        "watch_file": os.path.join(folder, "watch.md"),
        "build": settings.get("build") or "none",
        "build_dir": build_worktree(run_id, repo["repo"]) if "build" in state["order"] else "none",
        "build_log": os.path.join(folder, "build.log"),
        "artifacts": state["steps"].get("build", {}).get("artifacts") or [],
        "release": settings.get("release", "off"),
        "package": settings.get("package", "off"),
    }


def start(conn, run_id, repo_name):
    run = runs.get(conn, run_id)
    project = runs.current_project(run)
    repo = land.with_prs(conn, run_id, repo_name)[0]
    path = state_file(run_id, repo_name)
    if os.path.isfile(path):
        state = read_json(path)
        if state["pr_url"] == repo["pr_url"]:
            if "build" in state["order"] and state["steps"]["build"]["status"] != "done":
                ensure_worktree(repo, run_id, state["merge_sha"])
            return describe(run_id, repo, state)
        folder = after_dir(run_id, repo_name)
        os.rename(folder, f"{folder}-{state['merge_sha'][:7]}")
        stale = build_worktree(run_id, repo_name)
        if os.path.isdir(stale):
            git(["worktree", "remove", "--force", stale], repo["path"], check=False)
    settings = settings_of(project, repo_name)
    order = steps_of(settings)
    if not order:
        raise FactoryError(f"repo {repo_name} of project {project['name']} has no after_merge steps")
    found = land.read_checks(project, repo)
    if found.get("state") != "MERGED":
        why = found.get("state") or f"{found['checks']}: {found.get('reason')}"
        raise FactoryError(f"the PR of {repo_name} is {why}; the steps after merge run once it is merged")
    merge_sha = found.get("merge_sha") or ""
    if not SHA_RE.match(merge_sha):
        raise FactoryError(f"the code host did not say which commit the merge of {repo_name} made")
    if "build" in order:
        ensure_worktree(repo, run_id, merge_sha)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d")
    title = pr_title(run_id, repo_name, repo["pr_url"])
    folder = after_dir(run_id, repo_name)
    notes = os.path.join(folder, "notes.md")
    write_text(notes, f"{title}\n\nMerged in {repo['pr_url']} as {merge_sha}.\n")
    state = {
        "repo": repo_name,
        "pr_url": repo["pr_url"],
        "merge_sha": merge_sha,
        "tag": f"build-{stamp}-{merge_sha[:7]}",
        "title": f"{repo_name} {merge_sha[:7]}: {title}",
        "notes_file": notes,
        "settings": settings,
        "order": order,
        "steps": {step: {"status": "todo"} for step in order},
        "created_at": now(),
    }
    store(run_id, repo_name, state)
    with db.tx(conn):
        db.event(conn, "land.after", run_id, repo=repo_name, merge_sha=merge_sha, steps=order)
    return describe(run_id, repo, state)


def shell(command, cwd, log, timeout):
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(f"$ {command}\n")
        handle.flush()
        try:
            done = subprocess.run(["bash", "-c", command], cwd=cwd, stdout=handle, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, timeout=timeout)
        except subprocess.TimeoutExpired:
            return f"{command} ran longer than {timeout} seconds"
    return None if done.returncode == 0 else f"{command} exited with {done.returncode}"


def wanted_artifacts(settings):
    found = settings.get("artifact")
    if not found:
        return []
    return found if isinstance(found, list) else [found]


def keep_artifact(source, folder, repo_name):
    if os.path.isdir(source):
        base = os.path.join(folder, f"{repo_name}-{os.path.basename(source.rstrip(os.sep))}")
        return shutil.make_archive(base, "zip", root_dir=source)
    target = os.path.join(folder, os.path.basename(source))
    shutil.copyfile(source, target)
    return target


def build(conn, run_id, repo_name, timeout=None):
    run, state = load(conn, run_id, repo_name)
    if "build" not in state["order"]:
        raise FactoryError(f"repo {repo_name} has no build after merge")
    step = state["steps"]["build"]
    if step["status"] == "done":
        return {"repo": repo_name, "status": "done", "artifacts": step.get("artifacts") or [], "reason": None}
    repo = land.with_prs(conn, run_id, repo_name)[0]
    project = runs.current_project(run)
    worktree = ensure_worktree(repo, run_id, state["merge_sha"])
    folder = after_dir(run_id, repo_name)
    log = os.path.join(folder, "build.log")
    write_text(log, f"build of {repo_name} at {state['merge_sha']}, {now()}\n")
    entry = next((item for item in project.get("repos", []) if item["name"] == repo_name), {})
    commands = [command for command in (entry.get("setup"), state["settings"]["build"]) if command]
    reason = None
    for command in commands:
        reason = shell(command, worktree, log, timeout or BUILD_TIMEOUT)
        if reason:
            break
    artifacts = []
    missing = [path for path in wanted_artifacts(state["settings"]) if not os.path.exists(os.path.join(worktree, path))]
    if not reason and missing:
        reason = f"the build left no {', '.join(missing)}"
    if not reason:
        artifacts = [keep_artifact(os.path.join(worktree, path), folder, repo_name)
                     for path in wanted_artifacts(state["settings"])]
    if reason:
        step.update(status="failed", reason=reason)
    else:
        step.update(status="done", reason=None, artifacts=artifacts, built_at=now())
        git(["worktree", "remove", "--force", worktree], repo["path"], check=False)
        git(["worktree", "prune"], repo["path"], check=False)
    store(run_id, repo_name, state)
    with db.tx(conn):
        db.event(conn, "land.build", run_id, repo=repo_name, status=step["status"], reason=reason, artifacts=artifacts)
    return {"repo": repo_name, "status": step["status"], "artifacts": artifacts, "reason": reason,
            "build_log": log, "build_dir": worktree if reason else "removed"}


def publish(conn, run_id, repo_name, dry_run=False):
    run, state = load(conn, run_id, repo_name)
    wanted = [step for step in ("release", "package") if step in state["order"]]
    if not wanted:
        raise FactoryError(f"repo {repo_name} publishes no release or package after merge")
    built = state["steps"]["build"]
    artifacts = built.get("artifacts") or []
    if built["status"] != "done" or not artifacts or not all(os.path.isfile(path) for path in artifacts):
        raise FactoryError(f"repo {repo_name} has no build to publish; run land build first")
    project = runs.current_project(run)
    repo = land.with_prs(conn, run_id, repo_name)[0]
    result = {"repo": repo_name, "tag": state["tag"], "artifacts": artifacts}
    for step in wanted:
        entry = state["steps"][step]
        if entry["status"] == "done":
            result[step] = {"action": "done", "where": entry.get("where")}
            continue
        capability = f"publish_{step}"
        host, reason = land.host_capability(project, capability)
        if not host:
            result[step] = {"action": "none", "reason": reason}
            continue
        if dry_run:
            result[step] = {"action": f"would_{step}", "mode": state["settings"].get(step)}
            continue
        extra = {"pr_url": repo["pr_url"], "tag": state["tag"], "merge_sha": state["merge_sha"],
                 "artifacts": ",".join(artifacts), "title": state["title"], "notes_file": state["notes_file"],
                 "release_mode": state["settings"].get("release", "off")}
        outcome = adapters.run_capability(project, host, capability, land.repo_entry(repo), extra, PUBLISH_TIMEOUT)
        if outcome["status"] == "ok":
            where = outcome["data"].get("url") or outcome["data"].get("ref")
            entry.update(status="done", reason=None, where=where, done_at=now())
            result[step] = {"action": outcome["data"]["action"], "where": where}
        else:
            why = outcome["reason"]
            if outcome["status"] == "credentials":
                why += f"; see Setup of the {host['adapter']} adapter"
            entry.update(status="failed", reason=f"{capability} of {host['adapter']}: {why}")
            result[step] = {"action": "none", "reason": entry["reason"]}
    if not dry_run:
        store(run_id, repo_name, state)
        with db.tx(conn):
            db.event(conn, "land.publish", run_id, repo=repo_name, tag=state["tag"],
                     **{step: result[step]["action"] for step in wanted})
    return result


def verdict_of(path):
    if not os.path.isfile(path):
        return "MISSING", None
    lines = [line for line in read_text(path).splitlines() if line.strip()]
    match = WATCH_RE.match(lines[0]) if lines else None
    if not match:
        return "INVALID", None
    return match.group(1), lines[1].strip() if len(lines) > 1 else ""


def save_watch(conn, run_id, repo_name, text):
    load(conn, run_id, repo_name)
    path = os.path.join(after_dir(run_id, repo_name), "watch.md")
    write_text(path, text if text.endswith("\n") else text + "\n")
    return {"saved": path}


def watched(conn, run_id, repo_name):
    _, state = load(conn, run_id, repo_name)
    if "watch" not in state["order"]:
        raise FactoryError(f"repo {repo_name} has no deploy watch after merge")
    path = os.path.join(after_dir(run_id, repo_name), "watch.md")
    verdict, conclusion = verdict_of(path)
    if verdict in ("MISSING", "INVALID"):
        return {"repo": repo_name, "verdict": verdict, "watch_file": path,
                "reason": "the watch file is missing" if verdict == "MISSING"
                else "the watch file's first line is not DEPLOY: <verdict>"}
    state["steps"]["watch"].update(status="done", verdict=verdict, conclusion=conclusion, done_at=now())
    store(run_id, repo_name, state)
    with db.tx(conn):
        db.event(conn, "land.watch", run_id, repo=repo_name, verdict=verdict, merge_sha=state["merge_sha"])
    return {"repo": repo_name, "verdict": verdict, "conclusion": conclusion, "watch_file": path}
