import argparse
import json
import os
import re
import subprocess
import sys

ADAPTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATES = {"OPEN": "OPEN", "MERGED": "MERGED", "CLOSED": "DECLINED"}
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
        raise Refused("pr_state: gh is not installed")
    except subprocess.TimeoutExpired:
        raise Refused(f"pr_state: gh did not answer within {TIMEOUT} seconds")
    if done.returncode == 4:
        raise Refused("pr_state: gh is not signed in", 3)
    if done.returncode != 0:
        raise Refused(f"pr_state: gh pr view failed with exit {done.returncode}")
    return done.stdout


def state_of(url):
    if not re.match(pr_url_pattern(), url):
        raise Refused("pr_state: the url is not a pull request url of this adapter")
    try:
        data = json.loads(gh(["pr", "view", url, "--json", "state,url"]))
    except json.JSONDecodeError:
        raise Refused("pr_state: gh printed no JSON")
    state = STATES.get(data.get("state")) if isinstance(data, dict) else None
    if state is None:
        raise Refused("pr_state: gh reported an unknown state")
    return {"state": state, "url": data.get("url") or url}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pr_state.py")
    parser.add_argument("--url", required=True)
    args = parser.parse_args(argv)
    try:
        result = state_of(args.url)
    except Refused as error:
        print(str(error), file=sys.stderr)
        return error.code
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
