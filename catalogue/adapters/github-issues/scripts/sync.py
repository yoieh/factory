import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
KEY_RE = re.compile(r"^[0-9]{1,9}$")
FIELDS = "number,title,state,stateReason,url,updatedAt"
BUDGET = 25


class Refused(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def gh(arguments, timeout):
    environment = dict(os.environ, GH_PROMPT_DISABLED="1")
    try:
        done = subprocess.run(["gh"] + arguments, capture_output=True, text=True, timeout=max(1, timeout),
                              env=environment, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise Refused("sync: gh is not installed")
    except subprocess.TimeoutExpired:
        raise Refused("sync: gh did not answer in time")
    if done.returncode == 4:
        raise Refused("sync: gh is not signed in", 3)
    return done


def utc(stamp):
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        moment = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def status_of(issue):
    if issue.get("state") == "CLOSED":
        reason = issue.get("stateReason")
        return f"CLOSED ({reason})" if reason else "CLOSED"
    return issue.get("state") or "UNKNOWN"


def entry(issue):
    return {
        "external_key": str(issue["number"]),
        "title": issue.get("title") or "",
        "url": issue.get("url") or "",
        "external_status": status_of(issue),
        "updated_at": utc(issue.get("updatedAt")),
    }


def parse(stdout):
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        raise Refused("sync: gh printed no JSON")


def sync(repository, keys, started):
    if not REPOSITORY_RE.match(repository):
        raise Refused("sync: --repository must be <owner>/<name>")
    for key in keys:
        if not KEY_RE.match(key):
            raise Refused("sync: every key must be an issue number")
    if not keys:
        return {"issues": []}
    left = lambda: BUDGET - (time.monotonic() - started)
    status = gh(["auth", "status", "--hostname", "github.com"], left())
    if status.returncode != 0:
        raise Refused("sync: gh is not signed in to github.com", 3)
    listed = gh(["issue", "list", "--repo", repository, "--state", "all", "--limit", "1000", "--json", FIELDS], left())
    if listed.returncode != 0:
        raise Refused(f"sync: gh issue list failed with exit {listed.returncode}")
    found = {}
    for issue in parse(listed.stdout):
        if isinstance(issue, dict) and str(issue.get("number")) in keys:
            found[str(issue["number"])] = entry(issue)
    for key in keys:
        if key in found or left() < 2:
            continue
        viewed = gh(["issue", "view", key, "--repo", repository, "--json", FIELDS], left())
        if viewed.returncode != 0:
            continue
        try:
            issue = json.loads(viewed.stdout)
        except json.JSONDecodeError:
            continue
        if isinstance(issue, dict) and str(issue.get("number")) == key:
            found[key] = entry(issue)
    return {"issues": [found[key] for key in keys if key in found]}


def main(argv=None):
    started = time.monotonic()
    parser = argparse.ArgumentParser(prog="sync.py")
    parser.add_argument("--repository", required=True)
    parser.add_argument("--keys", default="")
    args = parser.parse_args(argv)
    keys = list(dict.fromkeys(key.strip() for key in args.keys.split(",") if key.strip()))
    try:
        result = sync(args.repository, keys, started)
    except Refused as error:
        print(str(error), file=sys.stderr)
        return error.code
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
