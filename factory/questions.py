import os
import re
import time

from . import FactoryError, db, heads, home
from .util import now, read_text, text_hash

OPTION_RE = re.compile(r"^\s*[-*]\s+\*\*([A-Z])(?:\s*\(([^)]*)\))?\s*:?\s*\*\*\s*:?\s*(.*)$")
ASKER_RE = re.compile(r"^(orchestrator|planner|retro|(produce|gate):[a-z][a-z0-9-]{1,39})$")
LETTERS = "ABCD"
MAX_LINE = 200
MAX_WORDS = 120


def format_problems(text):
    problems = []
    lines = (text or "").lstrip("﻿").splitlines()
    start = next((index for index, line in enumerate(lines) if line.strip()), None)
    if start is None:
        return ["the question is empty"]
    head = re.match(r"^QUESTION:[ \t]*(.*?)[ \t]*$", lines[start].strip(), re.I)
    if not head:
        return ["the first line must be 'QUESTION: <the decision>?'"]
    decision = head.group(1)
    if not decision:
        problems.append("the question line is empty")
    elif not decision.endswith("?"):
        problems.append("the question line must end with '?'")
    if len(decision) > MAX_LINE:
        problems.append(f"the question line has {len(decision)} characters, at most {MAX_LINE}")
    rest = lines[start + 1:]
    body = "\n".join(rest)
    opens = len(re.findall(r"<details\b", body, re.I))
    closes = len(re.findall(r"</details\s*>", body, re.I))
    counted = body
    if opens > 1:
        problems.append("at most one <details> block")
    if opens != closes:
        problems.append("the <details> block is not closed")
    elif opens == 1:
        before = re.split(r"<details\b", body, maxsplit=1, flags=re.I)[0]
        tail = re.split(r"</details\s*>", body, maxsplit=1, flags=re.I)[1]
        if tail.strip():
            problems.append("the <details> block must be the last part")
        counted = before
    options = []
    for line in counted.splitlines():
        match = OPTION_RE.match(line)
        if match:
            options.append((match.group(1), (match.group(2) or "").strip().lower()))
    if not 2 <= len(options) <= 4:
        problems.append(f"{len(options)} options, need 2 to 4 (lines like '- **A (recommended):** ...')")
    letters = "".join(letter for letter, _ in options)
    if options and letters != LETTERS[:len(options)]:
        problems.append(f"options must be lettered A, B, C, D in order, found {', '.join(letters)}")
    recommended = sum(1 for _, mark in options if mark == "recommended")
    if recommended != 1:
        problems.append(f"exactly one option must be marked (recommended), found {recommended}")
    words = len(re.findall(r"\S+", counted))
    if words > MAX_WORDS:
        problems.append(f"{words} words under the question line, at most {MAX_WORDS}")
    return problems


def row(conn, question_id):
    found = db.one(conn.execute("SELECT * FROM questions WHERE id = ?", (question_id,)))
    if not found:
        raise FactoryError(f"no question {question_id}")
    return found


def messages(conn, question_id):
    return db.rows(conn.execute(
        "SELECT id, kind, author, text, path, at, delivered_at FROM question_messages WHERE question_id = ? ORDER BY id",
        (question_id,),
    ))


def with_thread(conn, question):
    return dict(question, thread=messages(conn, question["id"]))


def check_asker(asked_by):
    if not ASKER_RE.match(asked_by or ""):
        raise FactoryError(
            f"--by must be orchestrator, planner, retro, produce:<step> or gate:<step>, not {asked_by!r}", code=2
        )


def existing(conn, run_id, job_id, asked_by, path, text):
    if path:
        found = conn.execute(
            "SELECT id FROM questions WHERE run_id = ? AND path = ? AND text = ? AND status != 'resolved' ORDER BY id DESC",
            (run_id, path, text),
        ).fetchone()
    else:
        found = conn.execute(
            "SELECT id FROM questions WHERE run_id = ? AND path IS NULL AND COALESCE(job_id, '') = ? "
            "AND asked_by = ? AND text = ? AND status != 'resolved' ORDER BY id DESC",
            (run_id, job_id or "", asked_by, text),
        ).fetchone()
    return found[0] if found else None


def log_refusal(conn, run_id, job_id, asked_by, text, problems):
    digest = text_hash(text)
    seen = conn.execute(
        "SELECT 1 FROM events WHERE run_id = ? AND kind = 'question.refused' AND json_extract(payload, '$.hash') = ?",
        (run_id, digest),
    ).fetchone()
    if not seen:
        db.event(conn, "question.refused", run_id, job_id, hash=digest, by=asked_by, problems=problems)


def store(conn, run_id, text, job_id=None, asked_by="orchestrator", path=None, plain=False):
    check_asker(asked_by)
    if not conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
        raise FactoryError(f"no run {run_id}")
    text = (text or "").strip()
    if not text:
        raise FactoryError("the question is empty", code=2)
    with db.tx(conn):
        found = existing(conn, run_id, job_id, asked_by, path, text)
        if found:
            return {"id": found, "existing": True}
        if not plain:
            problems = format_problems(text)
            if problems:
                log_refusal(conn, run_id, job_id, asked_by, text, problems)
                return {"problems": problems}
        cursor = conn.execute(
            "INSERT INTO questions (run_id, job_id, asked_by, text, path, status, asked_at) VALUES (?, ?, ?, ?, ?, 'open', ?)",
            (run_id, job_id, asked_by, text, path, now()),
        )
        question_id = cursor.lastrowid
        db.event(conn, "question.ask", run_id, job_id, question=question_id, by=asked_by)
    return {"id": question_id, "existing": False}


def ask(conn, run_id, text, job_id=None, asked_by="orchestrator", path=None, plain=False):
    stored = store(conn, run_id, text, job_id, asked_by, path, plain)
    if "problems" in stored:
        raise FactoryError("question format: " + "; ".join(stored["problems"]), code=2)
    return stored


def from_file(conn, run_id, job_id, asked_by, path):
    text = read_text(path)
    stored = store(conn, run_id, text, job_id, asked_by, path)
    if "problems" in stored:
        return {"question_error": stored["problems"], "path": path}
    return {"question": stored["id"]}


def add_message(conn, question, kind, author, text, path=None, delivered=False):
    stamp = now()
    conn.execute(
        "INSERT INTO question_messages (question_id, kind, author, text, path, at, delivered_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (question["id"], kind, author, text, path, stamp, stamp if delivered else None),
    )


def set_status(conn, question, status, **extra):
    fields = {"status": status, **extra}
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn.execute(f"UPDATE questions SET {assignments} WHERE id = ?", (*fields.values(), question["id"]))
    db.event(conn, "question.status", question["run_id"], question["job_id"],
             question=question["id"], **{"from": question["status"], "to": status})


def answer(conn, question_id, text, delivered=False, author="human"):
    text = (text or "").strip()
    if not text:
        raise FactoryError("the answer is empty", code=2)
    with db.tx(conn):
        question = row(conn, question_id)
        if question["status"] not in ("open", "clarify"):
            raise FactoryError(f"question {question_id} is {question['status']}, not open")
        add_message(conn, question, "answer", author, text, delivered=delivered)
        set_status(conn, question, "answered", answer=text, answered_at=now())
    return with_thread(conn, row(conn, question_id))


def reply_file(conn, question):
    from . import jobs

    if question["asked_by"].startswith(("produce:", "gate:")) and question["job_id"]:
        current = jobs.current_group(conn, question["run_id"], question["job_id"])
        if not current:
            groups = jobs.group_rows(conn, question["run_id"], question["job_id"])
            current = groups[0] if groups else None
        if current:
            folder = jobs.group_dir(question["run_id"], question["job_id"], current["position"], current["step"])
            return os.path.join(folder, f"reply-{question['id']}.md")
    return os.path.join(home.path("runs", question["run_id"]), f"reply-{question['id']}.md")


def clarify(conn, question_id, text, delivered=False, author="human"):
    text = (text or "").strip()
    if not text:
        raise FactoryError("the follow-up is empty", code=2)
    with db.tx(conn):
        question = row(conn, question_id)
        if question["status"] != "open":
            raise FactoryError(f"question {question_id} is {question['status']}, not open")
        target = reply_file(conn, question)
        add_message(conn, question, "clarify", author, text, path=target, delivered=delivered)
        set_status(conn, question, "clarify")
    return dict(with_thread(conn, row(conn, question_id)), reply_file=target)


def reply(conn, question_id, text=None):
    with db.tx(conn):
        question = row(conn, question_id)
        if question["status"] != "clarify":
            raise FactoryError(f"question {question_id} is {question['status']}, not waiting for a reply")
        target = reply_file(conn, question)
        if text is None:
            head = heads.read(target, heads.REPLY)
            if head["kind"] == "MISSING":
                raise FactoryError(f"no reply file: {target}")
            if head["kind"] != "REPLY":
                raise FactoryError(f"{target}: the first line must be 'REPLY: <one short sentence>'")
            body, path = head["text"].strip(), target
        else:
            body, path = text.strip(), None
            if not body:
                raise FactoryError("the reply is empty", code=2)
        add_message(conn, question, "reply", question["asked_by"], body, path=path, delivered=True)
        set_status(conn, question, "open")
    return with_thread(conn, row(conn, question_id))


def resolve(conn, question_id):
    with db.tx(conn):
        question = row(conn, question_id)
        if question["status"] != "answered":
            raise FactoryError(f"question {question_id} is {question['status']}, not answered")
        set_status(conn, question, "resolved")
    return with_thread(conn, row(conn, question_id))


def listing(conn, run_id=None, statuses=None):
    sql = "SELECT * FROM questions"
    clauses, values = [], []
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)
    if statuses:
        clauses.append(f"status IN ({', '.join('?' for _ in statuses)})")
        values.extend(statuses)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    return [with_thread(conn, question) for question in db.rows(conn.execute(sql + " ORDER BY id", values))]


def pending(conn, run_id):
    return db.rows(conn.execute(
        "SELECT m.id AS message_id, m.kind, m.text, m.path, q.id, q.job_id, q.asked_by, q.status "
        "FROM question_messages m JOIN questions q ON q.id = m.question_id "
        "WHERE q.run_id = ? AND m.kind IN ('answer', 'clarify') AND m.delivered_at IS NULL ORDER BY m.id",
        (run_id,),
    ))


def wait(conn, run_id, timeout=1800, interval=1.0):
    if not conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone():
        raise FactoryError(f"no run {run_id}")
    deadline = time.monotonic() + max(0, timeout)
    while True:
        found = pending(conn, run_id)
        if found:
            stamp = now()
            with db.tx(conn):
                for item in found:
                    conn.execute("UPDATE question_messages SET delivered_at = ? WHERE id = ? AND delivered_at IS NULL",
                                 (stamp, item["message_id"]))
            result = []
            for item in found:
                entry = {key: item[key] for key in ("id", "job_id", "asked_by", "status", "kind", "text")}
                if item["kind"] == "clarify":
                    entry["reply_file"] = item["path"]
                result.append(entry)
            return result
        if time.monotonic() >= deadline:
            return []
        time.sleep(min(interval, max(0.05, deadline - time.monotonic())))
