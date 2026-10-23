import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from . import FactoryError, VERSION, catalogue, heads, home, improvements, issues, jobs, projects, questions, runs, skills, usage
from .util import read_json, read_text

PNG = b"\x89PNG\r\n\x1a\n"
MAX_BODY = 1_000_000
MAX_TEXT = 4000
MAX_ISSUE_BODY = 100_000
MAX_TITLE = 200
POLL_SECONDS = 300
TEXT_SUFFIXES = (".md", ".json")
TEXT_FOLDERS = ("runs", "improve", "issues")
HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                               "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
                               "frame-ancestors 'none'",
}


class Refused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def dashboard_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")


def single(query, name, default=None):
    values = query.get(name)
    return values[0] if values and values[0] != "" else default


def flag(value):
    return str(value).lower() in ("1", "true", "yes")


def under(path, roots):
    real = os.path.realpath(path)
    return any(real == root or real.startswith(root + os.sep) for root in roots)


def guarded_file(raw, folders, suffixes):
    if not raw:
        raise Refused(400, "path is required")
    path = raw if os.path.isabs(raw) else home.path(raw)
    if os.path.splitext(path)[1].lower() not in suffixes:
        raise Refused(403, f"only {', '.join(suffixes)} files can be read")
    roots = [os.path.realpath(home.path(folder)) for folder in folders]
    if not under(path, roots):
        raise Refused(403, "the file is not under the factory's run, improve or issue folders")
    if not os.path.isfile(path):
        raise Refused(404, "no such file")
    return os.path.realpath(path)


def text_limit(value, limit, label):
    if value is None:
        return None
    if not isinstance(value, str):
        raise Refused(400, f"{label} must be text")
    if len(value) > limit:
        raise Refused(413, f"{label} has {len(value)} characters, at most {limit}")
    return value


def secret_settings(entry):
    try:
        data = read_json(entry["adapter_json"])
    except (FactoryError, KeyError):
        return set()
    return {name for name, spec in data.get("settings", {}).items() if isinstance(spec, dict) and spec.get("secret")}


def scrub_paths(value, roots):
    if isinstance(value, dict):
        return {key: scrub_paths(item, roots) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_paths(item, roots) for item in value
                if not (isinstance(item, str) and os.path.isabs(item) and not under(item, roots))]
    if isinstance(value, str) and os.path.isabs(value) and not under(value, roots):
        return None
    return value


def redacted_project(project):
    project = json.loads(json.dumps(project))
    for entry in project.get("integrations", {}).values():
        for name in secret_settings(entry):
            if name in entry.get("settings", {}):
                entry["settings"][name] = "***"
    roots = [os.path.realpath(home.root())] + [os.path.realpath(repo["path"]) for repo in project.get("repos", [])]
    return scrub_paths(project, roots)


def run_files(run_id):
    folder = runs.run_dir(run_id)
    found = []
    for base, dirs, files in os.walk(folder):
        dirs[:] = sorted(name for name in dirs if not name.startswith("."))
        for name in sorted(files):
            if name.startswith(".") or os.path.splitext(name)[1].lower() not in (".md", ".json", ".png"):
                continue
            path = os.path.join(base, name)
            found.append({"path": path, "name": os.path.relpath(path, folder), "size": os.path.getsize(path)})
            if len(found) >= 500:
                return found
    return found


def step_focus(path):
    try:
        _, body = catalogue.front_matter(read_text(path))
    except (FactoryError, OSError):
        return []
    return heads.items(heads.sections(body).get("focus"))


def run_steps(run_id, shown):
    steps = runs.snapshot(run_id, "catalogue.json").get("steps", {})
    used = set()
    for job in shown["jobs"]:
        for group in job["groups"]:
            used.add(group["step"])
            used.update(group["gates"])
    found = {}
    for name in sorted(used):
        step = steps.get(name)
        if step:
            found[name] = {key: step.get(key) for key in ("name", "role", "kinds", "agent", "model", "summary", "when", "level")}
            found[name]["focus"] = step_focus(step.get("path", ""))
    return found


def run_detail(conn, run_id):
    runs.get(conn, run_id)
    try:
        usage.sync(conn, run_id)
    except (FactoryError, OSError):
        pass
    shown = runs.show(conn, run_id)
    try:
        shown["usage"] = usage.show(conn, run_id)
    except FactoryError as error:
        shown["usage"] = {"error": error.message}
    plan = runs.run_paths(run_id)["plan"]
    for question in shown["questions"]:
        question["kind"] = question_kind(question, plan)
    shown["files"] = run_files(run_id)
    try:
        shown["steps"] = run_steps(run_id, shown)
    except FactoryError:
        shown["steps"] = {}
    return shown


def active_jobs(conn, project=None):
    found = []
    for run in runs.listing(conn, project):
        if run["status"] in ("done", "failed"):
            continue
        for job in jobs.job_rows(conn, run["id"]):
            entry = jobs.summary(conn, run["id"], job["id"])
            entry.update(run=run["id"], feature=run["feature"], project=run["project"])
            found.append(entry)
    return found


def question_kind(question, plan):
    return "plan_confirm" if question["path"] == plan and question["asked_by"] == "orchestrator" else "question"


def question_entries(conn, statuses, project=None):
    found = []
    cached = {}
    for question in questions.listing(conn, None, statuses):
        run = cached.get(question["run_id"])
        if run is None:
            run = cached[question["run_id"]] = runs.get(conn, question["run_id"])
        if project and run["project"] != project:
            continue
        plan = runs.run_paths(run["id"])["plan"]
        question["kind"] = question_kind(question, plan)
        question["run"] = {"id": run["id"], "feature": run["feature"], "project": run["project"], "status": run["status"],
                           "issue_key": run["issue_key"]}
        if question["job_id"]:
            try:
                question["job"] = jobs.summary(conn, run["id"], question["job_id"])
            except FactoryError:
                question["job"] = None
        if question["kind"] == "plan_confirm":
            question["jobs"] = [jobs.summary(conn, run["id"], job["id"]) for job in jobs.job_rows(conn, run["id"])]
            question["plan"] = plan
        found.append(question)
    return found


def project_detail(conn, name):
    projects.require(conn, name)
    project = projects.resolved(projects.load(name))
    project["installs"] = skills.project_installs(conn, name, project["repos"])
    project["catalogue"] = catalogue.show(home.load_config(), project)
    shown = redacted_project(project)
    shown["installs"]["plugin"] = home.plugin_root() is not None
    return shown


def issue_update(conn, key, body):
    allowed = ("status", "priority", "title", "labels", "body")
    unknown = [name for name in body if name not in allowed]
    if unknown:
        raise Refused(400, f"unknown fields: {', '.join(unknown)}")
    text_limit(body.get("title"), MAX_TITLE, "title")
    text_limit(body.get("body"), MAX_ISSUE_BODY, "body")
    labels = body.get("labels")
    add, remove = None, None
    if labels is not None:
        if not (isinstance(labels, list) and all(isinstance(label, str) for label in labels)) or len(labels) > 30:
            raise Refused(400, "labels must be a list of at most 30 strings")
        current = set(issues.get(conn, key)["labels"])
        add, remove = sorted(set(labels) - current), sorted(current - set(labels))
    if any(name in body for name in ("status", "priority", "title")) or add or remove:
        issues.set_fields(conn, key, body.get("status"), body.get("priority"), body.get("title"), add, remove)
    if "body" in body:
        issues.edit_text(conn, key, body["body"])
    return issues.show(conn, key)


def issue_create(conn, body):
    allowed = ("project", "title", "type", "priority", "labels", "body")
    unknown = [name for name in body if name not in allowed]
    if unknown:
        raise Refused(400, f"unknown fields: {', '.join(unknown)}")
    text_limit(body.get("title"), MAX_TITLE, "title")
    text_limit(body.get("body"), MAX_ISSUE_BODY, "body")
    labels = body.get("labels") or []
    if not (isinstance(labels, list) and all(isinstance(label, str) for label in labels)) or len(labels) > 30:
        raise Refused(400, "labels must be a list of at most 30 strings")
    if not isinstance(body.get("project"), str):
        raise Refused(400, "project is required")
    projects.require(conn, body["project"])
    return issues.add(conn, body["project"], body.get("title") or "", body.get("type") or "task",
                      body.get("priority") or "medium", labels, body.get("body") or "")


def number(value, label):
    if not re.match(r"^[0-9]+$", value):
        raise Refused(404, f"no {label} {value}")
    return int(value)


def get_routes(conn, route, query):
    project = single(query, "project")
    if route == "/api/runs":
        archived = single(query, "archived", "")
        return runs.listing(conn, project, "all" if archived == "all" else flag(archived))
    match = re.match(r"^/api/runs/([^/]+)$", route)
    if match:
        return run_detail(conn, match.group(1))
    if route == "/api/jobs":
        return active_jobs(conn, project)
    if route == "/api/questions":
        statuses = [status for status in (single(query, "status", "open,clarify") or "").split(",") if status]
        return question_entries(conn, statuses, project)
    if route == "/api/issues":
        statuses = [status for status in (single(query, "status", "") or "").split(",") if status]
        return issues.listing(conn, project, statuses or None, single(query, "type"), single(query, "label"),
                              single(query, "q"))
    match = re.match(r"^/api/issues/([A-Z][A-Z0-9]{1,9}-[0-9]+)$", route)
    if match:
        return issues.show(conn, match.group(1))
    if route == "/api/projects":
        return projects.listing(conn)
    match = re.match(r"^/api/projects/([a-z][a-z0-9-]{1,39})$", route)
    if match:
        return project_detail(conn, match.group(1))
    if route == "/api/catalogue":
        if project:
            projects.require(conn, project)
        return catalogue.show(home.load_config(), projects.load(project) if project else None)
    if route == "/api/improvements":
        statuses = [status for status in (single(query, "status", "") or "").split(",") if status]
        return improvements.listing(conn, None, project, statuses or None)
    if route == "/api/meta":
        table = usage.price_table()
        return {"version": VERSION, "home": home.root(), "prices": {"date": table["date"], "source": table["source"]}}
    raise Refused(404, f"no route {route}")


def post_routes(conn, route, body):
    match = re.match(r"^/api/questions/([^/]+)/(answer|clarify)$", route)
    if match:
        question_id = number(match.group(1), "question")
        text = text_limit(body.get("text"), MAX_TEXT, "text")
        if match.group(2) == "answer":
            return questions.answer(conn, question_id, text or "", delivered=False, author="dashboard")
        return questions.clarify(conn, question_id, text or "", delivered=False, author="dashboard")
    match = re.match(r"^/api/improvements/([^/]+)/decide$", route)
    if match:
        note = text_limit(body.get("note"), MAX_TEXT, "note")
        if not isinstance(body.get("status"), str):
            raise Refused(400, "status is required")
        return improvements.decide(conn, number(match.group(1), "improvement"), body["status"], note)
    match = re.match(r"^/api/runs/([^/]+)/archive$", route)
    if match:
        if not isinstance(body.get("archived"), bool):
            raise Refused(400, "archived must be true or false")
        if body["archived"]:
            return runs.archive(conn, match.group(1))
        return runs.unarchive(conn, match.group(1))
    if route == "/api/issues":
        return issue_create(conn, body)
    match = re.match(r"^/api/issues/([A-Z][A-Z0-9]{1,9}-[0-9]+)$", route)
    if match:
        return issue_update(conn, match.group(1), body)
    raise Refused(404, f"no route {route}")


class Handler(BaseHTTPRequestHandler):
    server_version = "factory"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        if not self.server.quiet:
            sys.stderr.write("%s %s\n" % (self.log_date_time_string(), format % args))

    def allowed_hosts(self):
        port = self.server.server_address[1]
        return (f"127.0.0.1:{port}", f"localhost:{port}")

    def extra_host(self, value, origin=False):
        # A name from dashboard.hosts, such as a tailnet name behind `tailscale serve`, on any port.
        if origin:
            scheme, _, value = value.partition("://")
            if scheme not in ("http", "https"):
                return False
        name, _, port = value.partition(":")
        return name in self.server.hosts and (port == "" or port.isdigit())

    def send(self, status, payload, content_type="application/json; charset=utf-8"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for name, value in HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def refuse(self, status, message):
        self.send(status, {"error": message})

    def body(self):
        kind = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if kind != "application/json":
            raise Refused(415, "POST bodies must be application/json")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Refused(400, "bad Content-Length")
        if length > MAX_BODY:
            raise Refused(413, f"the body is larger than {MAX_BODY} bytes")
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Refused(400, "the body is not valid JSON")
        if not isinstance(data, dict):
            raise Refused(400, "the body must be a JSON object")
        return data

    def handle_any(self, method):
        try:
            host = self.headers.get("Host") or ""
            if host not in self.allowed_hosts() and not self.extra_host(host):
                raise Refused(403, "this server answers only on 127.0.0.1, localhost and the names in dashboard.hosts")
            origin = self.headers.get("Origin")
            if method == "POST" and origin is not None and origin not in tuple(f"http://{host}" for host in self.allowed_hosts()) and not self.extra_host(origin, origin=True):
                raise Refused(403, "cross-origin POSTs are refused")
            parsed = urlsplit(self.path)
            route = parsed.path
            if method in ("GET", "HEAD") and route in ("/", "/index.html"):
                with open(dashboard_path(), "rb") as handle:
                    return self.send(200, handle.read(), "text/html; charset=utf-8")
            if method in ("GET", "HEAD") and route == "/api/file":
                path = guarded_file(single(parse_qs(parsed.query), "path"), TEXT_FOLDERS, TEXT_SUFFIXES)
                return self.send(200, {"path": path, "text": read_text(path)})
            if method in ("GET", "HEAD") and route == "/api/image":
                path = guarded_file(single(parse_qs(parsed.query), "path"), ("runs",), (".png",))
                with open(path, "rb") as handle:
                    data = handle.read()
                if not data.startswith(PNG):
                    raise Refused(415, "the file is not a PNG")
                return self.send(200, data, "image/png")
            body = self.body() if method == "POST" else None
            conn = home.connect()
            try:
                if method == "POST":
                    result = post_routes(conn, route, body)
                else:
                    result = get_routes(conn, route, parse_qs(parsed.query))
            finally:
                conn.close()
            self.send(200, result)
        except Refused as refused:
            self.refuse(refused.status, refused.message)
        except FactoryError as error:
            self.refuse(404 if error.message.startswith("no ") else 400, error.message)
        except Exception as error:
            if os.environ.get("FACTORY_DEBUG"):
                import traceback
                traceback.print_exc()
            self.refuse(500, f"internal error: {type(error).__name__}")

    def do_GET(self):
        self.handle_any("GET")

    def do_HEAD(self):
        self.handle_any("HEAD")

    def do_POST(self):
        self.handle_any("POST")

    def do_PUT(self):
        self.refuse(405, "method not allowed")

    do_DELETE = do_PUT
    do_PATCH = do_PUT


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port, quiet=False, hosts=()):
        super().__init__(("127.0.0.1", port), Handler)
        self.quiet = quiet
        self.hosts = frozenset(hosts)


def poll_once():
    conn = home.connect()
    try:
        return runs.check_prs(conn)
    finally:
        conn.close()


def poller(stop, interval=POLL_SECONDS):
    while not stop.wait(interval):
        try:
            poll_once()
        except Exception as error:
            sys.stderr.write(f"factory: PR poll failed: {type(error).__name__}: {error}\n")


def serve(port, quiet=False, hosts=()):
    try:
        server = Server(port, quiet, hosts)
    except OSError as error:
        raise FactoryError(f"cannot listen on 127.0.0.1:{port}: {error.strerror}")
    stop = threading.Event()
    threading.Thread(target=poller, args=(stop,), daemon=True).start()
    address = f"http://127.0.0.1:{server.server_address[1]}/"
    sys.stderr.write(f"factory dashboard on {address} (Ctrl+C stops it)\n")
    sys.stderr.flush()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()
    return {"url": address, "stopped_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
