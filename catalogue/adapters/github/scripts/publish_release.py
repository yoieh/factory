import argparse
import json
import os
import re
import subprocess
import sys

ADAPTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
MODES = ("draft", "publish")
TIMEOUT = 25
UPLOAD_TIMEOUT = 1500


class Refused(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def pr_url_pattern():
    with open(os.path.join(ADAPTER, "adapter.json"), encoding="utf-8") as handle:
        return json.load(handle)["patterns"]["pr_url"]


def gh(arguments, what, timeout=TIMEOUT, check=True):
    environment = dict(os.environ, GH_PROMPT_DISABLED="1")
    try:
        done = subprocess.run(["gh"] + arguments, capture_output=True, text=True, timeout=timeout,
                              env=environment, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise Refused("publish_release: gh is not installed")
    except subprocess.TimeoutExpired:
        raise Refused(f"publish_release: gh did not answer within {timeout} seconds")
    if done.returncode == 4:
        raise Refused("publish_release: gh is not signed in", 3)
    if done.returncode != 0 and check:
        raise Refused(f"publish_release: {what} failed with exit {done.returncode}")
    return done


def repository(url):
    if not re.match(pr_url_pattern(), url):
        raise Refused("publish_release: the url is not a pull request url of this adapter")
    parts = url.split("/")
    return f"{parts[3]}/{parts[4]}"


def publish(url, tag, target, path, title, notes, mode, prerelease):
    name = repository(url)
    if not TAG_RE.match(tag):
        raise Refused("publish_release: --tag must be letters, digits, dots, dashes and underscores")
    if not SHA_RE.match(target):
        raise Refused("publish_release: --target must be the full sha of the merge commit")
    if mode not in MODES:
        raise Refused("publish_release: --mode must be draft or publish")
    if not os.path.isfile(path):
        raise Refused("publish_release: the build file does not exist")
    if not os.path.isfile(notes):
        raise Refused("publish_release: the notes file does not exist")
    found = gh(["release", "view", tag, "--repo", name, "--json", "url,isDraft"], "gh release view", check=False)
    if found.returncode == 0:
        try:
            data = json.loads(found.stdout)
        except json.JSONDecodeError:
            raise Refused("publish_release: gh printed no JSON")
        return {"action": "exists", "url": data.get("url") or "", "draft": bool(data.get("isDraft")), "tag": tag}
    command = ["release", "create", tag, path, "--repo", name, "--target", target, "--title", title,
               "--notes-file", notes]
    if mode == "draft":
        command.append("--draft")
    if prerelease:
        command.append("--prerelease")
    done = gh(command, "gh release create", UPLOAD_TIMEOUT)
    lines = [line.strip() for line in done.stdout.splitlines() if line.strip()]
    return {"action": "created", "url": lines[-1] if lines else "", "draft": mode == "draft", "tag": tag}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="publish_release.py")
    parser.add_argument("--url", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--notes-file", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--prerelease", default="true")
    args = parser.parse_args(argv)
    try:
        result = publish(args.url, args.tag, args.target, args.file, args.title, args.notes_file, args.mode,
                         args.prerelease == "true")
    except Refused as error:
        print(str(error), file=sys.stderr)
        return error.code
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
