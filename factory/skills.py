import json
import os

from . import FactoryError, catalogue, db, home, projects
from .util import expand, git_toplevel, now, read_text, write_json, write_text

MANIFEST = ".factory-install.json"
ROLES = {
    "planner": "factory-planner",
    "worker": "factory-worker",
    "reviewer": "factory-reviewer",
    "retro": "factory-retro",
    "improver": "factory-improver",
}


def core_version():
    from . import VERSION

    return VERSION


def user_base():
    return os.path.join(os.path.expanduser("~"), ".claude")


def skill_source():
    return os.path.join(home.core_root(), "skills", "factory", "SKILL.md")


def agent_source(name):
    return os.path.join(home.core_root(), "agents", f"{name}.md")


def dedicated_sources(project=None):
    config = home.load_config()
    built = catalogue.build(config, project)
    found, missing = {}, []
    for name in catalogue.dedicated_agents(built["steps"]):
        if name in built["agents"]:
            found[name] = built["agents"][name]
        else:
            missing.append(name)
    return found, missing


def planned_files(project=None):
    files = {os.path.join("skills", "factory", "SKILL.md"): skill_source()}
    for name in ROLES.values():
        files[os.path.join("agents", f"{name}.md")] = agent_source(name)
    dedicated, missing = dedicated_sources(project)
    for name, source in dedicated.items():
        files[os.path.join("agents", f"{name}.md")] = source
    for relative, source in files.items():
        if not os.path.isfile(source):
            raise FactoryError(f"the core has no {source}; reinstall the core")
    return files, sorted(dedicated), missing


def manifest_path(base, scope):
    if scope == "plugin":
        return os.path.join(base, MANIFEST)
    return os.path.join(base, "skills", "factory", MANIFEST)


def read_manifest(base, scope):
    path = manifest_path(base, scope)
    if not os.path.isfile(path):
        return None
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def write_files(base, files):
    for relative, source in files.items():
        write_text(os.path.join(base, relative), read_text(source))


def record(conn, scope, base, project, files, extra=None):
    stamp = now()
    write_json(manifest_path(base, scope), {
        "scope": scope,
        "version": core_version(),
        "installed_at": stamp,
        "files": {relative: source for relative, source in sorted(files.items())},
    })
    with db.tx(conn):
        conn.execute(
            "INSERT INTO installs (scope, path, project, version, installed_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (path) DO UPDATE SET scope = excluded.scope, project = excluded.project, "
            "version = excluded.version, installed_at = excluded.installed_at",
            (scope, base, project, core_version(), stamp),
        )
        db.event(conn, "skill.install", scope=scope, path=base, project=project, version=core_version(), **(extra or {}))


def repo_base(path, cwd=None):
    absolute = expand(path, base=cwd or os.getcwd())
    top = git_toplevel(absolute)
    if not top:
        raise FactoryError(f"{absolute} is not inside a git checkout")
    return top, os.path.join(top, ".claude")


def install(conn, user=False, repo=None, project_name=None):
    if bool(user) == bool(repo):
        raise FactoryError("give --user or --repo <path>", code=2)
    project = None
    if repo:
        top, base = repo_base(repo)
        if not project_name:
            found = projects.resolution(conn, None, top)
            project_name = found["project"]
        scope = "repo"
    else:
        top, base = None, user_base()
        scope = "user"
    if project_name:
        projects.require(conn, project_name)
        project = projects.load(project_name)
    files, dedicated, missing = planned_files(project)
    previous = read_manifest(base, scope)
    stale = [relative for relative in (previous or {}).get("files", {}) if relative not in files]
    for relative in stale:
        target = os.path.join(base, relative)
        if os.path.isfile(target):
            os.unlink(target)
    write_files(base, files)
    record(conn, scope, base, project_name if scope == "repo" else None, files)
    return {
        "scope": scope,
        "path": base,
        "repo": top,
        "project": project_name if scope == "repo" else None,
        "version": core_version(),
        "files": sorted(os.path.join(base, relative) for relative in files),
        "dedicated": dedicated,
        "missing": missing,
    }


def uninstall(conn, user=False, repo=None):
    if bool(user) == bool(repo):
        raise FactoryError("give --user or --repo <path>", code=2)
    if repo:
        _, base = repo_base(repo)
        scope = "repo"
    else:
        base, scope = user_base(), "user"
    data = read_manifest(base, scope)
    row = db.one(conn.execute("SELECT * FROM installs WHERE path = ?", (base,)))
    if not data and not row:
        raise FactoryError(f"no factory install at {base}")
    removed = []
    for relative in (data or {}).get("files", {}):
        target = os.path.join(base, relative)
        if os.path.isfile(target):
            os.unlink(target)
            removed.append(target)
    manifest = manifest_path(base, scope)
    if os.path.isfile(manifest):
        os.unlink(manifest)
    skill_dir = os.path.join(base, "skills", "factory")
    if os.path.isdir(skill_dir) and not os.listdir(skill_dir):
        os.rmdir(skill_dir)
    with db.tx(conn):
        conn.execute("DELETE FROM installs WHERE path = ?", (base,))
        db.event(conn, "skill.uninstall", scope=scope, path=base)
    return {"scope": scope, "path": base, "removed": removed}


def drift(row):
    data = read_manifest(row["path"], row["scope"])
    changed, missing, gone = [], [], []
    if data:
        for relative, source in data.get("files", {}).items():
            target = os.path.join(row["path"], relative)
            if not os.path.isfile(target):
                missing.append(relative)
            elif not os.path.isfile(source):
                gone.append(relative)
            elif read_text(target) != read_text(source):
                changed.append(relative)
    version = core_version()
    current = row["version"] == version and not changed and not missing and data is not None
    return {
        "scope": row["scope"],
        "path": row["path"],
        "project": row["project"],
        "version": row["version"],
        "core_version": version,
        "installed_at": row["installed_at"],
        "current": current,
        "changed": changed,
        "missing": missing if data else ["the install manifest"],
        "source_gone": gone,
    }


def status(conn):
    rows = db.rows(conn.execute("SELECT * FROM installs ORDER BY scope, path"))
    return {
        "core_version": core_version(),
        "plugin": home.plugin_root(),
        "installs": [drift(row) for row in rows],
    }


def plugin(conn, folder):
    target = os.path.abspath(os.path.expanduser(folder))
    if os.path.isdir(target) and os.listdir(target) and read_manifest(target, "plugin") is None:
        raise FactoryError(f"{target} is not empty and holds no factory plugin; pick an empty or new folder")
    files, dedicated, missing = planned_files(None)
    previous = read_manifest(target, "plugin")
    for relative in (previous or {}).get("files", {}):
        if relative not in files and os.path.isfile(os.path.join(target, relative)):
            os.unlink(os.path.join(target, relative))
    write_files(target, files)
    write_json(os.path.join(target, ".claude-plugin", "plugin.json"), {
        "name": "factory",
        "version": core_version(),
        "description": "The /factory orchestrator skill and its agents. The factory CLI comes from the core install.",
        "author": {"name": "factory"},
    })
    record(conn, "plugin", target, None, files)
    return {"scope": "plugin", "path": target, "version": core_version(),
            "files": sorted(os.path.join(target, relative) for relative in files), "dedicated": dedicated,
            "missing": missing}


def plugin_agent_dirs(conn):
    found = []
    plugin = home.plugin_root()
    if plugin:
        found.append((home.plugin_name(plugin), os.path.join(plugin, "agents")))
    for row in db.rows(conn.execute("SELECT path FROM installs WHERE scope = 'plugin' ORDER BY installed_at DESC")):
        found.append((home.plugin_name(row["path"]), os.path.join(row["path"], "agents")))
    return found


def agents(conn, cwd=None):
    cwd = cwd or os.getcwd()
    top = git_toplevel(cwd)
    places = []
    if top:
        places.append(("repo", None, os.path.join(top, ".claude", "agents")))
    places.append(("user", None, os.path.join(user_base(), "agents")))
    for name, folder in plugin_agent_dirs(conn):
        places.append(("plugin", name, folder))

    def lookup(agent):
        for scope, plugin_name, folder in places:
            if os.path.isfile(os.path.join(folder, f"{agent}.md")):
                return scope, f"{plugin_name}:{agent}" if plugin_name else agent
        return None, None

    roles, missing = {}, []
    scope = None
    for role, agent in ROLES.items():
        found_scope, subagent = lookup(agent)
        roles[role] = subagent
        if subagent is None:
            missing.append(agent)
        elif role == "planner":
            scope = found_scope
    project = None
    found = projects.resolution(conn, None, cwd)
    if found["project"]:
        project = projects.load(found["project"])
    config = home.load_config()
    steps = catalogue.build(config, project)["steps"]
    dedicated = {}
    for agent in catalogue.dedicated_agents(steps):
        _, subagent = lookup(agent)
        if subagent:
            dedicated[agent] = subagent
        else:
            missing.append(agent)
    return {"scope": scope, "roles": roles, "dedicated": dedicated, "missing": missing}


def project_installs(conn, project_name, repos):
    rows = db.rows(conn.execute("SELECT * FROM installs ORDER BY path"))
    paths = {os.path.realpath(os.path.join(repo["path"], ".claude")): repo["name"] for repo in repos}
    repo_installs = []
    user = None
    for row in rows:
        if row["scope"] == "user":
            user = drift(row)
        elif row["scope"] == "repo" and (row["project"] == project_name or os.path.realpath(row["path"]) in paths):
            entry = drift(row)
            entry["repo"] = paths.get(os.path.realpath(row["path"]))
            repo_installs.append(entry)
    return {"user": user, "repos": repo_installs, "plugin": home.plugin_root()}
