import datetime
import json
import os

from . import FactoryError, adapters, db, home, issues, projects
from .util import git, git_origin, git_toplevel, now, write_json, write_text

STATUSES = ("open", "accepted", "rejected", "done")
MOVES = {
    "open": ("accepted", "rejected"),
    "accepted": ("done", "rejected", "open"),
    "rejected": ("open",),
    "done": ("open",),
}
ISSUE_STATUS = {"accepted": "open", "rejected": "closed", "done": "done", "open": "open"}


def decoded(item):
    if item is None:
        return None
    item = dict(item)
    item["seen_in"] = json.loads(item["seen_in"])
    return item


def get(conn, improvement_id):
    item = decoded(db.one(conn.execute("SELECT * FROM improvements WHERE id = ?", (improvement_id,))))
    if not item:
        raise FactoryError(f"no improvement {improvement_id}")
    return item


def listing(conn, run_id=None, project=None, statuses=None):
    clauses, values = [], []
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)
    if project:
        clauses.append("project = ?")
        values.append(project)
    if statuses:
        for status in statuses:
            if status not in STATUSES:
                raise FactoryError(f"--status must be one of {', '.join(STATUSES)}", code=2)
        clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
        values.extend(statuses)
    sql = "SELECT * FROM improvements"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return [decoded(row) for row in db.rows(conn.execute(sql + " ORDER BY id", values))]


def event(conn, kind, item, **payload):
    db.event(conn, kind, None, None, improvement=item["id"], **payload)


def issue_project(conn, item):
    config = home.load_config()
    if item["target"] == "factory" and config.get("factory_project") and projects.registered(conn, config["factory_project"]):
        return config["factory_project"]
    return item["project"]


def issue_body(item):
    return "\n".join([
        item["proposal"],
        "",
        f"Evidence: {item['evidence']}",
        f"Where: {item['location'] or 'not given'}",
        f"Target: {item['target']}" + (f" ({item['repo']})" if item["repo"] else ""),
        f"From run {item['run_id']}, improvement {item['id']}.",
        "",
    ])


def decide(conn, improvement_id, status, note=None):
    if status not in STATUSES:
        raise FactoryError(f"the decision must be one of {', '.join(STATUSES)}", code=2)
    with db.tx(conn):
        item = get(conn, improvement_id)
        if item["status"] == status:
            return dict(get(conn, improvement_id), changed=False)
        if status not in MOVES[item["status"]]:
            raise FactoryError(f"improvement {improvement_id} is {item['status']}; it cannot move to {status}")
        fields = {"status": status, "decided_at": None if status == "open" else now()}
        if note is not None:
            fields["note"] = note.strip() or None
        if status == "open":
            fields.update(pr_url=None, branch=None)
        assignments = ", ".join(f"{key} = ?" for key in fields)
        conn.execute(f"UPDATE improvements SET {assignments} WHERE id = ?", (*fields.values(), improvement_id))
        event(conn, "improvement.decide", item, **{"from": item["status"], "to": status})
        issue_key = item["issue_key"]
        if status == "accepted" and not issue_key:
            created = issues.add(conn, issue_project(conn, item), item["title"], type_="improvement", body=issue_body(item))
            issue_key = created["key"]
            conn.execute("UPDATE improvements SET issue_key = ? WHERE id = ?", (issue_key, improvement_id))
            conn.execute("INSERT OR IGNORE INTO issue_links (issue_key, kind, ref, created_at) VALUES (?, 'improvement', ?, ?)",
                         (issue_key, str(improvement_id), now()))
            issues.event(conn, "issue.link", issue_key, link="improvement", ref=str(improvement_id))
        elif issue_key:
            issues.move(conn, issue_key, ISSUE_STATUS[status], by="improvement", improvement=improvement_id)
    return dict(get(conn, improvement_id), changed=True)


def project_of(conn, item):
    name = issue_project(conn, item) if item["target"] == "factory" else item["project"]
    try:
        return projects.resolved(projects.load(name))
    except FactoryError:
        return None


def current_branch(path):
    return git(["symbolic-ref", "--quiet", "--short", "HEAD"], path, check=False) or None


def factory_group_project(conn, item, source):
    config = home.load_config()
    name = config.get("factory_project")
    if name and projects.registered(conn, name):
        project = projects.load(name)
        for repo in project["repos"]:
            if git_toplevel(repo["path"]) and os.path.realpath(git_toplevel(repo["path"])) == os.path.realpath(source):
                return projects.resolved(project), repo
    owner = projects.registered(conn, item["project"])
    synthetic = {"name": item["project"], "key": owner["key"] if owner else "FACTORY", "repos": [{"name": "factory", "path": source}]}
    return projects.resolved(projects.normalise(synthetic)), None


def location(conn, item, config, factory_base):
    if item["target"] == "process":
        return {"process": True}
    if item["target"] == "factory":
        source = config.get("factory_source")
        if not source:
            return {"skip": "no factory source (config.json factory_source)"}
        top = git_toplevel(source)
        if not top:
            return {"skip": f"the factory source {source} is not a git checkout"}
        base = factory_base or current_branch(top)
        if not base:
            return {"skip": "the factory source has no current branch; pass --factory-base"}
        project, repo = factory_group_project(conn, item, top)
        setup = (repo or {}).get("setup")
        return {"repo": "factory", "path": top, "base": base, "project": project, "setup": setup}
    try:
        project = projects.resolved(projects.load(item["project"]))
    except FactoryError as error:
        return {"skip": error.message}
    spec = project["improvement_targets"].get(item["target"])
    if spec is None:
        return {"skip": f"target {item['target']} is no longer in the improvement targets of {item['project']}"}
    name = item["repo"] or spec.get("repo")
    repo = next((entry for entry in project["repos"] if entry["name"] == name), None)
    if not repo:
        return {"skip": f"repo {name} is not linked to {item['project']}"}
    if not repo["is_git"]:
        return {"skip": f"repo {name} at {repo['path']} is not a git checkout"}
    wanted = spec.get("base", "default")
    if wanted == "default":
        base = repo["default_branch"]
    elif wanted == "current":
        base = current_branch(repo["path"])
    else:
        base = wanted
    if not base:
        return {"skip": f"no base branch for repo {name}"}
    return {"repo": name, "path": repo["path"], "base": base, "project": project, "setup": repo.get("setup")}


def items_markdown(repo, items):
    lines = [f"# Improvements for {repo}", ""]
    for item in items:
        lines += [
            f"## {item['id']}: {item['title']}",
            "",
            f"- Target: {item['target']}" + (f" ({item['repo']})" if item["repo"] else ""),
            f"- Where: {item['location'] or 'not given'}",
            f"- Evidence: {item['evidence']}",
            f"- Proposal: {item['proposal']}",
            f"- Note from the human: {item['note'] or 'none'}",
            "",
        ]
    return "\n".join(lines)


def batch_id():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate, number = stamp, 2
    while os.path.exists(home.path("improve", candidate)):
        candidate = f"{stamp}-{number}"
        number += 1
    return candidate


def start(conn, ids=None, factory_base=None):
    config = home.load_config()
    if ids:
        chosen = [get(conn, improvement_id) for improvement_id in ids]
    else:
        chosen = listing(conn, statuses=["accepted"])
    groups, process, skipped = {}, [], []
    for item in chosen:
        if item["status"] != "accepted":
            skipped.append({"id": item["id"], "reason": f"it is {item['status']}, not accepted"})
            continue
        if item["pr_url"]:
            skipped.append({"id": item["id"], "reason": f"it already has a PR: {item['pr_url']}"})
            continue
        found = location(conn, item, config, factory_base)
        if found.get("process"):
            process.append({"id": item["id"], "title": item["title"], "proposal": item["proposal"], "project": item["project"]})
        elif "skip" in found:
            skipped.append({"id": item["id"], "reason": found["skip"]})
        else:
            key = (os.path.realpath(found["path"]), found["base"])
            groups.setdefault(key, {"found": found, "items": []})["items"].append(item)
    if not groups:
        return {"batch": None, "groups": [], "process": process, "skipped": skipped}
    batch = batch_id()
    result = []
    folders, branches = set(), {}
    for (path, base), entry in groups.items():
        found = entry["found"]
        folder_name, number = found["repo"], 2
        while folder_name in folders:
            folder_name = f"{found['repo']}-{number}"
            number += 1
        folders.add(folder_name)
        branch = f"chore/improvements-{batch}"
        count = branches.get(path, 0)
        branches[path] = count + 1
        if count:
            branch = f"{branch}-{count + 1}"
        folder = home.path("improve", batch, folder_name)
        items_file = os.path.join(folder, "items.md")
        project_file = os.path.join(folder, "project.json")
        write_text(items_file, items_markdown(found["repo"], entry["items"]))
        write_json(project_file, found["project"])
        result.append({
            "repo": found["repo"],
            "repo_path": found["path"],
            "remote_url": git_origin(found["path"]),
            "base_branch": base,
            "branch": branch,
            "worktree": home.path("worktrees", "improve", batch, folder_name),
            "items_file": items_file,
            "report_file": os.path.join(folder, "report.md"),
            "pr": os.path.join(folder, "pr.md"),
            "ids": [item["id"] for item in entry["items"]],
            "project_file": project_file,
            "setup": found["setup"],
        })
    with db.tx(conn):
        db.event(conn, "improve.start", None, None, batch=batch, groups=[group["repo"] for group in result],
                 ids=[improvement_id for group in result for improvement_id in group["ids"]])
    return {"batch": batch, "groups": result, "process": process, "skipped": skipped}


def record_pr(conn, ids, url, branch):
    url = (url or "").strip()
    branch = (branch or "").strip()
    if not branch:
        raise FactoryError("--branch is empty", code=2)
    items = [get(conn, improvement_id) for improvement_id in ids]
    for item in items:
        if item["status"] != "accepted":
            raise FactoryError(f"improvement {item['id']} is {item['status']}, not accepted")
        project = project_of(conn, item)
        if project:
            adapters.check_pr_url(project, url)
        elif not url.startswith(("https://", "http://")):
            raise FactoryError(f"not a PR url: {url}")
    with db.tx(conn):
        for item in items:
            conn.execute("UPDATE improvements SET pr_url = ?, branch = ? WHERE id = ?", (url, branch, item["id"]))
            event(conn, "improvement.pr", item, url=url, branch=branch)
            if item["issue_key"]:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO issue_links (issue_key, kind, ref, created_at) VALUES (?, 'pr', ?, ?)",
                    (item["issue_key"], url, now()),
                )
                if cursor.rowcount:
                    issues.event(conn, "issue.link", item["issue_key"], link="pr", ref=url)
    return [get(conn, item["id"]) for item in items]


def prs(conn, check=False):
    grouped = {}
    for item in listing(conn, statuses=["accepted"]):
        if item["pr_url"]:
            grouped.setdefault(item["pr_url"], []).append(item)
    result = []
    for url, items in grouped.items():
        entry = {"url": url, "branch": items[0]["branch"], "ids": [item["id"] for item in items],
                 "projects": sorted({item["project"] for item in items})}
        if check:
            project = project_of(conn, items[0])
            if project is None:
                entry.update(state="unchecked", reason=f"project {items[0]['project']} does not exist")
            else:
                found = adapters.read_pr_state(project, url)
                entry["state"] = found["state"]
                if found.get("reason"):
                    entry["reason"] = found["reason"]
        result.append(entry)
    return result
