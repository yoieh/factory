import json
import os
import re
import shutil
import subprocess

from . import FactoryError
from .util import expand, read_json

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
VENDOR_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SETTING_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")

SLOTS = ("tracker", "code_host", "design")
CAPABILITIES = {
    "tracker": ("fetch_issue", "import", "sync", "comment"),
    "code_host": ("push", "open_pr", "pr_state", "pr_template", "pr_checks", "land_pr", "publish_release", "publish_package"),
    "design": ("fetch_design",),
}
VIAS = ("mcp", "cli", "skill", "git", "file", "script", "core")
REQUIRES = ("bin", "env", "files", "mcp", "skill")
SETTING_TYPES = ("string", "int", "bool", "list")
PATTERN_KEYS = {
    "tracker": ("key_pattern", "url_pattern"),
    "code_host": ("pr_url",),
    "design": ("design_url", "scrub"),
}
PLACEHOLDERS = (
    "project", "repo", "repo_path", "remote_url", "source_branch", "target_branch",
    "pr_file", "title", "pr_url", "issue_key", "external_key", "external_keys", "head_sha", "action",
    "merge_sha", "tag", "artifacts", "notes_file", "release_mode",
)
RESERVED_SETTINGS = PLACEHOLDERS + ("adapter", "write", "patterns")
DEFAULT_SLOTS = {"tracker": "local", "code_host": "none"}
ADAPTER_KEYS = ("name", "slot", "version", "vendors", "capabilities", "settings", "patterns")


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def pattern_problems(key, value, slot):
    problems = []
    values = value if key == "scrub" else [value]
    if key == "scrub" and not isinstance(value, list):
        return [f"patterns.scrub must be a list of patterns"]
    for pattern in values:
        if not isinstance(pattern, str) or not pattern:
            problems.append(f"patterns.{key} must be a non-empty string")
            continue
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            problems.append(f"patterns.{key} does not compile: {error}")
            continue
        if compiled.search("") is not None:
            problems.append(f"patterns.{key} must not match the empty string")
        if re.search(r"(?<!\\)(?:\\\\)*\\d", pattern):
            problems.append(f"patterns.{key} uses \\d; use [0-9]")
        if key == "url_pattern" and "key" not in compiled.groupindex:
            problems.append("patterns.url_pattern needs a named group (?P<key>...)")
    return problems


def setting_value_problems(name, spec, value):
    kind = spec.get("type")
    if value is None:
        return []
    if kind == "string" and not isinstance(value, str):
        return [f"setting {name} must be a string"]
    if kind == "int" and not is_int(value):
        return [f"setting {name} must be an integer"]
    if kind == "bool" and not isinstance(value, bool):
        return [f"setting {name} must be true or false"]
    if kind == "list" and not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
        return [f"setting {name} must be a list of strings"]
    return []


def validate(data, folder_name):
    if not isinstance(data, dict):
        return ["adapter.json must be a JSON object"]
    problems = [f"unknown key {key!r}" for key in data if key not in ADAPTER_KEYS]
    name = data.get("name")
    if not (isinstance(name, str) and NAME_RE.match(name)):
        problems.append("name must match ^[a-z][a-z0-9-]{1,39}$")
    elif name != folder_name:
        problems.append(f"name {name!r} must equal the folder name {folder_name!r}")
    slot = data.get("slot")
    if slot not in SLOTS:
        problems.append(f"slot must be one of {', '.join(SLOTS)}")
    if "version" in data and not is_int(data["version"]):
        problems.append("version must be an integer")
    vendors = data.get("vendors", [])
    if not isinstance(vendors, list) or not all(isinstance(v, str) and VENDOR_RE.match(v) for v in vendors):
        problems.append("vendors must be a list of lowercase words (^[a-z0-9][a-z0-9_-]*$)")
    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        problems.append("settings must be an object")
        settings = {}
    for setting, spec in settings.items():
        if not SETTING_RE.match(setting):
            problems.append(f"setting name {setting!r} must match ^[a-z][a-z0-9_]*$")
        if setting in RESERVED_SETTINGS:
            problems.append(f"setting name {setting!r} is reserved")
        if not isinstance(spec, dict):
            problems.append(f"setting {setting} must be an object")
            continue
        for key in spec:
            if key not in ("type", "default", "secret"):
                problems.append(f"setting {setting}: unknown key {key!r}")
        if spec.get("type") not in SETTING_TYPES:
            problems.append(f"setting {setting}: type must be one of {', '.join(SETTING_TYPES)}")
        elif "default" in spec:
            problems.extend(setting_value_problems(setting, spec, spec["default"]))
        if "secret" in spec and not isinstance(spec["secret"], bool):
            problems.append(f"setting {setting}: secret must be true or false")
    capabilities = data.get("capabilities", {})
    if not isinstance(capabilities, dict):
        problems.append("capabilities must be an object")
        capabilities = {}
    for capability, spec in capabilities.items():
        problems.extend(capability_problems(slot, capability, spec, settings))
    patterns = data.get("patterns", {})
    if not isinstance(patterns, dict):
        problems.append("patterns must be an object")
    else:
        for key, value in patterns.items():
            if slot in PATTERN_KEYS and key not in PATTERN_KEYS[slot]:
                problems.append(f"patterns.{key} is not a {slot} pattern")
                continue
            problems.extend(pattern_problems(key, value, slot))
    return problems


def capability_problems(slot, capability, spec, settings):
    label = f"capabilities.{capability}"
    if slot in CAPABILITIES and capability not in CAPABILITIES[slot]:
        return [f"{label} is not a {slot} capability"]
    if not isinstance(spec, dict):
        return [f"{label} must be an object"]
    problems = [f"{label}: unknown key {key!r}" for key in spec if key not in ("via", "requires", "command", "path")]
    via = spec.get("via")
    if via not in VIAS:
        problems.append(f"{label}: via must be one of {', '.join(VIAS)}")
    requires = spec.get("requires", {})
    if not isinstance(requires, dict):
        problems.append(f"{label}: requires must be an object")
    else:
        for key, value in requires.items():
            if key not in REQUIRES:
                problems.append(f"{label}: unknown requires key {key!r}")
            elif not (isinstance(value, list) and all(isinstance(item, str) and item for item in value)):
                problems.append(f"{label}: requires.{key} must be a list of strings")
    if via == "script":
        command = spec.get("command")
        if not (isinstance(command, list) and command and all(isinstance(part, str) for part in command)):
            problems.append(f"{label}: a script needs command, a list of strings")
        else:
            for part in command:
                for name in PLACEHOLDER_RE.findall(part):
                    if name not in PLACEHOLDERS and name not in settings:
                        problems.append(f"{label}: unknown placeholder {{{name}}}")
    elif "command" in spec:
        problems.append(f"{label}: command is only for via script")
    if via == "file":
        if not (isinstance(spec.get("path"), str) and spec["path"]):
            problems.append(f"{label}: a file needs path")
    elif "path" in spec:
        problems.append(f"{label}: path is only for via file")
    return problems


def load(folder):
    path = os.path.join(folder, "adapter.json")
    data = read_json(path)
    problems = validate(data, os.path.basename(folder.rstrip("/")))
    return data, problems


def integration_problems(slot, entry, adapter):
    label = f"integrations.{slot}"
    problems = []
    settings = adapter.get("settings", {})
    for key, value in entry.items():
        if key == "adapter":
            continue
        if key == "write":
            if slot != "tracker":
                problems.append(f"{label}: write is only for trackers")
            elif not isinstance(value, bool):
                problems.append(f"{label}: write must be true or false")
            continue
        if key == "patterns":
            if not isinstance(value, dict):
                problems.append(f"{label}: patterns must be an object")
                continue
            for pattern_key, pattern in value.items():
                if pattern_key not in PATTERN_KEYS[slot]:
                    problems.append(f"{label}: patterns.{pattern_key} is not a {slot} pattern")
                    continue
                problems.extend(f"{label}: {problem}" for problem in pattern_problems(pattern_key, pattern, slot))
            continue
        if key not in settings:
            problems.append(
                f"{label}: {key!r} is not a setting of adapter {adapter['name']} "
                "(credentials go in the environment or a credential file, never in project config)"
            )
            continue
        problems.extend(f"{label}: {problem}" for problem in setting_value_problems(key, settings[key], value))
    return problems


def resolved_settings(adapter, entry):
    values = {}
    for name, spec in adapter.get("settings", {}).items():
        values[name] = entry.get(name, spec.get("default"))
    return values


def resolved_patterns(adapter, entry):
    patterns = dict(adapter.get("patterns", {}))
    patterns.update(entry.get("patterns", {}))
    return patterns


def read_env_files(files, names):
    found = {}
    for entry in files:
        try:
            path = expand(entry)
        except FactoryError:
            continue
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line in lines:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.startswith("export "):
                text = text[len("export "):].strip()
            key, sep, value = text.partition("=")
            key = key.strip()
            if not sep or key not in names or key in found:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            found[key] = value
    return found


def script_files(command):
    named = []
    for part in command[1:] if command else []:
        if "{" in part or part.startswith("-"):
            continue
        if "/" in part or part.endswith((".py", ".sh")):
            named.append(part)
    return named


def capability_status(folder, spec, repos):
    via = spec.get("via")
    requires = spec.get("requires", {})
    result = {"via": via, "requires": requires}
    if via == "script":
        missing = [name for name in script_files(spec.get("command", [])) if not os.path.exists(os.path.join(folder, name))]
        if missing:
            result.update(status="not built", reason=f"script not built: {', '.join(missing)}")
            return result
    if via == "file":
        if not os.path.isfile(os.path.join(folder, spec.get("path", ""))):
            result.update(status="missing", reason=f"file missing: {spec.get('path')}")
            return result
    missing_bins = [name for name in requires.get("bin", []) if shutil.which(name) is None]
    if missing_bins:
        result.update(status="missing", reason=f"not on PATH: {', '.join(missing_bins)}")
        return result
    names = requires.get("env", [])
    if names:
        from_files = read_env_files(requires.get("files", []), names)
        unset = [name for name in names if not os.environ.get(name) and name not in from_files]
        if unset:
            result.update(status="missing", reason=f"not set: {', '.join(unset)}")
            return result
    if via in ("mcp", "skill"):
        result["status"] = "check_in_session"
        return result
    if via == "git":
        per_repo = {repo["name"]: ("available" if repo.get("has_origin") else "missing") for repo in repos}
        result["repos"] = per_repo
        if per_repo and all(state == "available" for state in per_repo.values()):
            result["status"] = "available"
        elif any(state == "available" for state in per_repo.values()):
            result.update(status="partial", reason="some repos have no origin remote")
        elif per_repo:
            result.update(status="missing", reason="no repo has an origin remote")
        else:
            result["status"] = "available"
        return result
    result["status"] = "available"
    return result


def extract_key(patterns, argument):
    text = (argument or "").strip()
    key = None
    url = None
    key_pattern = patterns.get("key_pattern")
    url_pattern = patterns.get("url_pattern")
    if key_pattern and re.fullmatch(key_pattern, text):
        key = text
    elif url_pattern:
        match = re.match(url_pattern, text)
        if match and match.groupdict().get("key"):
            key = match.group("key")
            url = text
    if key is None:
        raise FactoryError(f"{text!r} is not a key or issue URL of the project's tracker")
    if not SAFE_KEY.match(key):
        raise FactoryError(f"key {key!r} has characters a branch name cannot hold")
    return key, url


def finds(patterns, name, text):
    pattern = patterns.get(name)
    if not pattern:
        return []
    found = []
    for match in re.finditer(pattern, text or ""):
        if match.group(0) not in found:
            found.append(match.group(0))
    return found


TIMEOUT = 30
REPO_PLACEHOLDERS = ("repo", "repo_path", "remote_url")
OUTPUTS = {
    "pr_state": lambda data: data.get("state") in ("OPEN", "MERGED", "DECLINED", "SUPERSEDED") and isinstance(data.get("url"), str),
    "open_pr": lambda data: data.get("action") in ("created", "updated") and isinstance(data.get("url"), str),
    "pr_checks": lambda data: (
        data.get("state") in ("OPEN", "MERGED", "DECLINED") and isinstance(data.get("url"), str)
        and isinstance(data.get("draft"), bool) and isinstance(data.get("head_sha"), str)
        and data.get("mergeable") in ("MERGEABLE", "CONFLICTING", "UNKNOWN")
        and data.get("checks") in ("passing", "pending", "failing", "none")
        and isinstance(data.get("failing"), list) and isinstance(data.get("pending"), list)),
    "land_pr": lambda data: data.get("action") in ("ready", "merged") and isinstance(data.get("url"), str),
    "publish_release": lambda data: data.get("action") in ("created", "exists") and isinstance(data.get("url"), str),
    "publish_package": lambda data: data.get("action") == "pushed" and isinstance(data.get("ref"), str),
    "sync": lambda data: isinstance(data.get("issues"), list) and all(
        isinstance(item, dict) and isinstance(item.get("external_key"), str) for item in data["issues"]),
    "fetch_issue": lambda data: isinstance(data.get("title"), str),
}
FILL_IN = "<fill in"


def setting_text(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(value)
    return str(value)


def fill(command, values):
    missing = []

    def replace(match):
        name = match.group(1)
        value = values.get(name)
        if value is None:
            missing.append(name)
            return ""
        return value

    filled = [PLACEHOLDER_RE.sub(replace, part) for part in command]
    if missing:
        names = ", ".join("{" + name + "}" for name in dict.fromkeys(missing))
        raise FactoryError(f"placeholder {names} has no value; set the setting or pass --<name> <value>")
    return filled


def redact(text, secrets):
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, "***")
    return text


def script_values(project, entry, repo=None, extra=None):
    values = {name: setting_text(value) for name, value in entry.get("settings", {}).items()}
    values["project"] = project["name"]
    values.update({name: "" for name in REPO_PLACEHOLDERS})
    if repo:
        values["repo"] = repo.get("name") or repo.get("repo") or ""
        values["repo_path"] = repo.get("path") or ""
        values["remote_url"] = repo.get("remote") or ""
    for name, value in (extra or {}).items():
        values[name] = value
    return values


def run_script(folder, capability, spec, values, timeout=None):
    timeout = timeout or TIMEOUT
    command = fill(spec["command"], values)
    requires = spec.get("requires", {})
    names = requires.get("env", [])
    environment = dict(os.environ)
    for name, value in read_env_files(requires.get("files", []), names).items():
        environment.setdefault(name, value)
    secrets = [environment[name] for name in names if environment.get(name)]
    try:
        done = subprocess.run(command, cwd=folder, env=environment, capture_output=True, text=True,
                              timeout=timeout, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        return {"status": "unknown", "code": None, "reason": f"{command[0]} is not installed"}
    except subprocess.TimeoutExpired:
        return {"status": "unknown", "code": None, "reason": f"the script timed out after {timeout} seconds"}
    if done.returncode == 3:
        return {"status": "credentials", "code": 3, "reason": "credentials missing"}
    if done.returncode != 0:
        lines = [line for line in done.stderr.strip().splitlines() if line.strip()]
        reason = redact(lines[-1] if lines else f"exit {done.returncode}", secrets)
        return {"status": "unknown", "code": done.returncode, "reason": reason[:300]}
    try:
        data = json.loads(done.stdout)
    except json.JSONDecodeError:
        return {"status": "unknown", "code": 0, "reason": "the script did not print one JSON object"}
    if not isinstance(data, dict):
        return {"status": "unknown", "code": 0, "reason": "the script did not print one JSON object"}
    check = OUTPUTS.get(capability)
    if check and not check(data):
        return {"status": "unknown", "code": 0, "reason": f"the script's output is not a valid {capability} result"}
    return {"status": "ok", "code": 0, "data": data}


def project_adapter(project, name):
    for slot, entry in project["integrations"].items():
        if entry["adapter"] == name:
            return entry
    raise FactoryError(f"project {project['name']} does not use adapter {name}")


def script_capability(entry, capability):
    data = read_json(entry["adapter_json"])
    spec = data.get("capabilities", {}).get(capability)
    if spec is None:
        raise FactoryError(f"adapter {entry['adapter']} has no {capability}")
    if spec.get("via") != "script":
        raise FactoryError(f"{capability} of adapter {entry['adapter']} is done by the orchestrator (via {spec.get('via')}), not by a script")
    state = entry["capabilities"][capability]
    if state.get("status") not in ("available",):
        raise FactoryError(f"{capability} of adapter {entry['adapter']} is {state.get('status')}: {state.get('reason')}")
    return spec


def run_capability(project, entry, capability, repo=None, extra=None, timeout=None):
    spec = script_capability(entry, capability)
    return run_script(entry["dir"], capability, spec, script_values(project, entry, repo, extra), timeout)


SKELETON_CAPABILITIES = {
    "tracker": {
        "fetch_issue": {"via": "mcp", "requires": {"mcp": ["mcp__<fill in: server>__<fill in: tool that reads one issue>"]}},
        "import": {"via": "core"},
    },
    "code_host": {
        "push": {"via": "git"},
        "open_pr": {"via": "mcp", "requires": {"mcp": ["mcp__<fill in: server>__<fill in: tool that opens or updates a draft PR>"]}},
    },
    "design": {
        "fetch_design": {"via": "mcp", "requires": {"mcp": ["mcp__<fill in: server>__<fill in: tool that reads one frame>"]}},
    },
}
SKELETON_SECTIONS = {
    "fetch_issue": [
        "1. <fill in: the MCP tool to call, with which arguments, to read one issue by key or URL.>",
        "2. <fill in: where each field of issue.md comes from: title, URL, type, status, assignee, labels, parent,",
        "   description, comments, linked issues, attachment names and design links.>",
        "3. When the issue is done or closed, or assigned to someone else, tell the user and ask whether to go on.",
        "4. Then `run new --external-key <key> --external-url <url>`, write `issue.md` and copy it to the mirror with",
        "   `issue edit <issue_key> --body-file <issue_file>`.",
    ],
    "push": [
        "Push only repos with `can_push`, and never force:",
        "",
        "```sh",
        "git -C \"<dir>\" push -u origin \"<branch>\"",
        "```",
    ],
    "open_pr": [
        "1. <fill in: the MCP tool that finds an open PR for the same source and target branch.>",
        "2. <fill in: the MCP tool that opens a draft PR with the title and the body of the pr file, and the one that",
        "   updates an open PR in place.>",
        "3. Store the url: `run pr <run-id> <url> --repo <repo>` in a run, `improvement pr <ids> --url <url> --branch",
        "   <branch>` in Improve.",
    ],
    "fetch_design": [
        "1. <fill in: the MCP tool that lists the frames behind a design link, and how to split a page into frames.>",
        "2. Per frame, `design target <run-id> --name <frame>` gives the png and md paths.",
        "3. <fill in: the MCP tool for the screenshot, and how to save it to the png path without printing its URL.>",
        "4. <fill in: the MCP tool for texts, layout and styles, written to the md path in the Design files format.>",
        "5. Then `design check <run-id>`.",
    ],
}


def skeleton_json(name, slot):
    return {
        "name": name,
        "slot": slot,
        "version": 1,
        "vendors": [],
        "capabilities": SKELETON_CAPABILITIES[slot],
        "settings": {},
        "patterns": {},
    }


def skeleton_md(name, slot):
    lines = [
        f"# {name}",
        "",
        f"<fill in: one paragraph on what this {slot.replace('_', ' ')} adapter does and which MCP server it uses.>",
        "",
        "## Setup",
        "",
        "- <fill in: what the user installs or connects, and how to check that the MCP tools are there.>",
        "",
    ]
    for capability, spec in SKELETON_CAPABILITIES[slot].items():
        if spec["via"] == "core":
            continue
        lines += [f"## {capability}", ""] + SKELETON_SECTIONS[capability] + [""]
    lines += [
        "## Never",
        "",
        "- Print or store a token, a credential or a short-lived signed URL.",
        "- Reach the service any other way than the tools above.",
        "- <fill in: what this service must never be asked to do, for example closing or editing issues.>",
        "",
    ]
    return "\n".join(lines)


def new(name, slot, folder):
    if not NAME_RE.match(name or ""):
        raise FactoryError("adapter name must match ^[a-z][a-z0-9-]{1,39}$", code=2)
    if slot not in SLOTS:
        raise FactoryError(f"--slot must be one of {', '.join(SLOTS)}", code=2)
    target = os.path.join(folder, "adapters", name)
    if os.path.exists(target):
        raise FactoryError(f"{target} exists; pick another name or edit it there")
    os.makedirs(target)
    json_path = os.path.join(target, "adapter.json")
    md_path = os.path.join(target, "adapter.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(skeleton_json(name, slot), indent=2) + "\n")
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(skeleton_md(name, slot))
    return {"name": name, "slot": slot, "dir": target, "adapter_json": json_path, "adapter_md": md_path,
            "next": "fill in every <fill in: ...> placeholder, then run factory catalogue check"}


def unfilled(folder):
    found = []
    for name in ("adapter.json", "adapter.md"):
        path = os.path.join(folder, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            count = handle.read().count(FILL_IN)
        if count:
            found.append((path, count))
    return found


FINAL_STATES = ("MERGED", "DECLINED", "SUPERSEDED")


def check_pr_url(project, url):
    host = project["integrations"].get("code_host") or {}
    pattern = host.get("patterns", {}).get("pr_url")
    if not re.match(r"^https?://\S+$", url or ""):
        raise FactoryError(f"not a PR url: {url}")
    if pattern and not re.match(pattern, url):
        raise FactoryError(f"{url} does not match the {host['adapter']} pr_url pattern")


def read_pr_state(project, url, repo=None):
    host = project["integrations"].get("code_host")
    name = host["adapter"] if host else "none"
    status = (host or {}).get("capabilities", {}).get("pr_state")
    if not status:
        return {"state": "unchecked", "reason": f"the code host {name} has no pr_state"}
    if status["via"] != "script":
        return {"state": "unchecked", "reason": f"pr_state of {name} is via {status['via']}; the core reads only scripts"}
    if status["status"] != "available":
        return {"state": "unchecked", "reason": f"pr_state of {name} is {status['status']}: {status.get('reason')}"}
    outcome = run_capability(project, host, "pr_state", repo, {"pr_url": url})
    if outcome["status"] == "ok":
        return {"state": outcome["data"]["state"]}
    if outcome["status"] == "credentials":
        return {"state": "unchecked", "reason": f"pr_state of {name}: credentials missing"}
    return {"state": "unknown", "reason": outcome["reason"]}
