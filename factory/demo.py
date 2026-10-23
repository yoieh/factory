import json
import os
import subprocess

from . import FactoryError, db, home, improvements, issues, jobs, projects, questions, retro, runs
from .util import write_text

NAME = "demo"
GIT_ENV = {"GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
           "GIT_COMMITTER_NAME": "Demo", "GIT_COMMITTER_EMAIL": "demo@example.com"}

PLAN = """# Plan: {title}

{summary}

## Repo facts

### api
- Language and framework: Python service
- Test command: `make test`
- Worktree setup: `make deps`
- Conventions: short imperative commit subjects

### web
- Language and framework: TypeScript web app
- Test command: `npm test`
- Worktree setup: `npm ci`
- Conventions: conventional commits

## Jobs

| Job | Kind | Repo | Title | After | Pipeline | Why this pipeline |
|---|---|---|---|---|---|---|

```json
{jobs}
```

## Outside the code

## Risks and open points
"""

QUESTION = """QUESTION: Should the form keep the phone field optional?

**Why I ask:** The issue asks for a phone field but does not say whether it is required.

**Options**
- **A (recommended):** Keep it optional. Fewer people abandon the form.
- **B:** Make it required. Sales can call every lead back.

**Status:** The form is built; the validation waits for this answer.

<details><summary>Details</summary>

The api job accepts both, so only the form changes.

</details>
"""

RETRO = """# Retrospective: Export orders as CSV

The export landed in one round. The spec missed the date format, which cost a round on the web side.

## What went well
## What cost time or tokens
## Improvements

```json
{items}
```
"""


def git(path, *args):
    done = subprocess.run(["git", "-C", path] + list(args), capture_output=True, text=True,
                          env=dict(os.environ, **GIT_ENV))
    if done.returncode != 0:
        raise FactoryError(f"demo: git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout.strip()


def make_repo(path, files):
    os.makedirs(path, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", path], check=True, capture_output=True)
    for relative, text in files.items():
        write_text(os.path.join(path, relative), text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "first commit")
    return path


def plan(run, title, summary, job_list):
    write_text(os.path.join(run["run_dir"], "plan.md"),
               PLAN.format(title=title, summary=summary, jobs=json.dumps({"jobs": job_list}, indent=1)))
    for job in job_list:
        write_text(os.path.join(run["run_dir"], "jobs", job["id"], "spec.md"),
                   f"# {job['id']}: {job['title']}\n\nKind: {job.get('kind', 'code')}\n\n## Goal\n{job['title']}.\n\n"
                   "## Acceptance criteria\n- [ ] it works\n")


def branch(conn, run, repo_name):
    repo = next(entry for entry in runs.run_repos(conn, run["id"]) if entry["repo"] == repo_name)
    if not git(repo["path"], "branch", "--list", run["feature_branch"]):
        git(repo["path"], "branch", run["feature_branch"], "main")
    return repo


def produce(group, text="STATUS: DONE\nTest command: `make test`\nResult: 42 passed\n\n## Changes\n- `app.py`: the change\n"):
    write_text(os.path.join(group["group_dir"], "output.md"), text)


def verdict(round_dir, gate, value="PASS", body="The change does what the spec asks."):
    text = f"VERDICT: {value}\n{body}\n"
    if value == "CHANGES":
        text += "\n## Must fix\n- `api/leads.py:41`: the email is not validated; reject bad addresses with 422\n"
    text += "\n## Notes\n- [human] check the wording of the confirmation mail\n"
    write_text(os.path.join(round_dir, f"{gate}.md"), text)


def seed(conn):
    if projects.registered(conn, NAME):
        raise FactoryError("the demo project exists already; archive it, or use another factory home")
    root = home.path("demo")
    api = make_repo(os.path.join(root, "api"), {"README.md": "# api\n", "app.py": "print('api')\n"})
    web = make_repo(os.path.join(root, "web"), {"README.md": "# web\n", "app.ts": "console.log('web')\n"})
    projects.add(conn, NAME, "DEMO", "A demo project with an API and a web app")
    projects.link_repo(conn, NAME, api, role="service: the leads API")
    projects.link_repo(conn, NAME, web, role="frontend: the public web app")
    first = issues.add(conn, NAME, "Add a contact form for leads", "feature", "high", ["web", "api"],
                       "Visitors leave their name, email and phone. Sales gets the lead in the CRM inbox.")
    issues.add(conn, NAME, "Export orders as CSV", "feature", "medium", ["api"], "Finance wants a monthly export.")
    issues.add(conn, NAME, "The cart forgets items after login", "bug", "high", ["web"], "Steps: add an item, log in.")
    issues.add(conn, NAME, "Measure checkout latency", "research", "low", [], "Where do the slow requests come from?")
    issues.set_fields(conn, "DEMO-3", status="ready")
    issues.set_fields(conn, "DEMO-4", status="ready")

    running = runs.new(conn, "Add a contact form for leads", NAME, ["api", "web"], issue_key=first["key"],
                       session="demo-session-0001")
    plan(running, "contact form", "An endpoint in api, then the form in web that posts to it.", [
        {"id": "lead-endpoint", "kind": "code", "repo": "api", "title": "feat: add the leads endpoint", "depends_on": [],
         "template": "code-change", "pipeline": [{"step": "build", "gates": ["review-code", "review-security"]}]},
        {"id": "lead-form", "kind": "code", "repo": "web", "title": "feat: add the contact form", "depends_on": ["lead-endpoint"],
         "template": "ui-change", "pipeline": [{"step": "build", "gates": ["review-code", "review-ux"]}]},
    ])
    jobs.load_plan(conn, running["id"])
    for name in ("api", "web"):
        branch(conn, running, name)
    with db.tx(conn):
        runs.move(conn, running["id"], "confirm")
        runs.move(conn, running["id"], "running")
    ref = f"{running['id']}/lead-endpoint"
    started = jobs.start(conn, ref)
    jobs.store_agent(conn, ref, "lead-endpoint-01")
    produce(started["group"])
    jobs.step_sync(conn, ref)
    verdict(started["group"]["round_dir"], "review-code")
    verdict(started["group"]["round_dir"], "review-security", "CHANGES", "Input is not validated.")
    jobs.gate_sync(conn, ref)
    second = jobs.next_round(conn, ref)
    produce(started["group"])
    jobs.step_sync(conn, ref)
    verdict(second["round_dir"], "review-code")
    verdict(second["round_dir"], "review-security")
    jobs.gate_sync(conn, ref)
    jobs.advance(conn, ref)
    jobs.move_stage(conn, ref, "done")
    ref = f"{running['id']}/lead-form"
    started = jobs.start(conn, ref)
    jobs.store_agent(conn, ref, "lead-form-01")
    write_text(os.path.join(started["group"]["group_dir"], "output.md"), QUESTION)
    jobs.step_sync(conn, ref)
    runs.skip_add(conn, running["id"], "design.fetch_design", "UI built without the design", pr=True)

    confirm = runs.new(conn, "The cart forgets items after login", NAME, ["web"], issue_key="DEMO-3")
    plan(confirm, "cart after login", "Reproduce with a test, then fix the session merge.", [
        {"id": "cart-login", "kind": "code", "repo": "web", "title": "fix: keep the cart after login", "depends_on": [],
         "template": "bug-fix", "pipeline": [{"step": "reproduce", "gates": ["review-code"]},
                                             {"step": "fix", "gates": ["review-code", "verify"]}]},
    ])
    jobs.load_plan(conn, confirm["id"])
    with db.tx(conn):
        runs.move(conn, confirm["id"], "confirm")
    questions.ask(conn, confirm["id"], "Approve the plan? Answer yes, or say what to change.", plain=True,
                  path=runs.run_paths(confirm["id"])["plan"])

    finished = runs.new(conn, "Export orders as CSV", NAME, ["api"], issue_key="DEMO-2")
    plan(finished, "orders export", "One endpoint that streams the CSV.", [
        {"id": "orders-csv", "kind": "code", "repo": "api", "title": "feat: export orders as CSV", "depends_on": [],
         "template": "code-change", "pipeline": [{"step": "build", "gates": ["review-code"]}]},
    ])
    jobs.load_plan(conn, finished["id"])
    branch(conn, finished, "api")
    with db.tx(conn):
        runs.move(conn, finished["id"], "confirm")
        runs.move(conn, finished["id"], "running")
    ref = f"{finished['id']}/orders-csv"
    started = jobs.start(conn, ref)
    produce(started["group"])
    jobs.step_sync(conn, ref)
    verdict(started["group"]["round_dir"], "review-code")
    jobs.gate_sync(conn, ref)
    jobs.advance(conn, ref)
    jobs.move_stage(conn, ref, "done")
    with db.tx(conn):
        runs.move(conn, finished["id"], "finishing")
        runs.move(conn, finished["id"], "done")
    write_text(os.path.join(finished["run_dir"], "report.md"),
               "# Report\n\n- api: feature branch pushed nowhere (no code host); 42 tests passed.\n")
    write_text(runs.run_paths(finished["id"])["retro_file"], RETRO.format(items=json.dumps({"improvements": [
        {"key": "spec-names-date-format", "target": "factory", "where": "catalogue/steps/build.md, Rules",
         "title": "Specs name the date format of every export",
         "evidence": "jobs/orders-csv/groups/01-build/round-1/review-code.md", "proposal": "Under Rules, add: name the date format.",
         "impact": "high"},
        {"key": "issue-template-fields", "target": "process", "title": "Issues for exports list the columns",
         "evidence": "issue.md has no column list", "proposal": "Add a Columns section to export issues.", "impact": "medium"},
        {"key": "api-test-fixture", "target": "repo", "repo": "api", "where": "tests/conftest.py",
         "title": "A shared orders fixture", "evidence": "three tests build the same orders by hand",
         "proposal": "Add an orders fixture.", "impact": "low"},
    ]}, indent=1)))
    loaded = retro.load(conn, finished["id"])
    improvements.decide(conn, loaded["new"][2], "accepted", "Yes, keep it small")
    return {
        "project": NAME,
        "repos": [api, web],
        "runs": [running["id"], confirm["id"], finished["id"]],
        "issues": [issue["key"] for issue in issues.listing(conn, NAME)],
        "next": "factory serve, then open the dashboard",
    }
