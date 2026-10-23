"""Import the history of a board-based factory: the reference this core was built from.

Its folder holds board.json, jobs/<job-id>/ (feature, spec, build, round-<n>/review-<gate>.md, decision),
retros/<retro-id>/ (retro.md with numbered Proposals, gate.md or refine.md) and backlog.md with
backlog-archive.md, whose `(retro <retro-id>#<n>)` tags say what became of each proposal. Every old job
becomes a finished run with one job and one build group, its rounds and verdicts read from the files. Every
retro proposal becomes an improvement on the run of the last job the retro looked at.
"""

import datetime
import json
import os
import re
import shutil

from . import FactoryError, catalogue, db, home, projects, runs
from .util import read_text, slugify, write_json, write_text

JOB_DIR_RE = re.compile(r"^[0-9]{3}-[a-z0-9-]{1,36}$")
ROUND_RE = re.compile(r"^round-([0-9]+)$")
ITEM_RE = re.compile(r"^- \[(.)\] (.*)$", re.M)
TAG_RE = re.compile(r"\(retro ([0-9a-z-]+)#([0-9]+)\)")
DONE_AS_RE = re.compile(r"→ ([0-9]{3}-[a-z0-9-]+)\s*$")
REJECTED_RE = re.compile(r"\(rejected: (.*?)\)\s*$")
ID_TAG_RE = re.compile(r"\(id: ([0-9]{3}-[a-z0-9-]+)\)")
PROPOSAL_RE = re.compile(r"^([0-9]+)\. ", re.M)
FIELD_RE = re.compile(r"^\s+- (Kind|Evidence|Benefit): ?(.*)$")
TARGETS = {"feature": "repo", "process": "process", "factory": "factory"}
STATES = {"x": "done", " ": "accepted", "?": "open", "-": "rejected"}
DECISIONS = {"APPROVE": "PASS", "REWORK": "CHANGES", "ESCALATE": "QUESTION"}
GATE_ORDER = ("review-code", "review-security", "review-ux", "review-ui", "merge", "approve", "human")
SOURCE = "board"


def stamp(at):
    moment = datetime.datetime.fromtimestamp(at, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def seconds(text):
    return datetime.datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()


def mtime(path):
    return os.path.getmtime(path)


def head(path):
    try:
        for line in read_text(path).splitlines():
            if line.strip():
                return line.strip()
    except (OSError, UnicodeDecodeError):
        pass
    return ""


def review_verdict(path):
    word = head(path).upper()
    for verdict in ("PASS", "CHANGES", "QUESTION"):
        if word.startswith(f"VERDICT: {verdict}"):
            return verdict
    return None


def decision_verdict(path):
    word = head(path).upper().split(" ")[0]
    return DECISIONS.get(word)


def files_under(folder):
    found = []
    for base, dirs, names in os.walk(folder):
        dirs.sort()
        found.extend(os.path.join(base, name) for name in sorted(names) if not name.startswith("."))
    return found


def read_job(source, job_id, board_entry):
    """The rounds, verdicts and times of one old job folder."""
    folder = os.path.join(source, "jobs", job_id)
    rounds = {}
    for name in os.listdir(folder):
        match = ROUND_RE.match(name)
        if match and os.path.isdir(os.path.join(folder, name)):
            rounds[int(match.group(1))] = os.path.join(folder, name)
    last = max(rounds) if rounds else 1
    verdicts = []
    for number in sorted(rounds):
        decisions = []
        for name in sorted(os.listdir(rounds[number])):
            path = os.path.join(rounds[number], name)
            stem, extension = os.path.splitext(name)
            if extension != ".md":
                continue
            if stem.startswith("review-"):
                gate = {"review-human": "human", "review-merge": "merge"}.get(stem, stem)
                verdict = review_verdict(path)
                if verdict:
                    verdicts.append({"round": number, "gate": gate, "verdict": verdict, "path": path})
            elif stem.startswith("decision") and decision_verdict(path):
                decisions.append(path)
        if decisions:
            verdicts.append({"round": number, "gate": "approve", "verdict": decision_verdict(decisions[-1]),
                             "path": decisions[-1]})
    final = os.path.join(folder, "decision.md")
    if os.path.isfile(final) and decision_verdict(final):
        verdicts = [entry for entry in verdicts if not (entry["round"] == last and entry["gate"] == "approve")]
        verdicts.append({"round": last, "gate": "approve", "verdict": decision_verdict(final), "path": final})
        if decision_verdict(final) == "QUESTION":
            # The job was merged after the escalation, so the human approved it.
            verdicts.append({"round": last, "gate": "human", "verdict": "PASS", "path": final})
    started_files = [os.path.join(folder, name) for name in ("feature.md", "thread-brief.md", "spec.md")
                     if os.path.isfile(os.path.join(folder, name))]
    every = files_under(folder)
    created = min(mtime(path) for path in started_files or every)
    finished = max(mtime(path) for path in every)
    if board_entry and board_entry.get("updated"):
        try:
            finished = max(created, seconds(board_entry["updated"]))
        except ValueError:
            pass
    spec = os.path.join(folder, "spec.md")
    title = (board_entry or {}).get("title") or head(os.path.join(folder, "feature.md")).lstrip("# ").strip() or job_id
    return {
        "id": job_id,
        "folder": folder,
        "title": " ".join(title.split()),
        "branch": (board_entry or {}).get("branch") or f"factory/{job_id}",
        "note": (board_entry or {}).get("note"),
        "thread": (board_entry or {}).get("thread"),
        "rounds": rounds,
        "last_round": last,
        "verdicts": verdicts,
        "gates": [gate for gate in GATE_ORDER if any(entry["gate"] == gate for entry in verdicts)],
        "created": created,
        "planned": mtime(spec) if os.path.isfile(spec) else created,
        "finished": finished,
    }


def board_jobs(source):
    path = os.path.join(source, "board.json")
    try:
        board = json.loads(read_text(path))
    except (OSError, json.JSONDecodeError) as error:
        raise FactoryError(f"{path}: {error}")
    return {entry["id"]: entry for entry in board.get("jobs", []) if isinstance(entry, dict) and entry.get("id")}


def merged_jobs(source):
    """Board jobs at stage merged, and job folders from before the board that an approver approved."""
    board = board_jobs(source)
    folder = os.path.join(source, "jobs")
    if not os.path.isdir(folder):
        raise FactoryError(f"{source} has no jobs folder; give the old factory folder, the one with board.json")
    found, skipped = [], []
    for name in sorted(os.listdir(folder)):
        if not JOB_DIR_RE.match(name) or not os.path.isdir(os.path.join(folder, name)):
            continue
        entry = board.get(name)
        if entry and entry.get("stage") != "merged":
            skipped.append({"job": name, "reason": f"stage {entry.get('stage')} on the board"})
        elif not entry and head(os.path.join(folder, name, "decision.md")).upper().split(" ")[0] != "APPROVE":
            skipped.append({"job": name, "reason": "not on the board and not approved"})
        else:
            found.append(read_job(source, name, entry))
    return found, skipped


def backlog_states(source):
    """(retro-id, number) -> the backlog line that carries that tag, its state and what became of it."""
    found = {}
    for name in ("backlog.md", "backlog-archive.md"):
        path = os.path.join(source, name)
        if not os.path.isfile(path):
            continue
        for match in ITEM_RE.finditer(read_text(path)):
            state, text = STATES.get(match.group(1)), match.group(2)
            if not state:
                continue
            for tag in TAG_RE.findall(text):
                key = (tag[0], int(tag[1]))
                earlier = found.get(key)
                if earlier and earlier["status"] == "done":
                    continue
                done_as = DONE_AS_RE.search(text)
                rejected = REJECTED_RE.search(text)
                id_tag = ID_TAG_RE.search(text)
                found[key] = {"status": state, "text": text, "job": done_as.group(1) if done_as else None,
                              "reason": rejected.group(1) if rejected else None,
                              "id_tag": id_tag.group(1) if id_tag else None}
    return found


def first_sentence(text, limit=120):
    text = " ".join(text.split())
    match = re.match(r"(.+?[.!?])(\s|$)", text)
    sentence = match.group(1) if match else text
    if len(sentence) <= limit:
        return sentence
    cut = sentence[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def proposals(text):
    section = re.split(r"^## Proposals[ \t]*$", text, maxsplit=1, flags=re.M)
    if len(section) < 2:
        return []
    body = re.split(r"^## ", section[1], maxsplit=1, flags=re.M)[0]
    starts = list(PROPOSAL_RE.finditer(body))
    found = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(body)
        lines = body[start.end():end].rstrip().splitlines()
        main, fields, current = [], {}, None
        for line in lines:
            field = FIELD_RE.match(line)
            if field:
                current = field.group(1).lower()
                fields[current] = field.group(2).strip()
            elif current:
                fields[current] = (fields[current] + "\n" + line.strip()).strip()
            else:
                main.append(line)
        found.append({"number": int(start.group(1)), "text": "\n".join(main).strip(), **fields})
    return found


def retro_scope(retro_id, text, job_ids, board):
    scope = []
    line = re.search(r"^Scope:[ \t]*(.*)$", text, re.M)
    if line:
        scope = [token for token in re.split(r"[\s,]+", line.group(1)) if token in job_ids]
    scope += [job_id for job_id, entry in board.items() if entry.get("retro") == retro_id and job_id in job_ids]
    if not scope:
        slug = retro_id[11:]
        scope = [job_id for job_id in job_ids if job_id == slug or job_id.endswith("-" + slug)]
    return list(dict.fromkeys(scope))


def read_retros(source, jobs_by_id):
    folder = os.path.join(source, "retros")
    if not os.path.isdir(folder):
        return [], []
    board = board_jobs(source)
    found, skipped = [], []
    for retro_id in sorted(os.listdir(folder)):
        path = os.path.join(folder, retro_id, "retro.md")
        if not os.path.isfile(path):
            continue
        text = read_text(path)
        scope = retro_scope(retro_id, text, jobs_by_id, board)
        if not scope:
            skipped.append({"retro": retro_id, "reason": "its scope names no imported job"})
            continue
        owner = max(scope, key=lambda job_id: (jobs_by_id[job_id]["finished"], job_id))
        found.append({"id": retro_id, "folder": os.path.join(folder, retro_id), "path": path, "scope": scope,
                      "job": owner, "at": mtime(path), "proposals": proposals(text)})
    return found, skipped


def issue_for(entry, issue_map):
    for key, issue in issue_map.items():
        old = (issue or {}).get("old") or ""
        if old and (old == entry["id_tag"] or entry["text"].startswith(old)):
            return key
    return None


def improvement_rows(retro, states, jobs_by_id, issue_map, repo):
    rows, keys = [], set()
    for item in retro["proposals"]:
        if not item["text"]:
            continue
        title = first_sentence(item["text"])
        key = slugify(f"{item['number']} {title}", 56) or f"proposal-{item['number']}"
        while key in keys:
            key += "-x"
        keys.add(key)
        target = TARGETS.get((item.get("kind") or "").split(" ")[0].lower(), "process")
        proposal = item["text"]
        if item.get("benefit"):
            proposal += f"\n\nBenefit: {item['benefit']}"
        state = states.get((retro["id"], item["number"]))
        decided = None
        if not state:
            status, note = "rejected", "never went into the old backlog"
        else:
            status = state["status"]
            note = None
            if status == "done":
                note = f"built in job {state['job']}" if state["job"] else "done in the old factory"
                if state["job"] in jobs_by_id:
                    decided = jobs_by_id[state["job"]]["finished"]
            elif status == "rejected":
                note = state["reason"] or "rejected in the old factory"
        rows.append({
            "key": key,
            "target": target,
            "repo": repo if target == "repo" else None,
            "title": title,
            "evidence": item.get("evidence") or f"retros/{retro['id']}/retro.md",
            "proposal": proposal,
            "status": status,
            "note": (f"{note} (retro {retro['id']}#{item['number']})" if note else f"retro {retro['id']}#{item['number']}"),
            "issue_key": issue_for(state, issue_map) if state and status == "accepted" else None,
            "decided_at": decided if decided is not None else (retro["at"] if status != "open" else None),
        })
    return rows


def copy_job(job, run_folder):
    for path in files_under(job["folder"]):
        if os.path.relpath(path, job["folder"]) == "build.md":
            destination = os.path.join(run_folder, "jobs", job["id"], "groups", "01-build", "output.md")
        else:
            destination = moved(path, job, run_folder)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(path, destination)


def moved(path, job, run_folder):
    """Where a file of the old job folder lands in the run folder."""
    relative = os.path.relpath(path, job["folder"])
    first = relative.split(os.sep)[0]
    base = os.path.join(run_folder, "jobs", job["id"])
    if ROUND_RE.match(first):
        return os.path.join(base, "groups", "01-build", relative)
    return os.path.join(base, relative)


def plan_text(job, repo):
    entry = {"id": job["id"], "kind": "code", "repo": repo, "title": job["title"], "depends_on": [],
             "template": "code-change", "pipeline": [{"step": "build", "gates": job["gates"]}]}
    return (f"# Plan: {job['title']}\n\nImported from the old factory: job `{job['id']}`, one job with the old "
            "review rounds.\n\n## Jobs\n\n| Job | Kind | Repo | Title | After | Pipeline | Why this pipeline |\n"
            "|---|---|---|---|---|---|---|\n"
            f"| {job['id']} | code | {repo} | {job['title']} | | build: {', '.join(job['gates'])} | the old factory's |\n\n"
            "```json\n" + json.dumps({"jobs": [entry]}, indent=1) + "\n```\n")


def report_text(job):
    lines = [f"# Report: {job['title']}", "", f"Imported from the old factory's job `{job['id']}`.", "",
             f"- Branch: `{job['branch']}`, merged into main by the old factory.",
             f"- Rounds: {job['last_round']}."]
    if job["note"]:
        lines.append(f"- Board note: {job['note']}")
    if job["thread"]:
        lines.append(f"- T3 thread: `{job['thread']}`")
    return "\n".join(lines) + "\n"


def existing_runs(conn, project):
    return {row[0]: row[1] for row in conn.execute(
        "SELECT feature_branch, id FROM runs WHERE project = ? AND id IN "
        "(SELECT run_id FROM events WHERE kind = 'run.import')", (project,))}


def new_run_id(conn, job, taken):
    base = datetime.datetime.fromtimestamp(job["created"], datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    candidate, number = f"{base}-{slugify(job['id'], 36)}", 2
    while candidate in taken or conn.execute("SELECT 1 FROM runs WHERE id = ?", (candidate,)).fetchone() \
            or os.path.exists(runs.run_dir(candidate)):
        candidate = f"{base}-{slugify(job['id'], 36)}-{number}"
        number += 1
    taken.add(candidate)
    return candidate


def add_event(conn, run_id, job_id, at, kind, **payload):
    conn.execute("INSERT INTO events (run_id, job_id, at, kind, payload) VALUES (?, ?, ?, ?, ?)",
                 (run_id, job_id, stamp(at), kind, json.dumps(payload, ensure_ascii=False, sort_keys=True)))


def job_events(job):
    """(time, kind, job?, payload) for the timeline, from the file times, never going back in time."""
    events = [(job["created"], "run.new", False, {"imported": True}),
              (job["planned"], "run.status", False, {"from": "planning", "to": "running"})]
    start = job["planned"]
    for number in range(1, job["last_round"] + 1):
        folder = job["rounds"].get(number)
        times = [mtime(path) for path in files_under(folder)] if folder else []
        events.append((start, "job.round", True, {"group": 1, "round": number}))
        if times:
            events.append((min(times), "group.phase", True, {"group": 1, "from": "produce", "to": "gate"}))
            start = max(times)
    final = os.path.join(job["folder"], "decision.md")
    passed = mtime(final) if os.path.isfile(final) else start
    events += [(passed, "group.phase", True, {"group": 1, "from": "gate", "to": "passed"}),
               (job["finished"], "job.stage", True, {"from": "running", "to": "done"}),
               (job["finished"], "run.status", False, {"from": "running", "to": "finishing"}),
               (job["finished"], "run.status", False, {"from": "finishing", "to": "done"})]
    found, last = [], 0
    for at, kind, own, payload in events:
        last = max(last, at)
        found.append((last, kind, own, payload))
    return found


def insert_run(conn, run_id, project_name, repo, job, imported_at):
    created = stamp(job["created"])
    conn.execute(
        "INSERT INTO runs (id, project, feature, slug, feature_branch, parallel, max_rounds, confirm_plan, no_pr, "
        "status, created_at) VALUES (?, ?, ?, ?, ?, 1, ?, 0, 1, 'done', ?)",
        (run_id, project_name, job["title"], slugify(job["title"], 40) or "change", job["branch"],
         min(10, max(4, job["last_round"])), created),
    )
    conn.execute("INSERT INTO run_repos (run_id, repo, position, path, base_branch, can_push) VALUES (?, ?, 0, ?, ?, 0)",
                 (run_id, repo["name"], repo["path"], repo.get("default_branch") or "main"))
    conn.execute(
        "INSERT INTO jobs (run_id, id, position, kind, repo, title, template, stage, group_pos, branch) "
        "VALUES (?, ?, 0, 'code', ?, ?, 'code-change', 'done', 1, ?)",
        (run_id, job["id"], repo["name"], job["title"], job["branch"]),
    )
    conn.execute(
        "INSERT INTO job_groups (run_id, job_id, position, step, gates, phase, round, max_round) "
        "VALUES (?, ?, 1, 'build', ?, 'passed', ?, ?)",
        (run_id, job["id"], json.dumps(job["gates"]), job["last_round"], min(10, max(4, job["last_round"]))),
    )
    folder = runs.run_dir(run_id)
    for entry in job["verdicts"]:
        conn.execute(
            "INSERT OR REPLACE INTO verdicts (run_id, job_id, group_pos, round, gate, verdict, path) VALUES (?, ?, 1, ?, ?, ?, ?)",
            (run_id, job["id"], entry["round"], entry["gate"], entry["verdict"], moved(entry["path"], job, folder)),
        )
    for at, kind, own, payload in job_events(job):
        add_event(conn, run_id, job["id"] if own else None, at, kind, **payload)
    conn.execute("INSERT INTO events (run_id, at, kind, payload) VALUES (?, ?, 'run.import', ?)",
                 (run_id, imported_at, json.dumps({"source": SOURCE, "job": job["id"]}, sort_keys=True)))


def insert_questions(conn, run_id, job):
    path = os.path.join(job["folder"], "answers.md")
    if not os.path.isfile(path):
        return 0
    count = 0
    for line in read_text(path).splitlines():
        match = re.match(r"^- (.+?\?) — (.+)$", line.strip())
        if not match:
            continue
        conn.execute(
            "INSERT INTO questions (run_id, job_id, asked_by, text, path, answer, status, asked_at, answered_at) "
            "VALUES (?, ?, 'spec-writer', ?, ?, ?, 'resolved', ?, ?)",
            (run_id, job["id"], f"QUESTION: {match.group(1)}", moved(path, job, runs.run_dir(run_id)), match.group(2),
             stamp(job["created"]), stamp(max(job["created"], mtime(path)))),
        )
        count += 1
    return count


def insert_improvements(conn, run_id, project_name, rows):
    added = 0
    for row in rows:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO improvements (run_id, project, key, target, repo, title, evidence, proposal, impact, "
            "status, note, seen_in, issue_key, created_at, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'medium', ?, ?, ?, ?, ?, ?)",
            (run_id, project_name, row["key"], row["target"], row["repo"], row["title"], row["evidence"], row["proposal"],
             row["status"], row["note"], json.dumps([run_id]), row["issue_key"], row["created_at"],
             stamp(row["decided_at"]) if row["decided_at"] is not None else None),
        )
        if cursor.rowcount:
            added += 1
            if row["issue_key"] and conn.execute("SELECT 1 FROM issues WHERE key = ?", (row["issue_key"],)).fetchone():
                conn.execute("INSERT OR IGNORE INTO issue_links (issue_key, kind, ref, created_at) VALUES (?, 'improvement', ?, ?)",
                             (row["issue_key"], str(cursor.lastrowid), row["created_at"]))
            elif row["issue_key"]:
                conn.execute("UPDATE improvements SET issue_key = NULL WHERE id = ?", (cursor.lastrowid,))
    return added


def import_board(conn, source, project_name, repo_name=None, issues_file=None, dry_run=False):
    source = os.path.abspath(os.path.expanduser(source))
    if os.path.isdir(os.path.join(source, "factory")) and not os.path.isfile(os.path.join(source, "board.json")):
        source = os.path.join(source, "factory")
    if not os.path.isfile(os.path.join(source, "board.json")):
        raise FactoryError(f"{source} has no board.json; give the old factory folder")
    projects.require(conn, project_name)
    config = home.load_config()
    project = projects.resolved(projects.load(project_name, config), config)
    repos = project["repos"]
    if repo_name:
        repos = [repo for repo in repos if repo["name"] == repo_name]
        if not repos:
            raise FactoryError(f"{repo_name} is not a repo of {project_name}")
    if len(repos) != 1:
        raise FactoryError(f"{project_name} has {len(repos)} repos; say which one the old jobs changed with --repo")
    repo = repos[0]
    issue_map = {}
    if issues_file:
        try:
            issue_map = json.loads(read_text(os.path.expanduser(issues_file)))
        except (OSError, json.JSONDecodeError) as error:
            raise FactoryError(f"{issues_file}: {error}")
    jobs_found, skipped = merged_jobs(source)
    jobs_by_id = {job["id"]: job for job in jobs_found}
    retros, retros_skipped = read_retros(source, jobs_by_id)
    states = backlog_states(source)
    done = existing_runs(conn, project_name)
    taken = set()
    run_of = {}
    new_jobs = []
    for job in jobs_found:
        if job["branch"] in done:
            run_of[job["id"]] = done[job["branch"]]
        else:
            run_of[job["id"]] = new_run_id(conn, job, taken)
            new_jobs.append(job)
    planned_improvements = []
    for retro in retros:
        for row in improvement_rows(retro, states, jobs_by_id, issue_map, repo["name"]):
            row["created_at"] = stamp(retro["at"])
            planned_improvements.append((run_of[retro["job"]], retro, row))
    summary = {
        "source": source,
        "project": project_name,
        "repo": repo["name"],
        "runs": len(new_jobs),
        "runs_already_imported": len(jobs_found) - len(new_jobs),
        "retros": len(retros),
        "improvements": {status: sum(1 for _, _, row in planned_improvements if row["status"] == status)
                         for status in ("open", "accepted", "rejected", "done")},
        "skipped": skipped + retros_skipped,
        "dry_run": bool(dry_run),
    }
    if dry_run:
        return summary
    catalogue_snapshot = catalogue.show(config, project)
    imported_at = stamp(datetime.datetime.now(datetime.timezone.utc).timestamp())
    for job in new_jobs:
        folder = runs.run_dir(run_of[job["id"]])
        if os.path.exists(folder):
            shutil.rmtree(folder)
        copy_job(job, folder)
        write_json(os.path.join(folder, "project.json"), project)
        write_json(os.path.join(folder, "catalogue.json"), catalogue_snapshot)
        write_text(os.path.join(folder, "plan.md"), plan_text(job, repo["name"]))
        write_text(os.path.join(folder, "report.md"), report_text(job))
    for retro in retros:
        target = os.path.join(runs.run_dir(run_of[retro["job"]]), "retros", retro["id"])
        os.makedirs(target, exist_ok=True)
        for path in files_under(retro["folder"]):
            destination = os.path.join(target, os.path.relpath(path, retro["folder"]))
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(path, destination)
    questions = 0
    added = 0
    with db.tx(conn):
        for job in new_jobs:
            insert_run(conn, run_of[job["id"]], project_name, repo, job, imported_at)
            questions += insert_questions(conn, run_of[job["id"]], job)
        new_runs = {run_of[job["id"]] for job in new_jobs}
        for retro in retros:
            run_id = run_of[retro["job"]]
            rows = [row for other_run, other, row in planned_improvements if other is retro]
            count = insert_improvements(conn, run_id, project_name, rows)
            added += count
            if count or run_id in new_runs:
                add_event(conn, run_id, None, max(retro["at"], jobs_by_id[retro["job"]]["finished"]), "retro.load",
                          retro=retro["id"], new=count, imported=True)
        db.event(conn, "legacy.import", source=source, project=project_name, runs=len(new_jobs), improvements=added)
    summary["questions"] = questions
    summary["improvements_added"] = added
    return summary
