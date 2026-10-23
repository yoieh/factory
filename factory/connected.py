import json
import re

from . import FactoryError, db
from .util import now

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
RESERVED = ("human", "dashboard", "orchestrator", "planner", "retro")
MAX_DESCRIPTION = 400
RECENT = 20
SELF = "orchestrator"
ROLES = ("review", "fix", "merge", "deploy")
USER = ("human", "dashboard")


def check_name(name):
    name = (name or "").strip()
    if not NAME_RE.match(name):
        raise FactoryError(
            f"an agent name is 1 to 40 letters, digits, '.', '_' or '-', starting with a letter or digit, not {name!r}",
            code=2,
        )
    if name.lower() in RESERVED:
        raise FactoryError(f"{name!r} is reserved; pick another agent name", code=2)
    return name


def check_author(author, own=False):
    author = (author or "").strip()
    if not author:
        raise FactoryError("--by is empty", code=2)
    if author in ("human", "dashboard") or (own and author == SELF):
        return author
    return check_name(author)


def seen(conn, author):
    if author not in (*USER, SELF):
        touch(conn, author)


def check_roles(text):
    names = [name.strip() for name in (text or "").split(",") if name.strip()]
    if names == ["none"]:
        return []
    unknown = [name for name in names if name not in ROLES]
    if unknown or not names:
        raise FactoryError(f"--roles is a comma list of {', '.join(ROLES)}, or none; not {text!r}", code=2)
    return [role for role in ROLES if role in names]


def allow(conn, author, role):
    """Refuse PR work by a connected agent the user did not give the role; the user and the session pass."""
    author = check_author(author)
    if author in USER:
        return author
    found = db.one(conn.execute("SELECT roles FROM connected_agents WHERE name = ?", (author,)))
    roles = json.loads(found["roles"]) if found else []
    touch(conn, author)
    if role not in roles:
        raise FactoryError(f"agent {author} has no role {role}; the user gives it with "
                           f"factory agent add {author} --roles {','.join(roles + [role])}")
    return author


def row(conn, name):
    found = db.one(conn.execute("SELECT * FROM connected_agents WHERE name = ?", (name,)))
    if not found:
        raise FactoryError(f"no connected agent {name}; add it with factory agent add {name}")
    return found


def touch(conn, name):
    stamp = now()
    with db.tx(conn):
        cursor = conn.execute("UPDATE connected_agents SET seen_at = ? WHERE name = ?", (stamp, name))
        if cursor.rowcount == 0:
            conn.execute("INSERT INTO connected_agents (name, added_at, seen_at) VALUES (?, ?, ?)", (name, stamp, stamp))
            db.event(conn, "agent.add", name=name, how="seen")


def add(conn, name, description=None, roles=None):
    name = check_name(name)
    roles = None if roles is None else check_roles(roles)
    description = (description or "").strip() or None
    if description and len(description) > MAX_DESCRIPTION:
        raise FactoryError(f"the description has {len(description)} characters, at most {MAX_DESCRIPTION}", code=2)
    with db.tx(conn):
        found = db.one(conn.execute("SELECT * FROM connected_agents WHERE name = ?", (name,)))
        if found:
            changes = {}
            if description is not None and description != found["description"]:
                changes["description"] = description
            if roles is not None and roles != json.loads(found["roles"]):
                changes["roles"] = json.dumps(roles)
            if changes:
                conn.execute(f"UPDATE connected_agents SET {', '.join(key + ' = ?' for key in changes)} WHERE name = ?",
                             (*changes.values(), name))
                db.event(conn, "agent.set", name=name, **({"roles": roles} if "roles" in changes else {}))
        else:
            conn.execute("INSERT INTO connected_agents (name, description, roles, added_at) VALUES (?, ?, ?, ?)",
                         (name, description, json.dumps(roles or []), now()))
            db.event(conn, "agent.add", name=name, how="added", roles=roles or [])
    return show(conn, name)


def remove(conn, name):
    row(conn, name)
    with db.tx(conn):
        conn.execute("DELETE FROM connected_agents WHERE name = ?", (name,))
        db.event(conn, "agent.remove", name=name)
    return {"removed": name}


def counts(conn, name):
    found = {"answers": 0, "follow_ups": 0}
    for kind, count in conn.execute(
        "SELECT kind, COUNT(*) FROM question_messages WHERE author = ? AND kind IN ('answer', 'clarify') GROUP BY kind",
        (name,),
    ):
        found["answers" if kind == "answer" else "follow_ups"] = count
    found["decisions"] = conn.execute(
        "SELECT COUNT(*) FROM events WHERE kind = 'improvement.decide' AND json_extract(payload, '$.by') = ?", (name,),
    ).fetchone()[0]
    found["pr_work"] = conn.execute(
        f"SELECT COUNT(*) FROM events WHERE kind IN ({', '.join('?' for _ in PR_WORK)}) AND json_extract(payload, '$.by') = ?",
        (*PR_WORK, name),
    ).fetchone()[0]
    return found


PR_WORK = ("land.start", "land.save", "land.fix", "land.apply", "land.after", "land.build", "land.deploy",
           "land.publish", "land.watch")


def pr_work(conn, name, limit=RECENT):
    found = db.rows(conn.execute(
        f"SELECT e.at, e.run_id, e.kind, e.payload FROM events e WHERE e.kind IN ({', '.join('?' for _ in PR_WORK)}) "
        "AND json_extract(e.payload, '$.by') = ? ORDER BY e.id DESC LIMIT ?",
        (*PR_WORK, name, limit),
    ))
    for item in found:
        payload = json.loads(item.pop("payload") or "{}")
        item["action"] = item.pop("kind").split(".", 1)[1]
        item["repo"] = payload.get("repo")
        item["outcome"] = next((payload[key] for key in ("action", "verdict", "status", "job") if payload.get(key)), None)
    return found


def recent(conn, name, limit=RECENT):
    found = db.rows(conn.execute(
        "SELECT m.question_id, m.kind, m.text, m.at, q.run_id, q.job_id, q.asked_by, q.text AS question, q.status "
        "FROM question_messages m JOIN questions q ON q.id = m.question_id "
        "WHERE m.author = ? AND m.kind IN ('answer', 'clarify') ORDER BY m.id DESC LIMIT ?",
        (name, limit),
    ))
    for item in found:
        item["question"] = first_line(item["question"])
    return found


def decisions(conn, name, limit=RECENT):
    return db.rows(conn.execute(
        "SELECT e.at, json_extract(e.payload, '$.improvement') AS improvement_id, json_extract(e.payload, '$.from') AS \"from\", "
        "json_extract(e.payload, '$.to') AS \"to\", json_extract(e.payload, '$.note') AS note, i.title, i.project, i.run_id "
        "FROM events e LEFT JOIN improvements i ON i.id = json_extract(e.payload, '$.improvement') "
        "WHERE e.kind = 'improvement.decide' AND json_extract(e.payload, '$.by') = ? ORDER BY e.id DESC LIMIT ?",
        (name, limit),
    ))


def first_line(text):
    line = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    return re.sub(r"^QUESTION:\s*", "", line, flags=re.I)


def entry(conn, found):
    return dict(found, roles=json.loads(found["roles"]), **counts(conn, found["name"]))


def listing(conn):
    rows = db.rows(conn.execute("SELECT * FROM connected_agents ORDER BY COALESCE(seen_at, added_at) DESC, name"))
    return [entry(conn, found) for found in rows]


def show(conn, name):
    found = entry(conn, row(conn, name))
    found["recent"] = recent(conn, name)
    found["decisions_recent"] = decisions(conn, name)
    found["pr_work_recent"] = pr_work(conn, name)
    return found
