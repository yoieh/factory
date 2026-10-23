import datetime
import hashlib
import json
import os
import re
import subprocess
import tempfile
import unicodedata

from . import FactoryError


def now():
    moment = datetime.datetime.now(datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def expand(path, base=None):
    if not isinstance(path, str) or not path.strip():
        raise FactoryError(f"not a path: {path!r}")
    expanded = os.path.expanduser(path.strip())
    if not os.path.isabs(expanded):
        if base is None:
            raise FactoryError(f"path must be absolute after ~ expansion: {path}")
        expanded = os.path.join(base, expanded)
    return os.path.normpath(expanded)


def same_path(left, right):
    if not left or not right:
        return False
    return os.path.realpath(left) == os.path.realpath(right)


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(text)
        os.replace(temp, path)
    except BaseException:
        if os.path.exists(temp):
            os.unlink(temp)
        raise


def read_json(path):
    try:
        text = read_text(path)
    except FileNotFoundError:
        raise FactoryError(f"missing file: {path}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise FactoryError(f"{path}: invalid JSON: {error}")


def write_json(path, value):
    write_text(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def append_line(path, line):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")


def text_hash(*parts):
    digest = hashlib.sha256()
    for part in parts:
        digest.update((part or "").encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def slugify(text, limit=40):
    ascii_text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_text.lower())
    slug = ""
    for word in words:
        candidate = f"{slug}-{word}" if slug else word
        if len(candidate) > limit:
            if not slug:
                slug = word[:limit]
            break
        slug = candidate
    return slug


def git(args, cwd, check=True):
    try:
        done = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)
    except FileNotFoundError:
        raise FactoryError("git is not installed")
    if done.returncode != 0:
        if check:
            message = (done.stderr or done.stdout).strip().splitlines()
            raise FactoryError(f"git {' '.join(args)} in {cwd}: {message[-1] if message else 'failed'}")
        return None
    return done.stdout.strip()


def git_toplevel(path):
    if not path or not os.path.isdir(path):
        return None
    return git(["rev-parse", "--show-toplevel"], path, check=False) or None


def git_origin(path):
    return git(["remote", "get-url", "origin"], path, check=False) or None


def git_commit(path, ref):
    return git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], path, check=False) or None


def git_branch_exists(path, branch):
    return git_commit(path, f"refs/heads/{branch}") is not None


def git_default_branch(path):
    head = git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], path, check=False)
    if head and head.startswith("refs/remotes/origin/"):
        return head[len("refs/remotes/origin/"):]
    for name in ("main", "master"):
        if git_commit(path, f"refs/remotes/origin/{name}"):
            return name
    configured = git(["config", "--get", "init.defaultBranch"], path, check=False)
    for name in [configured, "main", "master"]:
        if name and git_branch_exists(path, name):
            return name
    return git(["symbolic-ref", "--quiet", "--short", "HEAD"], path, check=False) or None


def valid_branch_name(name):
    if not name or name.startswith("-"):
        return False
    try:
        done = subprocess.run(["git", "check-ref-format", "--branch", name], capture_output=True, text=True)
    except FileNotFoundError:
        raise FactoryError("git is not installed")
    return done.returncode == 0


def normalise_remote(url):
    if not url:
        return None
    text = url.strip()
    scp = re.match(r"^(?:[^@/]+@)?([^:/]+):(?!//)(.+)$", text)
    if scp and "://" not in text:
        host, path = scp.group(1), scp.group(2)
    else:
        parsed = re.match(r"^[a-z][a-z0-9+.-]*://(?:[^@/]*@)?([^/:]+)(?::[0-9]+)?/(.+)$", text, re.I)
        if not parsed:
            return text.rstrip("/")
        host, path = parsed.group(1), parsed.group(2)
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return f"{host.lower()}/{path}"


def dotted_set(target, dotted, value):
    keys = dotted.split(".")
    if not all(keys):
        raise FactoryError(f"bad key: {dotted}")
    node = target
    for key in keys[:-1]:
        if not isinstance(node.get(key), dict):
            node[key] = {}
        node = node[key]
    node[keys[-1]] = value


def parse_json_value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise FactoryError(f"value must be JSON (quote strings, for example '\"text\"'): {text}")
