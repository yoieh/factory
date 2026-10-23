import bisect
import glob
import json
import os
import re

from . import FactoryError, db, home, runs
from .util import read_json

PRICES_DATE = "2026-10-22"
PRICES = {
    "claude-fable-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25},
    "claude-mythos-5-1": {"input": 10.0, "output": 50.0, "cache_read": 0.25},
    "claude-fable-5": {"input": 10.0, "output": 50.0, "cache_read": 1.0},
    "claude-mythos-5": {"input": 10.0, "output": 50.0, "cache_read": 1.0},
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.2},
    "claude-opus-5": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
    "claude-opus-4-8": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
    "claude-opus-4-7": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
    "claude-opus-4-6": {"input": 5.0, "output": 25.0, "cache_read": 0.5},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0, "cache_read": 0.2},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0, "cache_read": 0.3},
    "claude-haiku-5-5": {"input": 0.1, "output": 0.5, "cache_read": 0.01},
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0, "cache_read": 0.1},
}
FIELDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")
PROMPT_LINE = re.compile(r"^([a-z_]+):[ \t]*(\S.*?)\s*$")
RECENT = 64


def claude_dir():
    return os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude")


def price_table():
    path = home.path("prices.json")
    if not os.path.isfile(path):
        return {"date": PRICES_DATE, "source": "built-in", "models": PRICES}
    data = read_json(path)
    problems = []
    if not isinstance(data, dict) or set(data) - {"date", "models"}:
        problems.append("prices.json holds only date and models")
    elif not isinstance(data.get("date"), str) or not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$", data["date"]):
        problems.append("prices.json: date must be YYYY-MM-DD, the day the prices were checked")
    elif not isinstance(data.get("models"), dict):
        problems.append("prices.json: models must be an object of model id to prices")
    else:
        for model, prices in data["models"].items():
            if not isinstance(prices, dict) or set(prices) != {"input", "output", "cache_read"} or not all(
                    isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 for value in prices.values()):
                problems.append(f"prices.json: {model} needs input, output and cache_read in dollars per million tokens")
    if problems:
        raise FactoryError("; ".join(problems))
    return {"date": data["date"], "source": path, "models": data["models"]}


def prices_for(table, model):
    found = None
    for name in table["models"]:
        if model == name or model.startswith(name + "-") or model.startswith(name + "["):
            if found is None or len(name) > len(found):
                found = name
    return table["models"][found] if found else None


def cost(table, model, counts):
    prices = prices_for(table, model)
    if prices is None:
        return None
    million = 1_000_000
    return round((
        counts["input"] * prices["input"]
        + counts["cache_write_5m"] * prices["input"] * 1.25
        + counts["cache_write_1h"] * prices["input"] * 2
        + counts["cache_read"] * prices["cache_read"]
        + counts["output"] * prices["output"]
    ) / million, 6)


def prompt_text(entry):
    message = entry.get("message") or {}
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def prompt_keys(text):
    keys = {}
    for line in text.splitlines():
        match = PROMPT_LINE.match(line.strip())
        if match and match.group(1) not in keys:
            keys[match.group(1)] = match.group(2)
    return keys


def counts_of(usage):
    creation = usage.get("cache_creation") or {}
    five = creation.get("ephemeral_5m_input_tokens")
    hour = creation.get("ephemeral_1h_input_tokens")
    if five is None and hour is None:
        five, hour = usage.get("cache_creation_input_tokens") or 0, 0
    return {
        "input": int(usage.get("input_tokens") or 0),
        "cache_write_5m": int(five or 0),
        "cache_write_1h": int(hour or 0),
        "cache_read": int(usage.get("cache_read_input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
    }


def read_new_lines(path, offset):
    with open(path, "rb") as handle:
        handle.seek(offset)
        data = handle.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    lines = []
    for raw in data[:end].split(b"\n"):
        try:
            lines.append(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return lines, offset + end + 1


def first_prompt(path):
    with open(path, "rb") as handle:
        for raw in handle:
            try:
                entry = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(entry, dict) and entry.get("type") == "user":
                return prompt_keys(prompt_text(entry))
    return None


def transcript_files(session_id):
    base = claude_dir()
    found = [(path, None) for path in glob.glob(os.path.join(base, "projects", "*", f"{session_id}.jsonl"))]
    found += [(path, "subagent") for path in
              glob.glob(os.path.join(base, "projects", "*", session_id, "subagents", "agent-*.jsonl"))]
    return sorted(found)


def identify(conn, run_id, path, kind):
    if kind is None:
        return {"run_id": run_id, "job_id": "", "node": "orchestrator", "round": 0}
    keys = first_prompt(path)
    if keys is None:
        return None
    if keys.get("run_dir") == "none":
        return {"skip": True}
    run_ref = os.path.basename(keys["run_dir"].rstrip("/")) if keys.get("run_dir") else run_id
    if not conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_ref,)).fetchone():
        run_ref = run_id
    job_dir = keys.get("job_dir")
    job_id = os.path.basename(job_dir.rstrip("/")) if job_dir and job_dir != "none" else ""
    node = keys.get("node") or "other"
    try:
        number = int(keys.get("round", "0"))
    except ValueError:
        number = 0
    found = {"run_id": run_ref, "job_id": job_id, "node": node, "round": number}
    group_dir = keys.get("group_dir")
    if node.startswith("produce:") and group_dir and group_dir != "none":
        position = re.match(r"^([0-9]+)-", os.path.basename(group_dir.rstrip("/")))
        if position:
            found["group"] = int(position.group(1))
    return found


def round_starts(conn, run_id, job_id, group):
    starts = []
    for at, payload in conn.execute(
            "SELECT at, payload FROM events WHERE run_id = ? AND job_id = ? AND kind = 'job.round' ORDER BY id", (run_id, job_id)):
        data = json.loads(payload)
        if data.get("group") == group:
            starts.append((at, data.get("round", 0)))
    return starts


def round_at(starts, stamp, default):
    if not starts or not stamp:
        return default
    times = [at for at, _ in starts]
    index = bisect.bisect_right(times, stamp) - 1
    return starts[index][1] if index >= 0 else default


def add_counts(conn, key, model, counts):
    conn.execute(
        "INSERT INTO usage (run_id, job_id, node, round, model, input, cache_write_5m, cache_write_1h, cache_read, output) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (run_id, job_id, node, round, model) DO UPDATE SET "
        "input = input + excluded.input, cache_write_5m = cache_write_5m + excluded.cache_write_5m, "
        "cache_write_1h = cache_write_1h + excluded.cache_write_1h, cache_read = cache_read + excluded.cache_read, "
        "output = output + excluded.output",
        (key["run_id"], key["job_id"], key["node"], key["round"], model, *(counts[field] for field in FIELDS)),
    )


def sync_file(conn, run_id, session_id, path, kind):
    stored = db.one(conn.execute("SELECT * FROM transcripts WHERE path = ?", (path,)))
    if stored is None:
        found = identify(conn, run_id, path, kind)
        if found is None:
            return 0
        if found.get("skip"):
            with db.tx(conn):
                conn.execute("INSERT OR IGNORE INTO transcripts (path, session_id, run_id, node, offset) VALUES (?, ?, NULL, 'skip', 0)",
                             (path, session_id))
            return 0
        with db.tx(conn):
            conn.execute(
                "INSERT OR IGNORE INTO transcripts (path, session_id, run_id, job_id, node, round, offset, recent) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (path, session_id, found["run_id"], found["job_id"], found["node"], found["round"],
                 json.dumps({"seen": [], "group": found.get("group")})),
            )
        stored = db.one(conn.execute("SELECT * FROM transcripts WHERE path = ?", (path,)))
    if stored["run_id"] is None:
        return 0
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    if size <= stored["offset"]:
        return 0
    lines, offset = read_new_lines(path, stored["offset"])
    state = json.loads(stored["recent"] or "{}")
    seen = list(state.get("seen", []))
    group = state.get("group")
    messages = {}
    order = []
    for entry in lines:
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message") or {}
        message_id = message.get("id")
        usage = message.get("usage")
        model = message.get("model")
        if not message_id or not isinstance(usage, dict) or not isinstance(model, str) or model.startswith("<"):
            continue
        if message_id in seen:
            continue
        counts = counts_of(usage)
        if message_id not in messages:
            order.append(message_id)
            messages[message_id] = (model, counts, entry.get("timestamp"))
        elif counts["output"] > messages[message_id][1]["output"]:
            messages[message_id] = (model, counts, messages[message_id][2])
    starts = round_starts(conn, stored["run_id"], stored["job_id"], group) if group else []
    with db.tx(conn):
        for message_id in order:
            model, counts, stamp = messages[message_id]
            key = {"run_id": stored["run_id"], "job_id": stored["job_id"], "node": stored["node"],
                   "round": round_at(starts, stamp, stored["round"])}
            add_counts(conn, key, model, counts)
        seen = (seen + order)[-RECENT:]
        conn.execute("UPDATE transcripts SET offset = ?, recent = ? WHERE path = ?",
                     (offset, json.dumps({"seen": seen, "group": group}), path))
    return len(order)


def sync(conn, run_id=None):
    if run_id:
        runs.get(conn, run_id)
        sessions = db.rows(conn.execute("SELECT run_id, session_id FROM sessions WHERE run_id = ? ORDER BY added_at", (run_id,)))
    else:
        sessions = db.rows(conn.execute("SELECT run_id, session_id FROM sessions ORDER BY added_at"))
    files, counted = 0, 0
    for session in sessions:
        for path, kind in transcript_files(session["session_id"]):
            try:
                counted += sync_file(conn, session["run_id"], session["session_id"], path, kind)
            except OSError:
                continue
            files += 1
    return {"run": run_id, "sessions": len(sessions), "files": files, "messages": counted}


def empty():
    return {field: 0 for field in FIELDS}


def summed(rows, table):
    totals = empty()
    known = 0.0
    unknown = set()
    for row in rows:
        for field in FIELDS:
            totals[field] += row[field]
        value = cost(table, row["model"], row)
        if value is None:
            unknown.add(row["model"])
        else:
            known += value
    totals["tokens"] = sum(totals[field] for field in FIELDS)
    totals["cost"] = round(known, 4)
    totals["cost_complete"] = not unknown
    totals["unknown_models"] = sorted(unknown)
    return totals


def show(conn, run_id):
    runs.get(conn, run_id)
    table = price_table()
    rows = db.rows(conn.execute("SELECT * FROM usage WHERE run_id = ? ORDER BY job_id, node, round, model", (run_id,)))
    for row in rows:
        row["cost"] = cost(table, row["model"], row)
    by_node, by_job, by_round = {}, {}, {}
    for row in rows:
        by_node.setdefault(row["node"], []).append(row)
        by_job.setdefault(row["job_id"] or "-", []).append(row)
        by_round.setdefault(f"{row['job_id'] or '-'}/{row['node']}/{row['round']}", []).append(row)
    return {
        "run": run_id,
        "prices": {"date": table["date"], "source": table["source"]},
        "rows": rows,
        "by_node": {name: summed(group, table) for name, group in by_node.items()},
        "by_job": {name: summed(group, table) for name, group in by_job.items()},
        "by_round": {name: summed(group, table) for name, group in by_round.items()},
        "total": summed(rows, table),
    }


def run_cost(conn, run_id):
    rows = db.rows(conn.execute("SELECT * FROM usage WHERE run_id = ?", (run_id,)))
    if not rows:
        return None
    try:
        table = price_table()
    except FactoryError:
        return None
    total = summed(rows, table)
    return {"tokens": total["tokens"], "cost": total["cost"], "cost_complete": total["cost_complete"], "prices_date": table["date"]}
