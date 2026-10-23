import json
import time

from . import FactoryError, connected, db, improvements, jobs, questions, runs

PLAN_REPLY = "yes, or say what to change"
MAX_EVENTS = 1000


def question_kind(question, plan):
    if question["path"] == plan and question["asked_by"] == "orchestrator":
        return "plan_confirm"
    if question["asked_by"].startswith("gate:"):
        try:
            steps = jobs.steps_of(question["run_id"])
        except FactoryError:
            steps = {}
        if jobs.is_human(steps, question["asked_by"].split(":", 1)[1]):
            return "human_gate"
    return "question"


def options(text):
    found = []
    for line in (text or "").splitlines():
        match = questions.OPTION_RE.match(line)
        if match:
            found.append({"letter": match.group(1), "text": match.group(3).strip(),
                          "recommended": (match.group(2) or "").strip().lower() == "recommended"})
    return found


def run_summary(run):
    return {"id": run["id"], "feature": run["feature"], "project": run["project"], "status": run["status"],
            "issue_key": run["issue_key"]}


def job_line(conn, run_id, job_id):
    summary = jobs.summary(conn, run_id, job_id)
    return {"id": summary["id"], "kind": summary["kind"], "repo": summary["repo"], "title": summary["title"],
            "stage": summary["stage"], "pipeline": [{"step": group["step"], "gates": group["gates"]} for group in summary["groups"]]}


def question_item(conn, question, run, by):
    paths = runs.run_paths(run["id"])
    kind = question_kind(question, paths["plan"])
    item = {
        "type": "question",
        "id": question["id"],
        "kind": kind,
        "run": run_summary(run),
        "job": None,
        "asked_by": question["asked_by"],
        "asked_at": question["asked_at"],
        "decision": connected.first_line(question["text"]),
        "options": options(question["text"]),
        "text": question["text"],
        "thread": question["thread"],
        "answer": ["factory", "question", "answer", str(question["id"]), "<answer>", "--by", by],
        "ask_back": ["factory", "question", "clarify", str(question["id"]), "<follow-up>", "--by", by],
    }
    if question["job_id"]:
        try:
            item["job"] = job_line(conn, run["id"], question["job_id"])
        except FactoryError:
            item["job"] = {"id": question["job_id"]}
    if kind == "plan_confirm":
        item["expects"] = PLAN_REPLY
        item["plan"] = paths["plan"]
        item["repos"] = [repo["repo"] for repo in runs.run_repos(conn, run["id"])]
        item["jobs"] = [job_line(conn, run["id"], job["id"]) for job in jobs.job_rows(conn, run["id"])]
    elif kind == "human_gate":
        item["expects"] = "A to approve, or what to change"
    return item


def improvement_item(item):
    return {
        "type": "improvement",
        "id": item["id"],
        "run_id": item["run_id"],
        "project": item["project"],
        "title": item["title"],
        "target": item["target"],
        "impact": item["impact"],
        "evidence": item["evidence"],
        "proposal": item["proposal"],
        "decide": ["factory", "improvement", "decide", str(item["id"]), "accepted|rejected", "--note", "<note>"],
    }


def items(conn, project=None, agent=None):
    by = agent or "<your agent name>"
    found = []
    cached = {}
    for question in questions.listing(conn, None, ["open"]):
        run = cached.get(question["run_id"])
        if run is None:
            run = cached[question["run_id"]] = runs.get(conn, question["run_id"])
        if project and run["project"] != project:
            continue
        found.append(question_item(conn, question, run, by))
    for item in improvements.listing(conn, None, project, ["open"]):
        found.append(improvement_item(item))
    return found


def inbox(conn, project=None, agent=None):
    if agent:
        agent = connected.check_name(agent)
        connected.touch(conn, agent)
    found = items(conn, project, agent)
    return {"items": found, "count": len(found), "cursor": cursor(conn)}


def cursor(conn):
    return conn.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]


def read_events(conn, since, run_id, kinds, limit, latest=False):
    clauses, values = ["id > ?"], [since]
    if run_id:
        clauses.append("run_id = ?")
        values.append(run_id)
    if kinds:
        clauses.append("(" + " OR ".join("kind = ? OR substr(kind, 1, ?) = ?" for _ in kinds) + ")")
        for kind in kinds:
            prefix = kind.rstrip(".") + "."
            values.extend([kind, len(prefix), prefix])
    found = db.rows(conn.execute(
        f"SELECT id, run_id, job_id, at, kind, payload FROM events WHERE {' AND '.join(clauses)} "
        f"ORDER BY id {'DESC' if latest else ''} LIMIT ?",
        (*values, limit),
    ))
    if latest:
        found.reverse()
    for event in found:
        event["payload"] = json.loads(event["payload"] or "{}")
    return found


def events(conn, since=None, run_id=None, kinds=(), limit=200, wait=False, timeout=1800, interval=1.0, agent=None):
    if agent:
        agent = connected.check_name(agent)
        connected.touch(conn, agent)
    if run_id:
        runs.get(conn, run_id)
    if not 1 <= limit <= MAX_EVENTS:
        raise FactoryError(f"--limit must be 1 to {MAX_EVENTS}", code=2)
    if since is None and not wait:
        found = read_events(conn, 0, run_id, kinds, limit, latest=True)
        return {"events": found, "cursor": found[-1]["id"] if found else cursor(conn)}
    if since is None:
        since = cursor(conn)
    if since < 0:
        raise FactoryError("--since must be 0 or more", code=2)
    deadline = time.monotonic() + max(0, timeout)
    while True:
        found = read_events(conn, since, run_id, kinds, limit)
        if found or not wait or time.monotonic() >= deadline:
            return {"events": found, "cursor": found[-1]["id"] if found else since}
        time.sleep(min(interval, max(0.05, deadline - time.monotonic())))
