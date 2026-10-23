import argparse
import json
import os
import re
import subprocess
import sys

ADAPTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METHODS = ("squash", "merge", "rebase")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TIMEOUT = 25


class Refused(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def pr_url_pattern():
    with open(os.path.join(ADAPTER, "adapter.json"), encoding="utf-8") as handle:
        return json.load(handle)["patterns"]["pr_url"]


def gh(arguments, what):
    environment = dict(os.environ, GH_PROMPT_DISABLED="1")
    try:
        done = subprocess.run(["gh"] + arguments, capture_output=True, text=True, timeout=TIMEOUT,
                              env=environment, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise Refused("land_pr: gh is not installed")
    except subprocess.TimeoutExpired:
        raise Refused(f"land_pr: gh did not answer within {TIMEOUT} seconds")
    if done.returncode == 4:
        raise Refused("land_pr: gh is not signed in", 3)
    if done.returncode != 0:
        raise Refused(f"land_pr: {what} failed with exit {done.returncode}")
    return done.stdout


def land(url, action, head, method):
    if not re.match(pr_url_pattern(), url):
        raise Refused("land_pr: the url is not a pull request url of this adapter")
    if action not in ("ready", "merge"):
        raise Refused("land_pr: --action must be ready or merge")
    if not SHA_RE.match(head or ""):
        raise Refused("land_pr: --head must be the full commit sha the review saw")
    if method not in METHODS:
        raise Refused(f"land_pr: the merge_method setting must be one of {', '.join(METHODS)}")
    try:
        data = json.loads(gh(["pr", "view", url, "--json", "state,isDraft,headRefOid,url"], "gh pr view"))
    except json.JSONDecodeError:
        raise Refused("land_pr: gh printed no JSON")
    if not isinstance(data, dict) or data.get("state") != "OPEN":
        raise Refused("land_pr: the PR is not open")
    if data.get("headRefOid") != head:
        raise Refused("land_pr: the PR has commits the review did not see")
    if data.get("isDraft"):
        gh(["pr", "ready", url], "gh pr ready")
    if action == "ready":
        return {"action": "ready", "url": data.get("url") or url}
    gh(["pr", "merge", url, f"--{method}", "--match-head-commit", head], "gh pr merge")
    return {"action": "merged", "url": data.get("url") or url}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="land_pr.py")
    parser.add_argument("--url", required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--method", default="squash")
    args = parser.parse_args(argv)
    try:
        result = land(args.url, args.action, args.head, args.method)
    except Refused as error:
        print(str(error), file=sys.stderr)
        return error.code
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
