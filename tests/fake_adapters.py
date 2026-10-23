import json
import os

from helpers import write

PR_URL = "https://code.example.com/shop/api/pull/7"

STATE_SCRIPT = """import json, os, sys
args = sys.argv[1:]
log = os.environ.get("ACME_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps({"args": args, "token": os.environ.get("ACME_TOKEN"), "other": os.environ.get("ACME_OTHER")}) + "\\n")
mode = os.environ.get("ACME_MODE", "ok")
if mode == "sleep":
    import time
    time.sleep(5)
if mode == "fail":
    sys.stderr.write("first line\\nrequest failed for " + (os.environ.get("ACME_TOKEN") or "") + "\\n")
    sys.exit(1)
if mode == "garbage":
    print("not json")
    sys.exit(0)
print(os.environ.get("ACME_PR_JSON", json.dumps({"state": "OPEN", "url": args[-1]})))
"""

LOCKED_SCRIPT = """import sys
sys.stderr.write("no token\\n")
sys.exit(3)
"""

SYNC_SCRIPT = """import json, os, sys
print(os.environ["ACME_SYNC_JSON"])
"""


def host(name="acme-host", script=STATE_SCRIPT, extra_settings=None, env=None, command=None):
    return {
        "data": {
            "name": name,
            "slot": "code_host",
            "version": 1,
            "vendors": [],
            "capabilities": {
                "push": {"via": "git"},
                "pr_state": {
                    "via": "script",
                    "command": command or ["python3", "scripts/pr_state.py", "--draft", "{draft}", "--reviewers", "{reviewers}",
                                           "--project", "{project}", "--repo", "{repo}", "--url", "{pr_url}"],
                    "requires": {"env": env or [], "files": ["~/.acme.env"]} if env else {},
                },
            },
            "settings": dict({"draft": {"type": "bool", "default": True},
                              "reviewers": {"type": "list", "default": ["team-a", "team-b"]}}, **(extra_settings or {})),
            "patterns": {"pr_url": "^https://code\\.example\\.com/[a-z]+/[a-z]+/pull/[0-9]+$"},
        },
        "scripts": {"scripts/pr_state.py": script},
    }


def tracker(name="acme-tracker"):
    return {
        "data": {
            "name": name,
            "slot": "tracker",
            "version": 1,
            "vendors": [],
            "capabilities": {
                "fetch_issue": {"via": "mcp", "requires": {"mcp": ["mcp__*acme*__get_issue"]}},
                "import": {"via": "core"},
                "sync": {"via": "script", "command": ["python3", "scripts/sync.py", "--keys", "{external_keys}"]},
            },
            "settings": {},
            "patterns": {"key_pattern": "^ACME-[0-9]+$",
                         "url_pattern": "^https://tracker\\.example\\.com/(?P<key>ACME-[0-9]+)$"},
        },
        "scripts": {"scripts/sync.py": SYNC_SCRIPT},
    }


def install(home, spec):
    folder = os.path.join(home, "catalogue", "adapters", spec["data"]["name"])
    write(os.path.join(folder, "adapter.json"), json.dumps(spec["data"]))
    write(os.path.join(folder, "adapter.md"), "# adapter\n\n## Setup\n\n## Never\n")
    for relative, text in spec["scripts"].items():
        write(os.path.join(folder, relative), text)
    return folder
