import re

from . import FactoryError, db
from .util import now

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
RESERVED = ("human", "dashboard", "orchestrator", "planner", "retro")
MAX_DESCRIPTION = 400
RECENT = 20


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


def add(conn, name, description=None):
    name = check_name(name)
    description = (description or "").strip() or None
    if description and len(description) > MAX_DESCRIPTION:
        raise FactoryError(f"the description has {len(description)} characters, at most {MAX_DESCRIPTION}", code=2)
    with db.tx(conn):
        found = db.one(conn.execute("SELECT * FROM connected_agents WHERE name = ?", (name,)))
        if found:
            if description is not None and description != found["description"]:
                conn.execute("UPDATE connected_agents SET description = ? WHERE name = ?", (description, name))
                db.event(conn, "agent.set", name=name)
        else:
            conn.execute("INSERT INTO connected_agents (name, description, added_at) VALUES (?, ?, ?)",
                         (name, description, now()))
            db.event(conn, "agent.add", name=name, how="added")
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


def first_line(text):
    line = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    return re.sub(r"^QUESTION:\s*", "", line, flags=re.I)


def entry(conn, found):
    return dict(found, **counts(conn, found["name"]))


def listing(conn):
    rows = db.rows(conn.execute("SELECT * FROM connected_agents ORDER BY COALESCE(seen_at, added_at) DESC, name"))
    return [entry(conn, found) for found in rows]


def show(conn, name):
    found = entry(conn, row(conn, name))
    found["recent"] = recent(conn, name)
    return found
