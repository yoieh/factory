import json
import os
import re

from . import FactoryError, db, runs
from .util import now, read_text

KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
ITEM_KEYS = ("key", "target", "where", "title", "evidence", "proposal", "impact", "repo", "same_as")
TEXT_KEYS = ("where", "title", "evidence", "proposal")
REQUIRED = ("key", "target", "title", "evidence", "proposal")
IMPACTS = ("high", "medium", "low")
MAX_ITEMS = 8
MAX_TEXT = 4000


def retro_json(text):
    errors = []
    for block in re.findall(r"^```json[ \t]*\n(.*?)^```[ \t]*$", text, re.M | re.S):
        try:
            data = json.loads(block)
        except json.JSONDecodeError as error:
            errors.append(f"the json block does not parse: {error}")
            continue
        if isinstance(data, dict) and "improvements" in data:
            return data, []
    return None, errors or ["retro.md has no ```json block with \"improvements\""]


def needs_repo(target, spec):
    if target in ("factory", "process"):
        return False
    return "repo" not in spec


def validate(conn, run_id, data, project):
    problems = []
    targets = project["improvement_targets"]
    repos = [repo["name"] for repo in project["repos"]]
    items = data.get("improvements")
    problems.extend(f"unknown key {key!r}" for key in data if key != "improvements")
    if not isinstance(items, list):
        return problems + ["improvements must be a list"]
    if len(items) > MAX_ITEMS:
        problems.append(f"{len(items)} improvements, at most {MAX_ITEMS}")
    keys = [item.get("key") for item in items if isinstance(item, dict)]
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            problems.append(f"improvement {number} must be an object")
            continue
        label = f"improvement {item.get('key') or number}"
        problems.extend(f"{label}: unknown key {key!r}" for key in item if key not in ITEM_KEYS)
        if "same_as" in item:
            target_id = item["same_as"]
            if not (isinstance(target_id, int) and not isinstance(target_id, bool)):
                problems.append(f"{label}: same_as must be the id of an earlier improvement")
            else:
                earlier = conn.execute("SELECT run_id FROM improvements WHERE id = ?", (target_id,)).fetchone()
                if not earlier:
                    problems.append(f"{label}: same_as {target_id} is not an improvement")
                elif earlier[0] == run_id:
                    problems.append(f"{label}: same_as {target_id} is from this run, not an earlier one")
        for key in REQUIRED:
            if "same_as" in item and key != "key":
                continue
            if not (isinstance(item.get(key), str) and item[key].strip()):
                problems.append(f"{label}: {key} is required")
        key = item.get("key")
        if isinstance(key, str):
            if not KEY_RE.match(key):
                problems.append(f"{label}: key must match ^[a-z0-9][a-z0-9-]{{0,59}}$")
            elif keys.count(key) > 1:
                problems.append(f"{label}: duplicate key")
        for field in TEXT_KEYS:
            value = item.get(field)
            if value is not None and not isinstance(value, str):
                problems.append(f"{label}: {field} must be text")
            elif isinstance(value, str) and len(value) > MAX_TEXT:
                problems.append(f"{label}: {field} has {len(value)} characters, at most {MAX_TEXT}")
        if "impact" in item and item["impact"] not in IMPACTS:
            problems.append(f"{label}: impact must be high, medium or low")
        if "same_as" in item:
            continue
        target = item.get("target")
        if isinstance(target, str) and target not in targets:
            problems.append(f"{label}: target {target!r} is not one of {', '.join(sorted(targets))}")
        elif isinstance(target, str):
            if needs_repo(target, targets[target]):
                if "repo" not in item:
                    problems.append(f"{label}: target {target} needs repo, a repo of the project")
                elif item["repo"] not in repos:
                    problems.append(f"{label}: repo {item['repo']!r} is not a repo of the project")
            elif "repo" in item:
                problems.append(f"{label}: target {target} takes no repo")
    return problems


def row_values(item, targets):
    target = item["target"]
    spec = targets[target]
    return {
        "target": target,
        "repo": item.get("repo") or spec.get("repo"),
        "location": item.get("where"),
        "title": item["title"].strip(),
        "evidence": item["evidence"].strip(),
        "proposal": item["proposal"].strip(),
        "impact": item.get("impact", "medium"),
    }


def load(conn, run_id):
    from . import improvements

    run = runs.get(conn, run_id)
    path = runs.run_paths(run_id)["retro_file"]
    if not os.path.isfile(path):
        raise FactoryError(f"no retro.md at {path}")
    data, errors = retro_json(read_text(path))
    if errors:
        raise FactoryError("retro.md: " + "; ".join(errors))
    project = runs.snapshot(run_id)
    problems = validate(conn, run_id, data, project)
    if problems:
        raise FactoryError(f"retro.md has {len(problems)} problem{'s' if len(problems) != 1 else ''}:\n- " + "\n- ".join(problems))
    targets = project["improvement_targets"]
    existing = {row["key"]: row for row in db.rows(conn.execute("SELECT * FROM improvements WHERE run_id = ?", (run_id,)))}
    created, updated, repeated = [], [], []
    seen_keys = set()
    with db.tx(conn):
        for item in data["improvements"]:
            if "same_as" in item:
                earlier = db.one(conn.execute("SELECT * FROM improvements WHERE id = ?", (item["same_as"],)))
                seen = json.loads(earlier["seen_in"])
                if run_id not in seen:
                    seen.append(run_id)
                    conn.execute("UPDATE improvements SET seen_in = ? WHERE id = ?", (json.dumps(seen), earlier["id"]))
                    db.event(conn, "improvement.seen", run_id, improvement=earlier["id"])
                repeated.append(earlier["id"])
                continue
            seen_keys.add(item["key"])
            values = row_values(item, targets)
            if item["key"] in existing:
                row = existing[item["key"]]
                assignments = ", ".join(f"{field} = ?" for field in values)
                conn.execute(f"UPDATE improvements SET {assignments} WHERE id = ?", (*values.values(), row["id"]))
                db.event(conn, "improvement.update", run_id, improvement=row["id"], key=item["key"])
                updated.append(row["id"])
                continue
            cursor = conn.execute(
                "INSERT INTO improvements (run_id, project, key, target, repo, location, title, evidence, proposal, impact, "
                "status, seen_in, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)",
                (run_id, run["project"], item["key"], values["target"], values["repo"], values["location"],
                 values["title"], values["evidence"], values["proposal"], values["impact"], json.dumps([run_id]), now()),
            )
            db.event(conn, "improvement.add", run_id, improvement=cursor.lastrowid, key=item["key"])
            created.append(cursor.lastrowid)
        removed = []
        for key, row in existing.items():
            if key not in seen_keys and row["status"] == "open":
                conn.execute("DELETE FROM improvements WHERE id = ?", (row["id"],))
                db.event(conn, "improvement.remove", run_id, improvement=row["id"], key=key)
                removed.append(row["id"])
        db.event(conn, "retro.load", run_id, new=len(created), updated=len(updated), repeated=len(repeated))
    return {
        "run": run_id,
        "new": created,
        "updated": updated,
        "repeated": repeated,
        "removed": removed,
        "improvements": improvements.listing(conn, run_id=run_id),
    }
