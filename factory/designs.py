import json
import os
import re

from . import FactoryError, adapters, db, home, projects, runs
from .util import read_text, slugify, write_json, write_text

PNG = b"\x89PNG\r\n\x1a\n"
INDEX = ".targets.json"


def design_dir(run_id):
    return os.path.join(runs.run_dir(run_id), "design")


def run_of_path(path):
    runs_folder = os.path.realpath(home.path("runs"))
    real = os.path.realpath(path)
    if not real.startswith(runs_folder + os.sep):
        return None
    return os.path.relpath(real, runs_folder).split(os.sep)[0]


def project_for(conn, path, project_flag=None):
    run_id = run_of_path(path)
    if run_id and conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone() and not project_flag:
        return runs.snapshot(run_id)
    if project_flag:
        projects.require(conn, project_flag)
        return projects.resolved(projects.load(project_flag))
    return projects.show(conn)


def design_adapter(project):
    return project["integrations"].get("design")


def links(conn, path, project_flag=None):
    absolute = os.path.abspath(path)
    try:
        text = read_text(absolute)
    except FileNotFoundError:
        raise FactoryError(f"no file {absolute}")
    project = project_for(conn, absolute, project_flag)
    adapter = design_adapter(project)
    if not adapter:
        return {"path": absolute, "adapter": None, "links": [], "reason": f"project {project['name']} has no design adapter"}
    found = adapters.finds(adapter["patterns"], "design_url", text)
    return {"path": absolute, "adapter": adapter["adapter"], "links": found}


def load_index(folder):
    path = os.path.join(folder, INDEX)
    if not os.path.isfile(path):
        return {}
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def target(conn, run_id, name):
    runs.get(conn, run_id)
    name = " ".join((name or "").split())
    if not name:
        raise FactoryError("--name is empty", code=2)
    folder = design_dir(run_id)
    os.makedirs(folder, exist_ok=True)
    index = load_index(folder)
    base = index.get(name)
    if not base:
        stem = slugify(name, 60) or "frame"
        taken = set(index.values())
        base, number = stem, 2
        while base in taken or base in ("notes", "readme") or any(
                os.path.exists(os.path.join(folder, base + suffix)) for suffix in (".png", ".md")):
            base = f"{stem}-{number}"
            number += 1
        index[name] = base
        write_json(os.path.join(folder, INDEX), index)
        with db.tx(conn):
            db.event(conn, "design.target", run_id, name=name, file=base)
    return {"name": name, "png": os.path.join(folder, base + ".png"), "md": os.path.join(folder, base + ".md")}


def is_png(path):
    with open(path, "rb") as handle:
        return handle.read(len(PNG)) == PNG


def check(conn, run_id):
    runs.get(conn, run_id)
    folder = design_dir(run_id)
    project = runs.snapshot(run_id)
    adapter = design_adapter(project)
    patterns = (adapter or {}).get("patterns", {}).get("scrub", []) if adapter else []
    removed, scrubbed = [], []
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    for entry in names:
        path = os.path.join(folder, entry)
        if not os.path.isfile(path):
            continue
        if entry.lower().endswith(".png") and not is_png(path):
            os.unlink(path)
            removed.append(path)
        elif entry.endswith(".md") and patterns:
            text = read_text(path)
            cleaned, count = text, 0
            for pattern in patterns:
                cleaned, found = re.subn(pattern, "", cleaned)
                count += found
            if count:
                write_text(path, cleaned)
                scrubbed.append({"path": path, "removed": count})
    index = load_index(folder)
    designs = []
    for name, base in index.items():
        png = os.path.join(folder, base + ".png")
        md = os.path.join(folder, base + ".md")
        designs.append({"name": name, "png": png if os.path.isfile(png) else None, "md": md if os.path.isfile(md) else None})
    with db.tx(conn):
        db.event(conn, "design.check", run_id, removed=len(removed), scrubbed=len(scrubbed))
    return {"designs": designs, "removed": removed, "scrubbed": scrubbed,
            "notes": os.path.join(folder, "notes.md") if os.path.isfile(os.path.join(folder, "notes.md")) else None}
