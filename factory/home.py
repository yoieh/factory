import copy
import os
import re
import shutil

from . import VERSION, FactoryError, db
from .util import dotted_set, expand, read_json, write_json

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,39}$")

DEFAULT_CONFIG = {
    "default_project": None,
    "dashboard": {"port": 8765},
    "factory_source": None,
    "factory_project": None,
    "catalogue_dirs": [],
    "parallel": 3,
    "max_rounds": 4,
}

FOLDERS = [
    "projects",
    "issues",
    "runs",
    "improve",
    "worktrees",
    "core",
    "catalogue/steps",
    "catalogue/pipelines",
    "catalogue/adapters",
    "catalogue/agents",
]


def root():
    return expand(os.environ.get("FACTORY_HOME") or "~/.factory")


def path(*parts):
    return os.path.join(root(), *parts)


def core_root():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_path():
    return path("config.json")


def db_path():
    return path("factory.db")


def ensure():
    created = not os.path.isdir(root())
    if created:
        os.makedirs(root(), mode=0o700)
    for folder in FOLDERS:
        os.makedirs(path(folder), exist_ok=True)
    if not os.path.exists(config_path()):
        write_json(config_path(), DEFAULT_CONFIG)
        created = True
    if not os.path.exists(db_path()):
        created = True
    return created


def connect():
    return db.connect(db_path())


def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def validate_config(config):
    problems = []
    if not isinstance(config, dict):
        return ["config.json: must be a JSON object"]
    for key in config:
        if key not in DEFAULT_CONFIG:
            problems.append(f"config.json: unknown key {key!r}")
    for key in ("default_project", "factory_project"):
        value = config.get(key)
        if value is not None and not (isinstance(value, str) and NAME_RE.match(value)):
            problems.append(f"config.json: {key} must be null or a project name")
    dashboard = config.get("dashboard", {})
    if not isinstance(dashboard, dict):
        problems.append("config.json: dashboard must be an object")
    else:
        for key in dashboard:
            if key != "port":
                problems.append(f"config.json: unknown key 'dashboard.{key}'")
        port = dashboard.get("port", 8765)
        if not (is_int(port) and 1 <= port <= 65535):
            problems.append("config.json: dashboard.port must be a port number")
    source = config.get("factory_source")
    if source is not None:
        try:
            expand(source)
        except FactoryError as error:
            problems.append(f"config.json: factory_source: {error.message}")
    dirs = config.get("catalogue_dirs", [])
    if not isinstance(dirs, list):
        problems.append("config.json: catalogue_dirs must be a list of paths")
    else:
        for entry in dirs:
            try:
                expand(entry)
            except FactoryError as error:
                problems.append(f"config.json: catalogue_dirs: {error.message}")
    parallel = config.get("parallel", 3)
    if not (is_int(parallel) and parallel >= 1):
        problems.append("config.json: parallel must be an integer of 1 or more")
    rounds = config.get("max_rounds", 4)
    if not (is_int(rounds) and 1 <= rounds <= 10):
        problems.append("config.json: max_rounds must be an integer from 1 to 10")
    return problems


def normalise_config(config):
    merged = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in config.items():
        merged[key] = value
    merged["dashboard"] = {"port": 8765, **(config.get("dashboard") or {})}
    if merged["factory_source"]:
        merged["factory_source"] = expand(merged["factory_source"])
    merged["catalogue_dirs"] = [expand(entry) for entry in merged["catalogue_dirs"]]
    return merged


def load_config():
    config = read_json(config_path())
    problems = validate_config(config)
    if problems:
        raise FactoryError("; ".join(problems))
    return normalise_config(config)


def set_config(conn, dotted, value):
    raw = read_json(config_path())
    if dotted.split(".")[0] not in DEFAULT_CONFIG:
        raise FactoryError(f"config.json: unknown key {dotted!r}")
    updated = copy.deepcopy(raw)
    dotted_set(updated, dotted, value)
    problems = validate_config(updated)
    if problems:
        raise FactoryError("; ".join(problems))
    if updated.get("factory_source"):
        updated["factory_source"] = expand(updated["factory_source"])
    if updated.get("catalogue_dirs"):
        updated["catalogue_dirs"] = [expand(entry) for entry in updated["catalogue_dirs"]]
    with db.tx(conn):
        write_json(config_path(), updated)
        db.event(conn, "config.set", key=dotted)
    return normalise_config(updated)


def init(conn):
    return {
        "home": root(),
        "config": config_path(),
        "db": db_path(),
        "version": VERSION,
        "schema_version": db.stored_version(conn),
    }


CORE_PARTS = ("bin", "factory", "catalogue", "skills", "agents", "examples", "tests", ".claude-plugin", "install.sh", "README.md")


def plugin_manifest(folder=None):
    return os.path.join(folder or core_root(), ".claude-plugin", "plugin.json")


def is_plugin_folder(folder=None):
    return os.path.isfile(plugin_manifest(folder))


def plugin_name(folder=None):
    try:
        return read_json(plugin_manifest(folder)).get("name") or "factory"
    except FactoryError:
        return "factory"


PLUGIN_SOURCE = ".plugin-source"


def under_cores(folder):
    return os.path.realpath(os.path.dirname(folder)) == os.path.realpath(path("core"))


def plugin_root():
    root = core_root()
    if not under_cores(root):
        return root if is_plugin_folder(root) else None
    marker = os.path.join(root, PLUGIN_SOURCE)
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8") as handle:
            source = handle.read().strip()
        if source and is_plugin_folder(source):
            return source
    return None


def copy_core(source, target):
    staging = f"{target}.staging-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    ignore = shutil.ignore_patterns("__pycache__", "*.pyc")
    for part in CORE_PARTS:
        origin = os.path.join(source, part)
        if os.path.isdir(origin):
            shutil.copytree(origin, os.path.join(staging, part), symlinks=True, ignore=ignore)
        elif os.path.isfile(origin):
            shutil.copy2(origin, os.path.join(staging, part))
    try:
        os.rename(staging, target)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        if not os.path.isdir(target):
            raise


def point_current(version):
    current = path("core", "current")
    if os.path.islink(current) and os.readlink(current) == version:
        return False
    temporary = path("core", f".current-{os.getpid()}")
    if os.path.lexists(temporary):
        os.unlink(temporary)
    os.symlink(version, temporary)
    os.replace(temporary, current)
    return True


def sync_core():
    source = core_root()
    if not is_plugin_folder(source):
        return None
    cores = path("core")
    if under_cores(source):
        return None
    target = os.path.join(cores, VERSION)
    copied = False
    if not os.path.isdir(target):
        os.makedirs(cores, exist_ok=True)
        copy_core(source, target)
        copied = True
    marker = os.path.join(target, PLUGIN_SOURCE)
    recorded = None
    if os.path.isfile(marker):
        with open(marker, encoding="utf-8") as handle:
            recorded = handle.read().strip()
    if recorded != source:
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write(source + "\n")
    moved = point_current(VERSION)
    if copied or moved:
        return {"version": VERSION, "copied": copied, "current": moved, "source": source, "target": target}
    return None
