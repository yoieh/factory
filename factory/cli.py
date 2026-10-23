import argparse
import json
import os
import sqlite3
import sys
import traceback

from . import (
    NAME, VERSION, FactoryError, adapters, after, catalogue, connected, db, demo, designs, doctor, home, improvements, inbox,
    issues, jobs, land, legacy, projects, questions, retro, runs, server, skills, usage,
)
from .util import parse_json_value


class ArgumentError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ArgumentError(message)


def common():
    parent = Parser(add_help=False)
    parent.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
    return parent


def add(subparsers, name, handler, help_text=None, table=False):
    parser = subparsers.add_parser(name, parents=[common()], help=help_text, description=help_text)
    if table:
        parser.add_argument("--table", action="store_true", help="print a table instead of JSON")
    parser.set_defaults(handler=handler, table_ok=table)
    return parser


def group(subparsers, name, help_text):
    parser = subparsers.add_parser(name, help=help_text, description=help_text)
    return parser.add_subparsers(dest=f"{name}_action", required=True, parser_class=Parser, metavar="<action>")


def build():
    parser = Parser(prog=NAME, description="The software factory CLI. Every command prints JSON.")
    parser.add_argument("--version", action="version", version=f"{NAME} {VERSION}")
    top = parser.add_subparsers(dest="command", required=True, parser_class=Parser, metavar="<command>")

    add(top, "init", cmd_init, "Create the factory home, config.json and factory.db. Idempotent.")
    entry = add(top, "doctor", cmd_doctor, "Check the machine and print fixes.")
    entry.add_argument("--project")
    entry = add(top, "paths", cmd_paths, "All paths of a run.")
    entry.add_argument("run_id")

    add(top, "agents", cmd_agents, "The subagent_type per role for the install this folder sees.")
    entry = add(top, "serve", cmd_serve, "Start the dashboard on 127.0.0.1.")
    entry.add_argument("--port", type=int)
    entry.add_argument("--quiet", action="store_true", help="no request log")
    add(top, "demo", cmd_demo, "Seed a demo project with runs, questions, issues and improvements.")

    skill = group(top, "skill", "Copies of the skill and agents, and plugin folders.")
    entry = add(skill, "install", cmd_skill_install, "Copy the skill and agents for the user or into a repo.")
    target = entry.add_mutually_exclusive_group(required=True)
    target.add_argument("--user", action="store_true")
    target.add_argument("--repo")
    entry.add_argument("--project")
    entry = add(skill, "uninstall", cmd_skill_uninstall, "Remove a user or repo install.")
    target = entry.add_mutually_exclusive_group(required=True)
    target.add_argument("--user", action="store_true")
    target.add_argument("--repo")
    add(skill, "status", cmd_skill_status, "Every recorded install, its version and drift from the core.")
    entry = add(skill, "plugin", cmd_skill_plugin, "Write a Claude Code plugin folder with the skill and agents.")
    entry.add_argument("dir")

    config = group(top, "config", "Read or change config.json.")
    add(config, "show", cmd_config_show, "Print config.json.")
    entry = add(config, "set", cmd_config_set, "Set one key of config.json (value as JSON).")
    entry.add_argument("key")
    entry.add_argument("value")

    project = group(top, "project", "Projects: repos, integrations, knowledge, defaults.")
    entry = add(project, "add", cmd_project_add, "Add a project.")
    entry.add_argument("name")
    entry.add_argument("--key", required=True)
    entry.add_argument("--description")
    entry = add(project, "link-repo", cmd_project_link, "Link a git checkout to a project.")
    entry.add_argument("name")
    entry.add_argument("path")
    entry.add_argument("--as", dest="as_name")
    entry.add_argument("--role")
    entry.add_argument("--default-branch")
    entry = add(project, "unlink-repo", cmd_project_unlink, "Unlink a repo.")
    entry.add_argument("name")
    entry.add_argument("repo")
    entry = add(project, "set", cmd_project_set, "Set one key of project.json (value as JSON).")
    entry.add_argument("name")
    entry.add_argument("key")
    entry.add_argument("value")
    entry = add(project, "show", cmd_project_show, "The resolved project, with availability and skips.")
    entry.add_argument("name", nargs="?")
    add(project, "list", cmd_project_list, "All projects.", table=True)
    entry = add(project, "here", cmd_project_here, "Which project the current directory resolves to, and why.")
    entry.add_argument("--project")
    entry = add(project, "pin", cmd_project_pin, "Write <repo>/.factory/project.")
    entry.add_argument("name")
    entry.add_argument("--repo")
    entry = add(project, "archive", cmd_project_archive, "Archive a project.")
    entry.add_argument("name")

    cat = group(top, "catalogue", "Steps, pipeline templates and adapters.")
    entry = add(cat, "show", cmd_catalogue_show, "The merged catalogue for a project.")
    entry.add_argument("--project")
    entry = add(cat, "check", cmd_catalogue_check, "Validate every catalogue file.")
    entry.add_argument("--project")

    adapter = group(top, "adapter", "Adapters for trackers, code hosts and design tools.")
    entry = add(adapter, "run", cmd_adapter_run,
                "Run a script capability: adapter run <name> <capability> [--project p] [--repo r] [--<placeholder> <value>]...")
    entry.add_argument("name")
    entry.add_argument("capability")
    entry.add_argument("rest", nargs=argparse.REMAINDER)
    entry = add(adapter, "new", cmd_adapter_new, "Write a skeleton adapter folder to fill in.")
    entry.add_argument("name")
    entry.add_argument("--slot", required=True, choices=["tracker", "code_host", "design"])
    entry.add_argument("--dir")

    issue = group(top, "issue", "The central issue tracker.")
    entry = add(issue, "add", cmd_issue_add, "Add an issue.")
    entry.add_argument("project")
    entry.add_argument("title")
    entry.add_argument("--type", default="task")
    entry.add_argument("--priority", default="medium")
    entry.add_argument("--label", action="append", default=[])
    entry.add_argument("--body")
    entry.add_argument("--body-file")
    entry = add(issue, "list", cmd_issue_list, "List issues.", table=True)
    entry.add_argument("--project")
    entry.add_argument("--status", action="append", default=[])
    entry.add_argument("--type")
    entry.add_argument("--label")
    entry.add_argument("--text")
    entry = add(issue, "show", cmd_issue_show, "One issue with body, links and history.")
    entry.add_argument("key")
    entry = add(issue, "set", cmd_issue_set, "Change status, priority, title or labels.")
    entry.add_argument("key")
    entry.add_argument("--status")
    entry.add_argument("--priority")
    entry.add_argument("--title")
    entry.add_argument("--label", action="append", default=[])
    entry.add_argument("--unlabel", action="append", default=[])
    entry = add(issue, "edit", cmd_issue_edit, "Replace the body.")
    entry.add_argument("key")
    entry.add_argument("--body-file", required=True)
    entry = add(issue, "link", cmd_issue_link, "Link a run, PR, issue or improvement.")
    entry.add_argument("key")
    target = entry.add_mutually_exclusive_group(required=True)
    target.add_argument("--run")
    target.add_argument("--pr")
    target.add_argument("--issue")
    target.add_argument("--improvement")
    entry = add(issue, "import", cmd_issue_import, "Mirror an external issue.")
    entry.add_argument("project")
    entry.add_argument("external_key")
    entry.add_argument("--title", required=True)
    entry.add_argument("--url")
    entry.add_argument("--external-status")
    entry.add_argument("--body-file")

    entry = add(issue, "sync", cmd_issue_sync, "Refresh mirrored issues through the tracker's sync script.")
    entry.add_argument("project")

    run = group(top, "run", "Runs.")
    entry = add(run, "new", cmd_run_new, "Create a run.")
    entry.add_argument("feature")
    entry.add_argument("--project")
    entry.add_argument("--repo", action="append", default=[])
    entry.add_argument("--issue")
    entry.add_argument("--external-key")
    entry.add_argument("--external-url")
    entry.add_argument("--branch")
    entry.add_argument("--base")
    entry.add_argument("--parallel", type=int)
    entry.add_argument("--max-rounds", type=int)
    entry.add_argument("--confirm-plan", action="store_true")
    entry.add_argument("--no-pr", action="store_true")
    entry.add_argument("--session")
    entry.add_argument("--by", default="human")
    entry = add(run, "session", cmd_run_session, "Add an orchestrator session to a run.")
    entry.add_argument("run_id")
    entry.add_argument("session", nargs="?")
    entry = add(run, "status", cmd_run_status, "Move a run.")
    entry.add_argument("run_id")
    entry.add_argument("status")
    entry = add(run, "rounds", cmd_run_rounds, "Change the round cap of groups not yet passed.")
    entry.add_argument("run_id")
    entry.add_argument("rounds", type=int)
    entry = add(run, "pr", cmd_run_pr, "Store a PR url for a repo of the run.")
    entry.add_argument("run_id")
    entry.add_argument("url")
    entry.add_argument("--repo", required=True)
    entry = add(run, "prs", cmd_run_prs, "Read PR states through the code host's pr_state.")
    entry.add_argument("run_id", nargs="?")
    entry.add_argument("--force", action="store_true")
    entry = add(run, "show", cmd_run_show, "Everything about a run.")
    entry.add_argument("run_id")
    entry = add(run, "list", cmd_run_list, "Runs.", table=True)
    entry.add_argument("--project")
    entry.add_argument("--archived", action="store_true")
    entry = add(run, "archive", cmd_run_archive, "Hide a done or failed run and remove its worktrees.")
    entry.add_argument("run_id")
    entry.add_argument("--force", action="store_true")
    entry = add(run, "unarchive", cmd_run_unarchive, "Show an archived run again.")
    entry.add_argument("run_id")
    entry = add(run, "delete", cmd_run_delete, "Remove a run for good.")
    entry.add_argument("run_id")
    entry.add_argument("--force", action="store_true")

    plan = group(top, "plan", "The plan of a run.")
    entry = add(plan, "load", cmd_plan_load, "Read and validate plan.md.")
    entry.add_argument("run_id")

    job = group(top, "job", "Jobs of a run.")
    entry = add(job, "ready", cmd_job_ready, "Jobs that can start now.")
    entry.add_argument("run_id")
    for name, handler, text in (
        ("start", cmd_job_start, "Start a queued job."),
        ("show", cmd_job_show, "Everything about a job."),
        ("round", cmd_job_round, "Start the next round of the current group."),
        ("next", cmd_job_next, "Move to the next group, or to merging or delivering."),
    ):
        entry = add(job, name, handler, text)
        entry.add_argument("ref")
    entry = add(job, "agent", cmd_job_agent, "Store the current producer's name or id.")
    entry.add_argument("ref")
    entry.add_argument("agent")
    entry = add(job, "stage", cmd_job_stage, "merging or delivering to done, or merging back to running.")
    entry.add_argument("ref")
    entry.add_argument("stage")
    entry = add(job, "drop", cmd_job_drop, "Give up an unfinished job, with the reason.")
    entry.add_argument("ref")
    entry.add_argument("reason")
    entry = add(job, "rework", cmd_job_rework, "Send a done or dropped job back to a producer.")
    entry.add_argument("ref")
    entry.add_argument("note")
    entry.add_argument("--group", type=int)

    step = group(top, "step", "Producer steps.")
    entry = add(step, "sync", cmd_step_sync, "Read the current group's output.md.")
    entry.add_argument("ref")
    entry = add(step, "save", cmd_step_save,
                "Store an agent's file from stdin when the harness refused its write: output.md, a gate file of the round, or out/<file>.")
    entry.add_argument("ref")
    entry.add_argument("path")
    entry.add_argument("--by")

    gate = group(top, "gate", "Gate steps.")
    entry = add(gate, "sync", cmd_gate_sync, "Read the round's gate files.")
    entry.add_argument("ref")
    entry = add(gate, "human", cmd_gate_human, "Record a human gate's verdict.")
    entry.add_argument("ref")
    entry.add_argument("gate")
    entry.add_argument("verdict", choices=["pass", "changes"])
    entry.add_argument("--note")

    question = group(top, "question", "Questions for a human.")
    entry = add(question, "ask", cmd_question_ask, "Store a question.")
    entry.add_argument("run_id")
    entry.add_argument("text")
    entry.add_argument("--job")
    entry.add_argument("--by", default="orchestrator")
    entry.add_argument("--path")
    entry.add_argument("--plain", action="store_true")
    entry = add(question, "answer", cmd_question_answer, "Store an answer.")
    entry.add_argument("id", type=int)
    entry.add_argument("text")
    entry.add_argument("--delivered", action="store_true")
    entry.add_argument("--by", default="human")
    entry = add(question, "clarify", cmd_question_clarify, "Ask back on a question.")
    entry.add_argument("id", type=int)
    entry.add_argument("text")
    entry.add_argument("--delivered", action="store_true")
    entry.add_argument("--by", default="human")
    entry = add(question, "reply", cmd_question_reply, "Store the reply to a follow-up.")
    entry.add_argument("id", type=int)
    entry.add_argument("--text")
    entry = add(question, "resolve", cmd_question_resolve, "Mark an answered question resolved.")
    entry.add_argument("id", type=int)
    entry = add(question, "list", cmd_question_list, "Questions with their threads.", table=True)
    entry.add_argument("--run")
    entry.add_argument("--status", action="append", default=[])
    entry = add(question, "wait", cmd_question_wait, "Wait for answers and follow-ups not delivered yet.")
    entry.add_argument("run_id")
    entry.add_argument("--timeout", type=float, default=1800)

    agent = group(top, "agent", "Connected agents: outside tools that read the inbox and answer for the user.")
    entry = add(agent, "add", cmd_agent_add, "Connect an agent by name, or change its description.")
    entry.add_argument("name")
    entry.add_argument("--description")
    add(agent, "list", cmd_agent_list, "Connected agents with when they were last seen and what they answered.", table=True)
    entry = add(agent, "show", cmd_agent_show, "One connected agent and its latest answers and follow-ups.")
    entry.add_argument("name")
    entry = add(agent, "remove", cmd_agent_remove, "Forget a connected agent. Its answers keep its name.")
    entry.add_argument("name")

    entry = add(top, "inbox", cmd_inbox, "Everything that waits for the user: open questions, plan confirms, human gates, improvements.")
    entry.add_argument("--project")
    entry.add_argument("--as", dest="agent", help="the connected agent that reads; marks it seen")
    entry = add(top, "events", cmd_events, "Events after a cursor, to follow the factory without polling every run.")
    entry.add_argument("--since", type=int, help="the cursor of the last call; without it, the latest events")
    entry.add_argument("--run")
    entry.add_argument("--kind", action="append", default=[], help="an event kind or prefix, such as question or run.status")
    entry.add_argument("--limit", type=int, default=200)
    entry.add_argument("--wait", action="store_true", help="block until there is a new event")
    entry.add_argument("--timeout", type=float, default=1800)
    entry.add_argument("--as", dest="agent", help="the connected agent that reads; marks it seen")

    skip = group(top, "skip", "Missing optional dependencies.")
    entry = add(skip, "add", cmd_skip_add, "Record a skip.")
    entry.add_argument("run_id")
    entry.add_argument("dependency")
    entry.add_argument("effect")
    entry.add_argument("--pr", action="store_true")
    entry.add_argument("--job")
    entry = add(skip, "list", cmd_skip_list, "The run's skips.")
    entry.add_argument("run_id")
    entry.add_argument("--pr", action="store_true")

    design = group(top, "design", "Designs fetched by a design adapter.")
    entry = add(design, "links", cmd_design_links, "Design links in a text file.")
    entry.add_argument("path")
    entry.add_argument("--project")
    entry = add(design, "target", cmd_design_target, "The png and md paths for a frame.")
    entry.add_argument("run_id")
    entry.add_argument("--name", required=True)
    entry = add(design, "check", cmd_design_check, "Drop downloads that are not PNG and strip scrub patterns.")
    entry.add_argument("run_id")

    land_group = group(top, "land", "Review a done run's PRs and mark them ready or merge them, as the project's defaults.land says.")
    entry = add(land_group, "check", cmd_land_check, "Read the checks, mergeability and head of the run's PRs.")
    entry.add_argument("run_id")
    entry.add_argument("--repo")
    entry.add_argument("--wait", type=int, default=0, help="seconds to wait while checks still run")
    entry = add(land_group, "start", cmd_land_start, "Open a new land round for a repo's PR: the lander's paths.")
    entry.add_argument("run_id")
    entry.add_argument("--repo", required=True)
    entry = add(land_group, "save", cmd_land_save,
                "Store the lander's review file, or with --watch the watcher's watch file, from stdin when the harness refused its write.")
    entry.add_argument("run_id")
    entry.add_argument("--repo", required=True)
    entry.add_argument("--watch", action="store_true", help="store the watch file of the steps after merge")
    entry = add(land_group, "apply", cmd_land_apply,
                "Act on the lander's verdict: mark the PR ready or merge it when every check passes and the mode allows it.")
    entry.add_argument("run_id")
    entry.add_argument("--repo", required=True)
    entry.add_argument("--dry-run", action="store_true", help="say what would happen and change nothing")
    entry = add(land_group, "after", cmd_land_after,
                "Start the steps after a merged PR, as the repo's after_merge says: the merge commit, the tag and the paths.")
    entry.add_argument("run_id")
    entry.add_argument("--repo", required=True)
    entry = add(land_group, "build", cmd_land_build, "Build the merge commit in its own worktree and keep the artifact.")
    entry.add_argument("run_id")
    entry.add_argument("--repo", required=True)
    entry.add_argument("--timeout", type=int, help="seconds the build may take (default 3600)")
    entry = add(land_group, "publish", cmd_land_publish,
                "Upload the build as a release and a package, as the repo's after_merge says.")
    entry.add_argument("run_id")
    entry.add_argument("--repo", required=True)
    entry.add_argument("--dry-run", action="store_true", help="say what would happen and change nothing")
    entry = add(land_group, "watched", cmd_land_watched, "Read the watcher's deploy verdict.")
    entry.add_argument("run_id")
    entry.add_argument("--repo", required=True)

    retro_group = group(top, "retro", "The retrospective.")
    entry = add(retro_group, "load", cmd_retro_load, "Read retro.md into improvements.")
    entry.add_argument("run_id")

    legacy_group = group(top, "legacy", "History from the old board-based factory.")
    entry = add(legacy_group, "import", cmd_legacy_import,
                "Import the old factory's merged jobs as finished runs and its retro proposals as improvements.")
    entry.add_argument("dir", help="the old factory folder, the one with board.json, jobs/ and retros/")
    entry.add_argument("--project", required=True)
    entry.add_argument("--repo", help="the repo the old jobs changed, when the project has more than one")
    entry.add_argument("--issues", help="a JSON map from issue keys to {\"old\": <job id or backlog text>}")
    entry.add_argument("--dry-run", action="store_true", help="count what would be imported and change nothing")

    improvement = group(top, "improvement", "Improvements from retrospectives.")
    entry = add(improvement, "list", cmd_improvement_list, "Improvements with seen_in.", table=True)
    entry.add_argument("--run")
    entry.add_argument("--project")
    entry.add_argument("--status", action="append", default=[])
    entry = add(improvement, "decide", cmd_improvement_decide, "A decision by the user or an agent on their behalf.")
    entry.add_argument("id", type=int)
    entry.add_argument("status", choices=list(improvements.STATUSES))
    entry.add_argument("--note")
    entry.add_argument("--by", default="human")
    entry = add(improvement, "start", cmd_improvement_start, "Group accepted items per repo and base branch.")
    entry.add_argument("--id", dest="ids", type=int, action="append", default=[])
    entry.add_argument("--factory-base")
    entry = add(improvement, "pr", cmd_improvement_pr, "Record the PR that applies items.")
    entry.add_argument("ids", type=int, nargs="+")
    entry.add_argument("--url", required=True)
    entry.add_argument("--branch", required=True)
    entry = add(improvement, "prs", cmd_improvement_prs, "Open PRs of accepted items.")
    entry.add_argument("--check", action="store_true")

    usage_group = group(top, "usage", "Token usage from transcripts.")
    entry = add(usage_group, "sync", cmd_usage_sync, "Read new transcript lines.")
    entry.add_argument("run_id", nargs="?")
    entry = add(usage_group, "show", cmd_usage_show, "Tokens and estimated cost per node, job, round and total.")
    entry.add_argument("run_id")

    entry = add(top, "event", cmd_event, "Record a free event.")
    entry.add_argument("run_id")
    entry.add_argument("kind")
    entry.add_argument("--job")
    entry.add_argument("--payload")
    return parser


def project_for(conn, flag):
    if flag:
        projects.require(conn, flag)
        return projects.load(flag)
    found = projects.resolution(conn)
    if found["project"]:
        return projects.load(found["project"])
    if found["toplevel"]:
        return projects.normalise(projects.adhoc_config(conn, found["toplevel"]))
    return None


def cmd_init(conn, args):
    return home.init(conn)


def cmd_doctor(conn, args):
    return doctor.run(conn, args.project)


def cmd_paths(conn, args):
    return runs.paths(conn, args.run_id)


def cmd_serve(conn, args):
    dashboard = home.load_config()["dashboard"]
    port = args.port if args.port is not None else dashboard["port"]
    if not 0 <= port <= 65535:
        raise FactoryError("--port must be a port number", code=2)
    conn.close()
    return server.serve(port, args.quiet, dashboard["hosts"])


def cmd_demo(conn, args):
    return demo.seed(conn)


def cmd_legacy_import(conn, args):
    return legacy.import_board(conn, args.dir, args.project, args.repo, args.issues, args.dry_run)


def cmd_agents(conn, args):
    return skills.agents(conn)


def cmd_skill_install(conn, args):
    return skills.install(conn, args.user, args.repo, args.project)


def cmd_skill_uninstall(conn, args):
    return skills.uninstall(conn, args.user, args.repo)


def cmd_skill_status(conn, args):
    return skills.status(conn)


def cmd_skill_plugin(conn, args):
    return skills.plugin(conn, args.dir)


def cmd_config_show(conn, args):
    return home.load_config()


def cmd_config_set(conn, args):
    return home.set_config(conn, args.key, parse_json_value(args.value))


def cmd_project_add(conn, args):
    return projects.add(conn, args.name, args.key, args.description)


def cmd_project_link(conn, args):
    return projects.link_repo(conn, args.name, args.path, args.as_name, args.role, args.default_branch)


def cmd_project_unlink(conn, args):
    return projects.unlink_repo(conn, args.name, args.repo)


def cmd_project_set(conn, args):
    return projects.set_key(conn, args.name, args.key, parse_json_value(args.value))


def cmd_project_show(conn, args):
    shown = projects.show(conn, args.name)
    if not shown.get("adhoc"):
        shown["installs"] = skills.project_installs(conn, shown["name"], shown["repos"])
    return shown


def cmd_project_list(conn, args):
    return projects.listing(conn)


def cmd_project_here(conn, args):
    return projects.resolution(conn, args.project)


def cmd_project_pin(conn, args):
    return projects.pin(conn, args.name, args.repo)


def cmd_project_archive(conn, args):
    return projects.archive(conn, args.name)


def cmd_catalogue_show(conn, args):
    return catalogue.show(home.load_config(), project_for(conn, args.project))


def cmd_catalogue_check(conn, args):
    result = catalogue.check(home.load_config(), project_for(conn, args.project))
    return result, 0 if result["ok"] else 1


def adapter_flags(rest):
    found = {}
    index = 0
    while index < len(rest):
        flag = rest[index]
        if flag == "--json":
            index += 1
            continue
        if not flag.startswith("--") or len(flag) < 3:
            raise FactoryError(f"unexpected argument {flag!r}: use --<placeholder> <value>", code=2)
        name, sep, value = flag[2:].partition("=")
        if not sep:
            if index + 1 >= len(rest):
                raise FactoryError(f"{flag} needs a value", code=2)
            value = rest[index + 1]
            index += 1
        found[name.replace("-", "_")] = value
        index += 1
    return found


def cmd_adapter_run(conn, args):
    flags = adapter_flags(args.rest)
    project_name = flags.pop("project", None)
    repo_name = flags.pop("repo", None)
    project = projects.show(conn, project_name)
    entry = adapters.project_adapter(project, args.name)
    repo = None
    if repo_name:
        repo = next((item for item in project["repos"] if item["name"] == repo_name), None)
        if repo is None:
            raise FactoryError(f"{repo_name!r} is not a repo of project {project['name']}")
    outcome = adapters.run_capability(project, entry, args.capability, repo, flags)
    if outcome["status"] == "credentials":
        raise FactoryError(f"{args.name} {args.capability}: credentials missing; see ## Setup in {entry['adapter_md']}", code=3)
    if outcome["status"] != "ok":
        raise FactoryError(f"{args.name} {args.capability} failed, nothing changed: {outcome['reason']}")
    with db.tx(conn):
        db.event(conn, "adapter.run", adapter=args.name, capability=args.capability, project=project["name"])
    return outcome["data"]


def cmd_adapter_new(conn, args):
    folder = os.path.abspath(os.path.expanduser(args.dir)) if args.dir else home.path("catalogue")
    result = adapters.new(args.name, args.slot, folder)
    with db.tx(conn):
        db.event(conn, "adapter.new", adapter=args.name, slot=args.slot, dir=result["dir"])
    return result


def cmd_issue_sync(conn, args):
    return issues.sync(conn, args.project)


def cmd_run_pr(conn, args):
    return runs.store_pr(conn, args.run_id, args.url, args.repo)


def cmd_run_prs(conn, args):
    return runs.check_prs(conn, args.run_id, args.force)


def cmd_land_check(conn, args):
    return land.check(conn, args.run_id, args.repo, args.wait)


def cmd_land_start(conn, args):
    return land.start(conn, args.run_id, args.repo)


def cmd_land_save(conn, args):
    if args.watch:
        return after.save_watch(conn, args.run_id, args.repo, sys.stdin.read())
    return land.save(conn, args.run_id, args.repo, sys.stdin.read())


def cmd_land_apply(conn, args):
    return land.apply(conn, args.run_id, args.repo, args.dry_run)


def cmd_land_after(conn, args):
    return after.start(conn, args.run_id, args.repo)


def cmd_land_build(conn, args):
    return after.build(conn, args.run_id, args.repo, args.timeout)


def cmd_land_publish(conn, args):
    return after.publish(conn, args.run_id, args.repo, args.dry_run)


def cmd_land_watched(conn, args):
    return after.watched(conn, args.run_id, args.repo)


def cmd_design_links(conn, args):
    return designs.links(conn, args.path, args.project)


def cmd_design_target(conn, args):
    return designs.target(conn, args.run_id, args.name)


def cmd_design_check(conn, args):
    return designs.check(conn, args.run_id)


def cmd_retro_load(conn, args):
    return retro.load(conn, args.run_id)


def cmd_improvement_list(conn, args):
    return improvements.listing(conn, args.run, args.project, args.status)


def cmd_improvement_decide(conn, args):
    return improvements.decide(conn, args.id, args.status, args.note, args.by)


def cmd_improvement_start(conn, args):
    return improvements.start(conn, args.ids, args.factory_base)


def cmd_improvement_pr(conn, args):
    return improvements.record_pr(conn, args.ids, args.url, args.branch)


def cmd_improvement_prs(conn, args):
    return improvements.prs(conn, args.check)


def cmd_usage_sync(conn, args):
    return usage.sync(conn, args.run_id)


def cmd_usage_show(conn, args):
    return usage.show(conn, args.run_id)


def cmd_issue_add(conn, args):
    projects.require(conn, args.project)
    return issues.add(conn, args.project, args.title, args.type, args.priority, args.label, args.body, args.body_file)


def cmd_issue_list(conn, args):
    return issues.listing(conn, args.project, args.status, args.type, args.label, args.text)


def cmd_issue_show(conn, args):
    return issues.show(conn, args.key)


def cmd_issue_set(conn, args):
    return issues.set_fields(conn, args.key, args.status, args.priority, args.title, args.label, args.unlabel)


def cmd_issue_edit(conn, args):
    return issues.edit(conn, args.key, args.body_file)


def cmd_issue_link(conn, args):
    for kind in ("run", "pr", "issue", "improvement"):
        value = getattr(args, kind)
        if value is not None:
            return issues.add_link(conn, args.key, kind, value)


def cmd_issue_import(conn, args):
    return issues.import_issue(conn, args.project, args.external_key, args.title, args.url, args.external_status, args.body_file)


def cmd_run_new(conn, args):
    return runs.new(
        conn, args.feature, args.project, args.repo, args.issue, args.external_key, args.external_url, args.branch,
        args.base, args.parallel, args.max_rounds, args.confirm_plan, args.no_pr, args.session, author=args.by,
    )


def cmd_run_session(conn, args):
    return runs.add_session_command(conn, args.run_id, args.session)


def cmd_run_status(conn, args):
    return runs.set_status(conn, args.run_id, args.status)


def cmd_run_rounds(conn, args):
    return runs.set_rounds(conn, args.run_id, args.rounds)


def cmd_run_show(conn, args):
    return runs.show(conn, args.run_id)


def cmd_run_list(conn, args):
    return runs.listing(conn, args.project, args.archived)


def cmd_run_archive(conn, args):
    return runs.archive(conn, args.run_id, args.force)


def cmd_run_unarchive(conn, args):
    return runs.unarchive(conn, args.run_id)


def cmd_run_delete(conn, args):
    return runs.delete(conn, args.run_id, args.force)


def cmd_plan_load(conn, args):
    return jobs.load_plan(conn, args.run_id)


def cmd_job_ready(conn, args):
    return jobs.ready(conn, args.run_id)


def cmd_job_start(conn, args):
    return jobs.start(conn, args.ref)


def cmd_job_show(conn, args):
    return jobs.show(conn, args.ref)


def cmd_job_agent(conn, args):
    return jobs.store_agent(conn, args.ref, args.agent)


def cmd_job_round(conn, args):
    return jobs.next_round(conn, args.ref)


def cmd_job_next(conn, args):
    return jobs.advance(conn, args.ref)


def cmd_job_stage(conn, args):
    return jobs.move_stage(conn, args.ref, args.stage)


def cmd_job_drop(conn, args):
    return jobs.drop(conn, args.ref, args.reason)


def cmd_step_save(conn, args):
    return jobs.save(conn, args.ref, args.path, sys.stdin.read(), args.by)


def cmd_job_rework(conn, args):
    return jobs.rework(conn, args.ref, args.note, args.group)


def cmd_step_sync(conn, args):
    return jobs.step_sync(conn, args.ref)


def cmd_gate_sync(conn, args):
    return jobs.gate_sync(conn, args.ref)


def cmd_gate_human(conn, args):
    return jobs.gate_human(conn, args.ref, args.gate, args.verdict, args.note)


def cmd_question_ask(conn, args):
    path = os.path.abspath(args.path) if args.path else None
    stored = questions.ask(conn, args.run_id, args.text, args.job, args.by, path, args.plain)
    return questions.with_thread(conn, questions.row(conn, stored["id"]))


def cmd_question_answer(conn, args):
    return questions.answer(conn, args.id, args.text, args.delivered, args.by)


def cmd_question_clarify(conn, args):
    return questions.clarify(conn, args.id, args.text, args.delivered, args.by)


def cmd_question_reply(conn, args):
    return questions.reply(conn, args.id, args.text)


def cmd_question_resolve(conn, args):
    return questions.resolve(conn, args.id)


def cmd_question_list(conn, args):
    return questions.listing(conn, args.run, args.status)


def cmd_question_wait(conn, args):
    return questions.wait(conn, args.run_id, args.timeout)


def cmd_agent_add(conn, args):
    return connected.add(conn, args.name, args.description)


def cmd_agent_list(conn, args):
    return connected.listing(conn)


def cmd_agent_show(conn, args):
    return connected.show(conn, args.name)


def cmd_agent_remove(conn, args):
    return connected.remove(conn, args.name)


def cmd_inbox(conn, args):
    return inbox.inbox(conn, args.project, args.agent)


def cmd_events(conn, args):
    return inbox.events(conn, args.since, args.run, args.kind, args.limit, args.wait, args.timeout, agent=args.agent)


def cmd_skip_add(conn, args):
    return runs.skip_add(conn, args.run_id, args.dependency, args.effect, args.pr, args.job)


def cmd_skip_list(conn, args):
    runs.get(conn, args.run_id)
    return runs.skips(conn, args.run_id, args.pr)


def cmd_event(conn, args):
    payload = parse_json_value(args.payload) if args.payload else None
    return runs.free_event(conn, args.run_id, args.kind, args.job, payload)


TABLE_COLUMNS = {
    "run": ["id", "project", "status", "jobs_done", "jobs", "open_questions", "feature"],
    "issue": ["key", "status", "type", "priority", "title"],
    "project": ["name", "key", "repos", "archived", "description"],
    "question": ["id", "run_id", "job_id", "asked_by", "status"],
    "improvement": ["id", "run_id", "target", "impact", "status", "title"],
    "agent": ["name", "seen_at", "answers", "follow_ups", "description"],
}


def table(rows, columns):
    def cell(value):
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        text = "" if value is None else str(value)
        return text if len(text) <= 60 else text[:57] + "..."

    cells = [[cell(row.get(column)) for column in columns] for row in rows]
    widths = [max([len(column)] + [len(line[index]) for line in cells]) for index, column in enumerate(columns)]
    lines = ["  ".join(column.ljust(widths[index]) for index, column in enumerate(columns)).rstrip()]
    for line in cells:
        lines.append("  ".join(value.ljust(widths[index]) for index, value in enumerate(line)).rstrip())
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build()
    try:
        args = parser.parse_args(argv)
    except ArgumentError as error:
        print(f"{NAME}: {error}", file=sys.stderr)
        return 2
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 0
    conn = None
    try:
        home.ensure()
        conn = home.connect()
        synced = home.sync_core()
        if synced:
            with db.tx(conn):
                db.event(conn, "core.sync", **synced)
        result = args.handler(conn, args)
        code = 0
        if isinstance(result, tuple):
            result, code = result
        if getattr(args, "table", False):
            kind = args.command
            print(table(result, TABLE_COLUMNS[kind]))
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return code
    except FactoryError as error:
        print(f"{NAME}: {error.message}", file=sys.stderr)
        return error.code
    except sqlite3.OperationalError as error:
        print(f"{NAME}: database: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        if os.environ.get("FACTORY_DEBUG"):
            traceback.print_exc()
        print(f"{NAME}: internal error: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.close()
