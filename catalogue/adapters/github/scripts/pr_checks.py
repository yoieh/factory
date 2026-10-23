import argparse
import json
import os
import re
import subprocess
import sys

ADAPTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATES = {"OPEN": "OPEN", "MERGED": "MERGED", "CLOSED": "DECLINED"}
MERGEABLE = ("MERGEABLE", "CONFLICTING", "UNKNOWN")
GOOD = ("SUCCESS", "NEUTRAL", "SKIPPED")
WAITING = ("PENDING", "EXPECTED", "QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED")
FIELDS = "state,url,isDraft,headRefOid,mergeable,reviewDecision,statusCheckRollup"
TIMEOUT = 25


class Refused(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def pr_url_pattern():
    with open(os.path.join(ADAPTER, "adapter.json"), encoding="utf-8") as handle:
        return json.load(handle)["patterns"]["pr_url"]


def gh(arguments):
    environment = dict(os.environ, GH_PROMPT_DISABLED="1")
    try:
        done = subprocess.run(["gh"] + arguments, capture_output=True, text=True, timeout=TIMEOUT,
                              env=environment, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise Refused("pr_checks: gh is not installed")
    except subprocess.TimeoutExpired:
        raise Refused(f"pr_checks: gh did not answer within {TIMEOUT} seconds")
    if done.returncode == 4:
        raise Refused("pr_checks: gh is not signed in", 3)
    if done.returncode != 0:
        raise Refused(f"pr_checks: gh pr view failed with exit {done.returncode}")
    return done.stdout


def check_of(entry):
    name = entry.get("name") or entry.get("context") or "unnamed check"
    if entry.get("__typename") == "StatusContext":
        state = entry.get("state") or ""
        if state == "SUCCESS":
            return name, "passing"
        return name, "pending" if state in WAITING else "failing"
    if (entry.get("status") or "COMPLETED") != "COMPLETED":
        return name, "pending"
    return name, "passing" if (entry.get("conclusion") or "") in GOOD else "failing"


def checks_of(url):
    if not re.match(pr_url_pattern(), url):
        raise Refused("pr_checks: the url is not a pull request url of this adapter")
    try:
        data = json.loads(gh(["pr", "view", url, "--json", FIELDS]))
    except json.JSONDecodeError:
        raise Refused("pr_checks: gh printed no JSON")
    if not isinstance(data, dict) or STATES.get(data.get("state")) is None:
        raise Refused("pr_checks: gh reported an unknown state")
    failing, pending, passing = [], [], []
    for entry in data.get("statusCheckRollup") or []:
        name, result = check_of(entry)
        {"failing": failing, "pending": pending, "passing": passing}[result].append(name)
    overall = "failing" if failing else "pending" if pending else "passing" if passing else "none"
    mergeable = data.get("mergeable") if data.get("mergeable") in MERGEABLE else "UNKNOWN"
    return {
        "state": STATES[data["state"]],
        "url": data.get("url") or url,
        "draft": bool(data.get("isDraft")),
        "head_sha": data.get("headRefOid") or "",
        "mergeable": mergeable,
        "review": data.get("reviewDecision") or "",
        "checks": overall,
        "failing": failing,
        "pending": pending,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pr_checks.py")
    parser.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    try:
        result = checks_of(args.url)
    except Refused as error:
        print(str(error), file=sys.stderr)
        return error.code
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
