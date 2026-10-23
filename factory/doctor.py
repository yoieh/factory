import os
import shutil
import sqlite3
import stat
import subprocess
import sys

from . import NAME, VERSION, home, projects


def check(name, ok, detail, fix=None):
    entry = {"check": name, "ok": bool(ok), "detail": detail}
    if not ok and fix:
        entry["fix"] = fix
    return entry


def shim_version(path):
    try:
        done = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    words = done.stdout.split()
    return words[-1] if done.returncode == 0 and words else None


def run(conn, project_name=None):
    checks = []
    version = sys.version_info
    checks.append(check("python", version >= (3, 10), f"{version.major}.{version.minor}.{version.micro}",
                        "install Python 3.10 or newer"))
    git = shutil.which("git")
    git_version = None
    if git:
        done = subprocess.run([git, "--version"], capture_output=True, text=True)
        git_version = done.stdout.strip()
    checks.append(check("git", git is not None, git_version or "not found", "install git"))
    checks.append(check("sqlite", sqlite3.sqlite_version_info >= (3, 35), sqlite3.sqlite_version,
                        "use a Python built with SQLite 3.35 or newer"))
    root = home.root()
    mode = stat.S_IMODE(os.stat(root).st_mode)
    writable = os.access(root, os.W_OK)
    private = not mode & 0o022
    checks.append(check("home", writable and private, f"{root} ({oct(mode)})",
                        f"chmod 700 {root}" if writable else f"make {root} writable for this user"))
    install = os.path.join(home.core_root(), "install.sh")
    on_path = shutil.which(NAME)
    if on_path:
        found = shim_version(on_path)
        same = found == VERSION
        checks.append(check("path", same, f"{on_path} runs {found or 'an unknown version'}",
                            f"sh {install} (this core is {VERSION})"))
    else:
        checks.append(check("path", False, f"no {NAME} command on PATH", f"sh {install}"))
    from . import skills

    found = skills.status(conn)
    user = next((entry for entry in found["installs"] if entry["scope"] == "user"), None)
    if found["plugin"]:
        checks.append(check("skill", True, f"plugin at {found['plugin']}"))
    elif user:
        checks.append(check("skill", user["current"], f"user install {user['version']} at {user['path']}",
                            f"{NAME} skill install --user (the core is {VERSION})"))
    else:
        checks.append(check("skill", False, "no plugin and no user install of the skill",
                            f"install the plugin, or run {NAME} skill install --user"))
    for entry in found["installs"]:
        if entry["scope"] != "user" and not entry["current"]:
            checks.append(check("skill", False, f"{entry['scope']} install at {entry['path']} is not current",
                                f"{NAME} skill plugin {entry['path']}" if entry["scope"] == "plugin"
                                else f"{NAME} skill install --repo {os.path.dirname(entry['path'])}"))
    result = {"ok": True, "version": VERSION, "home": root, "core": home.core_root(), "checks": checks}
    if project_name:
        project = projects.resolved(projects.load(project_name))
        for slot, entry in project["integrations"].items():
            for capability, status in entry["capabilities"].items():
                state = status.get("status")
                checks.append(check(f"{slot}.{capability}", state in ("available", "check_in_session"),
                                    f"{entry['adapter']}: {state}" + (f" ({status['reason']})" if status.get("reason") else ""),
                                    f"see ## Setup in {entry['adapter_md']}"))
        for warning in project["warnings"]:
            checks.append(check("repo", False, warning, "fix the repo path with project link-repo"))
        dirs = [repo["path"] for repo in project["repos"]] + [root]
        result["start_command"] = "claude " + " ".join(f"--add-dir {path}" for path in dirs)
    result["ok"] = all(entry["ok"] for entry in checks)
    return result
