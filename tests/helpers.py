import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from factory import cli

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}
CLEARED = ("FACTORY_PROJECT", "FACTORY_BIN", "FACTORY_CORE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_PLUGIN_ROOT", "CLAUDE_CONFIG_DIR",
           "XDG_CONFIG_HOME")

PLAN_HEAD = """# Plan: {title}

A test plan.

## Repo facts
{facts}
## Jobs

| Job | Kind | Repo | Title | After | Pipeline | Why this pipeline |
|---|---|---|---|---|---|---|

```json
{plan}
```

## Outside the code

## Risks and open points
"""

QUESTION = """QUESTION: Which field should the endpoint return?

**Why I ask:** The issue names two fields.

**Options**
- **A (recommended):** Return the id. It matches the other endpoints.
- **B:** Return the name. It changes the client.

**Status:** The build waits for this answer.
"""


@contextlib.contextmanager
def working_dir(path):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def git(cwd, *args):
    done = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True, text=True)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {cwd}: {done.stderr}")
    return done.stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class FactoryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="factory-test-"))
        self.home = os.path.join(self.tmp, "home")
        self.user = os.path.join(self.tmp, "user")
        os.makedirs(self.user)
        values = dict(GIT_ENV, FACTORY_HOME=self.home, HOME=self.user)
        self.environment = mock.patch.dict(os.environ, values)
        self.environment.start()
        for name in CLEARED:
            os.environ.pop(name, None)
        self.previous_cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.previous_cwd)
        self.environment.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, *args, cwd=None):
        out, err = io.StringIO(), io.StringIO()
        with working_dir(cwd or os.getcwd()), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main([str(arg) for arg in args])
        return code, out.getvalue(), err.getvalue()

    def cli(self, *args, cwd=None):
        code, out, err = self.call(*args, cwd=cwd)
        if code != 0:
            raise AssertionError(f"factory {' '.join(str(a) for a in args)} exited {code}: {err.strip()}")
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    def fails(self, *args, code=1, cwd=None):
        found, out, err = self.call(*args, cwd=cwd)
        if found != code:
            raise AssertionError(f"factory {' '.join(str(a) for a in args)} exited {found}, expected {code}: {err} {out}")
        self.assertTrue(err.startswith("factory: "), err)
        return err[len("factory: "):].strip()

    def repo(self, name, origin=True, files=None):
        path = os.path.join(self.tmp, "repos", name)
        if origin:
            bare = os.path.join(self.tmp, "remotes", f"{name}.git")
            os.makedirs(os.path.dirname(bare), exist_ok=True)
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", bare], check=True)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            subprocess.run(["git", "clone", "-q", bare, path], check=True, capture_output=True)
        else:
            os.makedirs(path)
            subprocess.run(["git", "init", "-q", "-b", "main", path], check=True)
        for relative, text in (files or {"README.md": f"# {name}\n"}).items():
            write(os.path.join(path, relative), text)
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "first commit")
        if origin:
            git(path, "push", "-q", "-u", "origin", "main")
        return path

    def project(self, name="shop", key="SHOP", repos=()):
        self.cli("project", "add", name, "--key", key)
        for repo in repos:
            self.cli("project", "link-repo", name, repo)
        return name

    def run_dir(self, run_id):
        return os.path.join(self.home, "runs", run_id)

    def write_plan(self, run_id, jobs, facts=None, specs=True, title="test"):
        repos = []
        for job in jobs:
            for name in ([job["repo"]] if job.get("repo") else []) + job.get("reads", []):
                if name not in repos:
                    repos.append(name)
        facts = facts if facts is not None else {name: ("make test", "make deps") for name in repos}
        lines = []
        for name, (test, setup) in facts.items():
            lines += [f"### {name}", "- Language and framework: test",
                      f"- Test command: `{test}`" if test else "- Test command: none",
                      f"- Worktree setup: `{setup}`" if setup else "- Worktree setup: none",
                      "- Conventions: plain", ""]
        text = PLAN_HEAD.format(title=title, facts="\n" + "\n".join(lines), plan=json.dumps({"jobs": jobs}, indent=1))
        write(os.path.join(self.run_dir(run_id), "plan.md"), text)
        if specs:
            for job in jobs:
                write(os.path.join(self.run_dir(run_id), "jobs", job["id"], "spec.md"), f"# {job['id']}: {job['title']}\n")
        return text

    def feature_worktree(self, run, repo_name):
        found = next(repo for repo in run["repos"] if repo["name"] == repo_name)
        base = "origin/" + found["base_branch"] if git(found["path"], "remote") else found["base_branch"]
        git(found["path"], "worktree", "add", "-q", "--no-track", "-b", run["feature_branch"], found["feature_worktree"], base)
        return found["feature_worktree"]

    def job_worktree(self, started):
        git(started["repo_path"], "worktree", "add", "-q", "--no-track", "-b", started["branch"], started["worktree"],
            started["feature_branch"])
        return started["worktree"]

    def commit(self, worktree, name="change.txt", text="change\n", message="feat: change"):
        write(os.path.join(worktree, name), text)
        git(worktree, "add", name)
        git(worktree, "commit", "-q", "-m", message)
        return git(worktree, "rev-parse", "HEAD")

    def output(self, group, text="STATUS: DONE\nTest command: `make test`\nResult: 3 passed\n"):
        return write(os.path.join(group["group_dir"], "output.md"), text)

    def verdict(self, round_dir, gate, verdict="PASS", body="Fine."):
        text = f"VERDICT: {verdict}\n{body}\n"
        if verdict == "CHANGES":
            text += "\n## Must fix\n- `a.py:1`: wrong, fix it\n\n## Notes\n- [human] check the copy\n"
        return write(os.path.join(round_dir, f"{gate}.md"), text)

    def started_run(self, jobs, repos=("api",), facts=None, **flags):
        paths = {name: self.repo(name) for name in repos}
        self.project(repos=list(paths.values()))
        args = ["run", "new", "Build the thing", "--project", "shop"]
        for name, value in flags.items():
            option = "--" + name.replace("_", "-")
            args += [option] if value is True else [option, value]
        run = self.cli(*args)
        self.write_plan(run["id"], jobs, facts=facts)
        loaded = self.cli("plan", "load", run["id"])
        run = dict(run, repos=[dict(repo) for repo in loaded["repos"]])
        for repo in run["repos"]:
            self.feature_worktree(run, repo["name"])
        self.cli("run", "status", run["id"], "running")
        return run


def code_job(job_id="api-change", repo="api", steps=None, depends=(), title=None):
    pipeline = steps or [{"step": "build", "gates": ["review-code"]}]
    return {"id": job_id, "kind": "code", "repo": repo, "title": title or f"feat: {job_id}",
            "depends_on": list(depends), "pipeline": pipeline}


def files_job(job_id="findings", reads=(), depends=(), steps=None):
    job = {"id": job_id, "kind": "files", "title": f"Findings for {job_id}", "depends_on": list(depends),
           "pipeline": steps or [{"step": "research", "gates": ["review-findings"]}]}
    if reads:
        job["reads"] = list(reads)
    return job
