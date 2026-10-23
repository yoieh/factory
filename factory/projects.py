import copy
import json
import os
import re

from . import FactoryError, adapters, catalogue, db, home
from .util import (
    dotted_set, expand, git_default_branch, git_origin, git_toplevel, normalise_remote,
    now, read_json, same_path, valid_branch_name, write_json, write_text,
)

NAME_RE = home.NAME_RE
KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
TARGET_RE = re.compile(r"^[a-z][a-z0-9_-]{0,39}$")
TOP_KEYS = (
    "name", "key", "description", "repos", "repo_root", "integrations", "knowledge",
    "step_skills", "catalogue_dirs", "improvement_targets", "defaults",
)
REPO_KEYS = ("name", "path", "remote", "default_branch", "role", "test", "setup", "after_merge")
AFTER_MERGE_KEYS = ("deploy", "watch", "build", "artifact", "release", "package")
RELEASE_MODES = ("off", "draft", "publish")
PACKAGE_MODES = ("off", "publish")
KNOWLEDGE_KEYS = ("label", "read_first", "per_repo", "architecture")
TARGET_KEYS = ("repo", "base", "label")
DEFAULTS = {"confirm_plan": False, "branch_prefix": "feature/", "require_code_review": True, "land": "off", "land_fixes": 2}
DEFAULT_KEYS = ("parallel", "max_rounds", "confirm_plan", "branch_prefix", "require_code_review", "land", "land_fixes")
LAND_MODES = ("off", "ready", "merge")
STEP_NAMES = {
    1: "--project",
    2: "FACTORY_PROJECT",
    3: "pin file",
    4: "linked repo",
    5: "default_project",
    6: "ad-hoc",
}


def config_path(name):
    return home.path("projects", name, "project.json")


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def path_problem(label, value):
    try:
        expand(value)
    except FactoryError as error:
        return f"{label}: {error.message}"
    return None


def path_list_problems(label, value):
    if not isinstance(value, list):
        return [f"{label} must be a list of paths"]
    return [problem for problem in (path_problem(label, entry) for entry in value) if problem]


def find_adapter(config, project, name):
    folder = None
    for _, base, scope in catalogue.dirs(config, project):
        if scope != "all":
            continue
        candidate = os.path.join(base, "adapters", name)
        if os.path.isfile(os.path.join(candidate, "adapter.json")):
            folder = candidate
    return folder


def validate(project, folder_name=None, config=None):
    config = config or home.load_config()
    problems = []
    if not isinstance(project, dict):
        return ["project.json must be a JSON object"]
    problems.extend(f"unknown key {key!r}" for key in project if key not in TOP_KEYS)
    name = project.get("name")
    if not (isinstance(name, str) and NAME_RE.match(name)):
        problems.append("name must match ^[a-z][a-z0-9-]{1,39}$")
    elif folder_name and name != folder_name:
        problems.append(f"name {name!r} must equal the folder name {folder_name!r}")
    if not (isinstance(project.get("key"), str) and KEY_RE.match(project["key"])):
        problems.append("key must match ^[A-Z][A-Z0-9]{1,9}$")
    if "description" in project and not isinstance(project["description"], str):
        problems.append("description must be a string")
    repos = project.get("repos")
    repo_names = []
    if not isinstance(repos, list):
        problems.append("repos is required: a list, which may be empty")
        repos = []
    for number, repo in enumerate(repos, start=1):
        label = f"repos[{number}]"
        if not isinstance(repo, dict):
            problems.append(f"{label} must be an object")
            continue
        problems.extend(f"{label}: unknown key {key!r}" for key in repo if key not in REPO_KEYS)
        repo_name = repo.get("name")
        if not (isinstance(repo_name, str) and REPO_RE.match(repo_name)):
            problems.append(f"{label}: name must match ^[A-Za-z0-9][A-Za-z0-9._-]*$")
        elif repo_name in repo_names:
            problems.append(f"{label}: duplicate repo name {repo_name!r}")
        else:
            repo_names.append(repo_name)
        if "path" not in repo:
            problems.append(f"{label}: path is required")
        else:
            problem = path_problem(f"{label}.path", repo["path"])
            if problem:
                problems.append(problem)
        for key in ("remote", "role", "test", "setup"):
            if key in repo and not isinstance(repo[key], str):
                problems.append(f"{label}: {key} must be a string")
        if "default_branch" in repo and not valid_branch_name(repo["default_branch"]):
            problems.append(f"{label}: default_branch is not a valid branch name")
        if "after_merge" in repo:
            problems.extend(after_merge_problems(f"{label}.after_merge", repo["after_merge"]))
    if "repo_root" in project:
        problem = path_problem("repo_root", project["repo_root"])
        if problem:
            problems.append(problem)
    if "catalogue_dirs" in project:
        problems.extend(path_list_problems("catalogue_dirs", project["catalogue_dirs"]))
    knowledge = project.get("knowledge", {})
    if not isinstance(knowledge, dict):
        problems.append("knowledge must be an object")
    else:
        problems.extend(f"knowledge: unknown key {key!r}" for key in knowledge if key not in KNOWLEDGE_KEYS)
        if "label" in knowledge and not isinstance(knowledge["label"], str):
            problems.append("knowledge.label must be a string")
        for key in ("read_first", "per_repo", "architecture"):
            if key in knowledge:
                problems.extend(path_list_problems(f"knowledge.{key}", knowledge[key]))
    skills = project.get("step_skills", {})
    if not isinstance(skills, dict):
        problems.append("step_skills must be an object")
    else:
        for skill_key, entries in skills.items():
            label = f"step_skills.{skill_key}"
            if not isinstance(entries, list):
                problems.append(f"{label} must be a list of {{path, when}}")
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    problems.append(f"{label}: each entry must be an object")
                    continue
                problems.extend(f"{label}: unknown key {key!r}" for key in entry if key not in ("path", "when"))
                if "path" not in entry:
                    problems.append(f"{label}: path is required")
                else:
                    problem = path_problem(f"{label}.path", entry["path"])
                    if problem:
                        problems.append(problem)
                if "when" in entry and not isinstance(entry["when"], str):
                    problems.append(f"{label}: when must be a string")
    targets = project.get("improvement_targets", {})
    if not isinstance(targets, dict):
        problems.append("improvement_targets must be an object")
    else:
        for target, spec in targets.items():
            label = f"improvement_targets.{target}"
            if not TARGET_RE.match(target):
                problems.append(f"{label}: name must match ^[a-z][a-z0-9_-]{{0,39}}$")
            if not isinstance(spec, dict):
                problems.append(f"{label} must be an object")
                continue
            problems.extend(f"{label}: unknown key {key!r}" for key in spec if key not in TARGET_KEYS)
            if "repo" in spec:
                if target in ("factory", "process"):
                    problems.append(f"{label}: {target} has no repo")
                elif spec["repo"] not in repo_names:
                    problems.append(f"{label}: repo {spec['repo']!r} is not a repo of the project")
            if "base" in spec and not (isinstance(spec["base"], str) and (
                    spec["base"] in ("current", "default") or valid_branch_name(spec["base"]))):
                problems.append(f"{label}: base must be current, default or a branch name")
            if "label" in spec and not isinstance(spec["label"], str):
                problems.append(f"{label}: label must be a string")
    defaults = project.get("defaults", {})
    if not isinstance(defaults, dict):
        problems.append("defaults must be an object")
    else:
        problems.extend(f"defaults: unknown key {key!r}" for key in defaults if key not in DEFAULT_KEYS)
        if "parallel" in defaults and not (is_int(defaults["parallel"]) and defaults["parallel"] >= 1):
            problems.append("defaults.parallel must be an integer of 1 or more")
        if "max_rounds" in defaults and not (is_int(defaults["max_rounds"]) and 1 <= defaults["max_rounds"] <= 10):
            problems.append("defaults.max_rounds must be an integer from 1 to 10")
        for key in ("confirm_plan", "require_code_review"):
            if key in defaults and not isinstance(defaults[key], bool):
                problems.append(f"defaults.{key} must be true or false")
        if "branch_prefix" in defaults and not isinstance(defaults["branch_prefix"], str):
            problems.append("defaults.branch_prefix must be a string")
        if "land" in defaults and defaults["land"] not in LAND_MODES:
            problems.append(f"defaults.land must be one of {', '.join(LAND_MODES)}")
        if "land_fixes" in defaults and not (is_int(defaults["land_fixes"]) and 0 <= defaults["land_fixes"] <= 5):
            problems.append("defaults.land_fixes must be an integer from 0 to 5")
    if problems:
        return problems
    normal = normalise(project)
    integrations = project.get("integrations", {})
    if not isinstance(integrations, dict):
        return ["integrations must be an object"]
    for slot, entry in integrations.items():
        if slot not in adapters.SLOTS:
            problems.append(f"integrations: unknown slot {slot!r}")
            continue
        if not isinstance(entry, dict) or not isinstance(entry.get("adapter"), str):
            problems.append(f"integrations.{slot} needs an adapter name")
            continue
        folder = find_adapter(config, normal, entry["adapter"])
        if not folder:
            problems.append(f"integrations.{slot}: no adapter {entry['adapter']!r} in the catalogue")
            continue
        try:
            data, adapter_problems = adapters.load(folder)
        except FactoryError as error:
            problems.append(f"integrations.{slot}: {error.message}")
            continue
        if adapter_problems:
            problems.append(f"integrations.{slot}: adapter {entry['adapter']} is invalid: {'; '.join(adapter_problems)}")
            continue
        if data["slot"] != slot:
            problems.append(f"integrations.{slot}: adapter {entry['adapter']} is a {data['slot']} adapter")
            continue
        problems.extend(adapters.integration_problems(slot, entry, data))
    return problems


def after_merge_problems(label, after):
    if not isinstance(after, dict):
        return [f"{label} must be an object"]
    problems = [f"{label}: unknown key {key!r}" for key in after if key not in AFTER_MERGE_KEYS]
    if "watch" in after and not isinstance(after["watch"], bool):
        problems.append(f"{label}.watch must be true or false")
    for key in ("build", "deploy"):
        if key in after and not (isinstance(after[key], str) and after[key].strip()):
            problems.append(f"{label}.{key} must be a non-empty string")
    if "artifact" in after:
        artifacts = after["artifact"] if isinstance(after["artifact"], list) else [after["artifact"]]
        if not artifacts or not all(isinstance(path, str) and path.strip() for path in artifacts):
            problems.append(f"{label}.artifact must be a path or a non-empty list of paths")
        else:
            names = [os.path.basename(path.replace("\\", "/").rstrip("/")) for path in artifacts]
            if any(os.path.isabs(path) or ".." in path.replace("\\", "/").split("/") or "," in path for path in artifacts):
                problems.append(f"{label}.artifact must be paths inside the repo, without commas")
            elif len(set(names)) != len(names):
                problems.append(f"{label}.artifact: every path needs its own file name")
    if "release" in after and after["release"] not in RELEASE_MODES:
        problems.append(f"{label}.release must be one of {', '.join(RELEASE_MODES)}")
    if "package" in after and after["package"] not in PACKAGE_MODES:
        problems.append(f"{label}.package must be one of {', '.join(PACKAGE_MODES)}")
    if "artifact" in after and "build" not in after:
        problems.append(f"{label}.artifact needs build")
    for key in ("release", "package"):
        if after.get(key, "off") != "off" and "artifact" not in after:
            problems.append(f"{label}.{key} needs build and artifact")
    return problems


def normalise(project):
    normal = copy.deepcopy(project)
    for repo in normal.get("repos", []):
        repo["path"] = expand(repo["path"])
    if "repo_root" in normal:
        normal["repo_root"] = expand(normal["repo_root"])
    normal["catalogue_dirs"] = [expand(entry) for entry in normal.get("catalogue_dirs", [])]
    knowledge = normal.get("knowledge", {})
    for key in ("read_first", "per_repo", "architecture"):
        if key in knowledge:
            knowledge[key] = [expand(entry) for entry in knowledge[key]]
    for entries in normal.get("step_skills", {}).values():
        for entry in entries:
            entry["path"] = expand(entry["path"])
    return normal


def checked(project, folder_name=None, config=None):
    problems = validate(project, folder_name, config)
    if problems:
        label = project.get("name") if isinstance(project, dict) else None
        raise FactoryError(f"project {label or folder_name}: " + "; ".join(problems))
    return normalise(project)


def raw(name):
    if not (isinstance(name, str) and NAME_RE.match(name)):
        raise FactoryError(f"bad project name {name!r}")
    path = config_path(name)
    if not os.path.isfile(path):
        raise FactoryError(f"no project {name}")
    return read_json(path)


def load(name, config=None):
    return checked(raw(name), name, config)


def registered(conn, name):
    return db.one(conn.execute("SELECT * FROM projects WHERE name = ?", (name,)))


def require(conn, name):
    found = registered(conn, name)
    if not found or not os.path.isfile(config_path(name)):
        raise FactoryError(f"no project {name}")
    return found


def key_owner(conn, key):
    found = conn.execute("SELECT name FROM projects WHERE key = ?", (key,)).fetchone()
    return found[0] if found else None


def tracker_warnings(project, config=None):
    entry = project.get("integrations", {}).get("tracker")
    if not entry or entry.get("adapter") == "local":
        return []
    config = config or home.load_config()
    folder = find_adapter(config, normalise(project), entry["adapter"])
    if not folder:
        return []
    data, _ = adapters.load(folder)
    pattern = adapters.resolved_patterns(data, entry).get("key_pattern")
    if pattern and re.fullmatch(pattern, f"{project['key']}-1"):
        return [
            f"the project key {project['key']} looks like a key of the {entry['adapter']} tracker; "
            "choose another local key so local and external issues never clash"
        ]
    return []


def save(project):
    write_json(config_path(project["name"]), project)


def add(conn, name, key, description=None):
    if not NAME_RE.match(name or ""):
        raise FactoryError("project name must match ^[a-z][a-z0-9-]{1,39}$")
    if not KEY_RE.match(key or ""):
        raise FactoryError("project key must match ^[A-Z][A-Z0-9]{1,9}$")
    project = {"name": name, "key": key}
    if description:
        project["description"] = description
    project["repos"] = []
    checked(project, name)
    with db.tx(conn):
        if registered(conn, name) or os.path.exists(config_path(name)):
            raise FactoryError(f"project {name} exists")
        owner = key_owner(conn, key)
        if owner:
            raise FactoryError(f"key {key} is used by project {owner}")
        save(project)
        conn.execute(
            "INSERT INTO projects (name, key, config_path, created_at) VALUES (?, ?, ?, ?)",
            (name, key, config_path(name), now()),
        )
        db.event(conn, "project.add", project=name, key=key)
    return {"name": name, "key": key, "config_path": config_path(name), "warnings": []}


def link_repo(conn, name, path, as_name=None, role=None, default_branch=None, cwd=None):
    require(conn, name)
    project = raw(name)
    absolute = expand(path, base=cwd or os.getcwd())
    top = git_toplevel(absolute)
    if not top:
        raise FactoryError(f"{absolute} is not a git checkout")
    repo_name = as_name or os.path.basename(top)
    if not REPO_RE.match(repo_name):
        raise FactoryError(f"repo name {repo_name!r} must match ^[A-Za-z0-9][A-Za-z0-9._-]*$; pass --as <name>")
    for repo in project.get("repos", []):
        if repo["name"] == repo_name:
            raise FactoryError(f"project {name} already has a repo named {repo_name}")
        if same_path(expand(repo["path"]), top):
            raise FactoryError(f"{top} is already linked as {repo['name']}")
    entry = {"name": repo_name, "path": top}
    remote = git_origin(top)
    if remote:
        entry["remote"] = remote
    branch = default_branch or git_default_branch(top)
    if branch:
        entry["default_branch"] = branch
    if role:
        entry["role"] = role
    project.setdefault("repos", []).append(entry)
    checked(project, name)
    with db.tx(conn):
        save(project)
        db.event(conn, "project.link_repo", project=name, repo=repo_name, path=top)
    return dict(entry, project=name)


def unlink_repo(conn, name, repo_name):
    require(conn, name)
    project = raw(name)
    kept = [repo for repo in project.get("repos", []) if repo.get("name") != repo_name]
    if len(kept) == len(project.get("repos", [])):
        raise FactoryError(f"project {name} has no repo {repo_name}")
    project["repos"] = kept
    checked(project, name)
    with db.tx(conn):
        save(project)
        db.event(conn, "project.unlink_repo", project=name, repo=repo_name)
    return {"project": name, "unlinked": repo_name}


def set_key(conn, name, dotted, value):
    require(conn, name)
    project = raw(name)
    top = dotted.split(".")[0]
    if top not in TOP_KEYS:
        raise FactoryError(f"unknown key {dotted!r}")
    if top == "name":
        raise FactoryError("a project cannot be renamed")
    updated = copy.deepcopy(project)
    if top == "repos" and dotted.count(".") >= 2:
        _, repo_name, rest = dotted.split(".", 2)
        entry = next((repo for repo in updated.get("repos", []) if repo.get("name") == repo_name), None)
        if entry is None:
            raise FactoryError(f"project {name} has no repo {repo_name}")
        if rest.split(".")[0] in ("name", "path"):
            raise FactoryError(f"repos.{repo_name}.{rest}: use link-repo and unlink-repo for a repo's name and path")
        dotted_set(entry, rest, value)
    else:
        dotted_set(updated, dotted, value)
    checked(updated, name)
    with db.tx(conn):
        if updated["key"] != project["key"]:
            owner = key_owner(conn, updated["key"])
            if owner and owner != name:
                raise FactoryError(f"key {updated['key']} is used by project {owner}")
            conn.execute("UPDATE projects SET key = ? WHERE name = ?", (updated["key"], name))
        save(updated)
        db.event(conn, "project.set", project=name, key=dotted)
    result = resolved(load(name))
    result["warnings"] = result["warnings"] + tracker_warnings(updated)
    return result


def archive(conn, name):
    require(conn, name)
    with db.tx(conn):
        conn.execute("UPDATE projects SET archived_at = ? WHERE name = ?", (now(), name))
        db.event(conn, "project.archive", project=name)
    return {"name": name, "archived": True}


def listing(conn):
    found = []
    for row in db.rows(conn.execute("SELECT * FROM projects ORDER BY name")):
        try:
            project = raw(row["name"])
            repos = [repo.get("name") for repo in project.get("repos", [])]
            description = project.get("description")
        except FactoryError:
            repos, description = [], None
        found.append({
            "name": row["name"],
            "key": row["key"],
            "description": description,
            "repos": repos,
            "archived": row["archived_at"] is not None,
            "config_path": row["config_path"],
        })
    return found


def pin_file(top):
    return os.path.join(top, ".factory", "project")


def pin(conn, name, repo=None, cwd=None):
    require(conn, name)
    start = expand(repo, base=cwd or os.getcwd()) if repo else (cwd or os.getcwd())
    top = git_toplevel(start)
    if not top:
        raise FactoryError(f"{start} is not inside a git checkout")
    write_text(pin_file(top), name + "\n")
    linked = any(same_path(expand(entry["path"]), top) for entry in raw(name).get("repos", []))
    with db.tx(conn):
        db.event(conn, "project.pin", project=name, path=top)
    warnings = [] if linked else [f"{top} is not a repo of project {name}; link it with project link-repo"]
    return {"project": name, "file": pin_file(top), "warnings": warnings}


def resolution(conn, flag=None, cwd=None):
    cwd = cwd or os.getcwd()
    config = home.load_config()

    def found(name, step, why):
        require(conn, name)
        return {"project": name, "step": step, "how": STEP_NAMES[step], "why": why, "toplevel": top}

    top = git_toplevel(cwd)
    if flag:
        return found(flag, 1, f"--project {flag}")
    if os.environ.get("FACTORY_PROJECT"):
        name = os.environ["FACTORY_PROJECT"]
        return found(name, 2, f"FACTORY_PROJECT={name}")
    if top:
        pinned = pin_file(top)
        if os.path.isfile(pinned):
            with open(pinned, encoding="utf-8") as handle:
                name = handle.readline().strip()
            return found(name, 3, f"{pinned} names {name}")
        origin = normalise_remote(git_origin(top))
        matches = []
        for row in db.rows(conn.execute("SELECT name FROM projects WHERE archived_at IS NULL ORDER BY name")):
            try:
                project = raw(row["name"])
            except FactoryError:
                continue
            for repo in project.get("repos", []) if isinstance(project.get("repos"), list) else []:
                try:
                    path_match = same_path(expand(repo.get("path", "")), top)
                except FactoryError:
                    path_match = False
                remote_match = bool(origin and normalise_remote(repo.get("remote")) == origin)
                if path_match or remote_match:
                    matches.append((row["name"], repo.get("name"), "path" if path_match else "remote"))
                    break
        if len(matches) > 1:
            names = ", ".join(match[0] for match in matches)
            raise FactoryError(f"{top} belongs to several projects ({names}); pass --project")
        if matches:
            name, repo_name, how = matches[0]
            return found(name, 4, f"{top} is repo {repo_name} of {name} (by {how})")
    if config.get("default_project"):
        name = config["default_project"]
        return found(name, 5, f"config.json default_project is {name}")
    why = f"no project matches {top}; run new registers an ad-hoc project" if top else "not in a git checkout"
    return {"project": None, "step": 6, "how": STEP_NAMES[6], "why": why, "toplevel": top}


def adhoc_names(conn, top):
    base = re.sub(r"[^a-z0-9-]+", "-", os.path.basename(top).lower()).strip("-")
    if not base or not base[0].isalpha():
        base = f"repo-{base}".strip("-")
    base = base[:36].rstrip("-")
    if len(base) < 2:
        base = f"{base}-repo"
    name, number = base, 2
    while registered(conn, name) or os.path.exists(config_path(name)):
        name = f"{base}-{number}"
        number += 1
    letters = re.sub(r"[^A-Z0-9]", "", base.upper())
    letters = re.sub(r"^[0-9]+", "", letters) or "REPO"
    stem = (letters + "X")[:10] if len(letters) < 2 else letters[:10]
    key, number = stem, 2
    while key_owner(conn, key):
        suffix = str(number)
        key = stem[:10 - len(suffix)] + suffix
        number += 1
    return name, key


def adhoc_config(conn, top):
    name, key = adhoc_names(conn, top)
    repo_name = os.path.basename(top)
    if not REPO_RE.match(repo_name):
        repo_name = name
    repo = {"name": repo_name, "path": top}
    remote = git_origin(top)
    if remote:
        repo["remote"] = remote
    branch = git_default_branch(top)
    if branch:
        repo["default_branch"] = branch
    return {"name": name, "key": key, "description": f"Ad-hoc project for {top}", "repos": [repo]}


def register_adhoc(conn, top):
    project = adhoc_config(conn, top)
    checked(project, project["name"])
    with db.tx(conn):
        save(project)
        conn.execute(
            "INSERT INTO projects (name, key, config_path, created_at) VALUES (?, ?, ?, ?)",
            (project["name"], project["key"], config_path(project["name"]), now()),
        )
        db.event(conn, "project.adhoc", project=project["name"], path=top)
    return project["name"]


def existing_paths(paths, skipped, label):
    kept = []
    for path in paths:
        if os.path.exists(path):
            kept.append(path)
        else:
            skipped.append({"what": label, "path": path, "reason": "does not exist"})
    return kept


def resolved(project, config=None, adhoc=False):
    config = config or home.load_config()
    skipped = []
    warnings = []
    repos = []
    for repo in project.get("repos", []):
        path = repo["path"]
        top = git_toplevel(path)
        has_origin = bool(top and git_origin(path))
        entry = {
            "name": repo["name"],
            "path": path,
            "remote": repo.get("remote") or (git_origin(path) if top else None),
            "default_branch": repo.get("default_branch") or (git_default_branch(path) if top else None),
            "role": repo.get("role"),
            "test": repo.get("test"),
            "setup": repo.get("setup"),
            "is_git": bool(top),
            "has_origin": has_origin,
        }
        if repo.get("after_merge"):
            entry["after_merge"] = repo["after_merge"]
        if not top:
            warnings.append(f"repo {repo['name']}: {path} is not a git checkout")
        repos.append(entry)
    integrations = {}
    configured = project.get("integrations", {})
    for slot in adapters.SLOTS:
        entry = configured.get(slot) or ({"adapter": adapters.DEFAULT_SLOTS[slot]} if slot in adapters.DEFAULT_SLOTS else None)
        if not entry:
            continue
        folder = find_adapter(config, project, entry["adapter"])
        if not folder:
            raise FactoryError(f"project {project['name']}: no adapter {entry['adapter']!r} in the catalogue")
        data, _ = adapters.load(folder)
        capabilities = {
            name: adapters.capability_status(folder, spec, repos)
            for name, spec in data.get("capabilities", {}).items()
        }
        resolved_entry = {
            "adapter": entry["adapter"],
            "slot": slot,
            "dir": folder,
            "adapter_json": os.path.join(folder, "adapter.json"),
            "adapter_md": os.path.join(folder, "adapter.md"),
            "settings": adapters.resolved_settings(data, entry),
            "patterns": adapters.resolved_patterns(data, entry),
            "capabilities": capabilities,
        }
        if slot == "tracker":
            resolved_entry["write"] = bool(entry.get("write", False))
        integrations[slot] = resolved_entry
    knowledge_in = project.get("knowledge", {})
    knowledge = {}
    if knowledge_in:
        knowledge["label"] = knowledge_in.get("label", "knowledge")
        knowledge["read_first"] = existing_paths(knowledge_in.get("read_first", []), skipped, "knowledge.read_first")
        per_repo = {}
        for repo in repos:
            paths = [pattern.replace("{repo}", repo["name"]) for pattern in knowledge_in.get("per_repo", [])]
            per_repo[repo["name"]] = existing_paths(paths, skipped, "knowledge.per_repo")
        knowledge["per_repo"] = per_repo
        knowledge["architecture"] = existing_paths(knowledge_in.get("architecture", []), skipped, "knowledge.architecture")
    step_skills = {}
    for skill_key, entries in project.get("step_skills", {}).items():
        kept = []
        for entry in entries:
            if os.path.exists(entry["path"]):
                kept.append({"path": entry["path"], "when": entry.get("when", "")})
            else:
                skipped.append({"what": f"step_skills.{skill_key}", "path": entry["path"], "reason": "does not exist"})
        step_skills[skill_key] = kept
    targets = {
        "factory": {"label": "factory"},
        "repo": {"base": "default", "label": "repo"},
        "process": {"label": "issues and process"},
    }
    if knowledge_in:
        targets["knowledge"] = {"base": "default", "label": knowledge.get("label", "knowledge")}
    for name, spec in project.get("improvement_targets", {}).items():
        targets[name] = dict(spec)
    defaults = {"parallel": config["parallel"], "max_rounds": config["max_rounds"], **DEFAULTS}
    defaults.update(project.get("defaults", {}))
    result = {
        "name": project["name"],
        "key": project["key"],
        "description": project.get("description"),
        "config_path": config_path(project["name"]),
        "adhoc": adhoc,
        "repos": repos,
        "repo_root": project.get("repo_root"),
        "integrations": integrations,
        "knowledge": knowledge,
        "step_skills": step_skills,
        "catalogue_dirs": project.get("catalogue_dirs", []),
        "improvement_targets": targets,
        "defaults": defaults,
        "skipped": skipped,
        "warnings": warnings,
    }
    return result


def show(conn, name=None, cwd=None):
    if name:
        require(conn, name)
        return resolved(load(name))
    found = resolution(conn, None, cwd)
    if found["project"]:
        return resolved(load(found["project"]))
    if not found["toplevel"]:
        raise FactoryError("no project: not in a git checkout and no project given")
    project = adhoc_config(conn, found["toplevel"])
    return resolved(checked(project, project["name"]), adhoc=True)
