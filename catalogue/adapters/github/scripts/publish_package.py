import argparse
import base64
import datetime
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import urllib.parse

ADAPTER = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
EMPTY = "application/vnd.oci.empty.v1+json"
ARTIFACT = "application/vnd.factory.build.v1"
TIMEOUT = 25
UPLOAD_TIMEOUT = 1500
CHUNK = 1024 * 1024


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
        raise Refused("publish_package: gh is not installed")
    except subprocess.TimeoutExpired:
        raise Refused(f"publish_package: gh did not answer within {TIMEOUT} seconds")
    if done.returncode == 4 or (done.returncode != 0 and arguments[:2] == ["auth", "token"]):
        raise Refused("publish_package: gh is not signed in", 3)
    if done.returncode != 0:
        raise Refused(f"publish_package: {what} failed with exit {done.returncode}")
    return done.stdout.strip()


def repository(url):
    if not re.match(pr_url_pattern(), url):
        raise Refused("publish_package: the url is not a pull request url of this adapter")
    parts = url.split("/")
    return parts[3], parts[4]


class Registry:
    def __init__(self, base, name, timeout=TIMEOUT):
        parsed = urllib.parse.urlsplit(base)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise Refused("publish_package: the registry setting must be an https url")
        self.scheme, self.host, self.name, self.timeout = parsed.scheme, parsed.netloc, name, timeout
        self.bearer = None

    def connect(self, timeout=None):
        kind = http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection
        return kind(self.host, timeout=timeout or self.timeout)

    def call(self, method, target, headers=None, body=None, timeout=None):
        found = urllib.parse.urlsplit(target)
        if found.netloc and found.netloc != self.host:
            raise Refused("publish_package: the registry sent the upload to another host")
        path = urllib.parse.urlunsplit(("", "", found.path, found.query, ""))
        headers = dict(headers or {})
        if self.bearer:
            headers["Authorization"] = f"Bearer {self.bearer}"
        connection = self.connect(timeout)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
        except OSError as error:
            raise Refused(f"publish_package: the registry did not answer ({type(error).__name__})")
        finally:
            connection.close()
        if response.status in (401, 403):
            raise Refused("publish_package: the registry refused the token; it needs the write:packages scope", 3)
        return response.status, response.headers, data

    def login(self, user, token):
        basic = base64.b64encode(f"{user}:{token}".encode()).decode()
        query = urllib.parse.urlencode({"service": self.host, "scope": f"repository:{self.name}:pull,push"})
        status, _, data = self.call("GET", f"/token?{query}", {"Authorization": f"Basic {basic}"})
        if status != 200:
            raise Refused(f"publish_package: the registry's token endpoint answered {status}")
        try:
            self.bearer = json.loads(data)["token"]
        except (ValueError, KeyError, TypeError):
            raise Refused("publish_package: the registry's token endpoint sent no token")

    def blob(self, digest, size, body):
        status, _, _ = self.call("HEAD", f"/v2/{self.name}/blobs/{digest}")
        if status == 200:
            return
        status, headers, _ = self.call("POST", f"/v2/{self.name}/blobs/uploads/")
        location = headers.get("Location")
        if status != 202 or not location:
            raise Refused(f"publish_package: starting an upload answered {status}")
        joiner = "&" if "?" in location else "?"
        status, _, _ = self.call("PUT", f"{location}{joiner}digest={urllib.parse.quote(digest)}",
                                 {"Content-Type": "application/octet-stream", "Content-Length": str(size)},
                                 body, UPLOAD_TIMEOUT)
        if status != 201:
            raise Refused(f"publish_package: the upload answered {status}")

    def manifest(self, reference, data):
        status, _, _ = self.call("PUT", f"/v2/{self.name}/manifests/{reference}",
                                 {"Content-Type": MANIFEST, "Content-Length": str(len(data))}, data)
        if status not in (200, 201):
            raise Refused(f"publish_package: storing the manifest {reference} answered {status}")


def file_digest(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def publish(url, tag, revision, path, registry):
    owner, repo = repository(url)
    if not TAG_RE.match(tag):
        raise Refused("publish_package: --tag must be letters, digits, dots, dashes and underscores")
    if not SHA_RE.match(revision):
        raise Refused("publish_package: --revision must be the full sha of the merge commit")
    if not os.path.isfile(path):
        raise Refused("publish_package: the build file does not exist")
    token = gh(["auth", "token", "--hostname", "github.com"], "gh auth token")
    user = gh(["api", "user", "--jq", ".login"], "gh api user")
    name = f"{owner.lower()}/{repo.lower()}"
    target = Registry(registry, name)
    target.login(user, token)
    empty = b"{}"
    empty_digest = "sha256:" + hashlib.sha256(empty).hexdigest()
    target.blob(empty_digest, len(empty), empty)
    digest, size = file_digest(path), os.path.getsize(path)
    with open(path, "rb") as handle:
        target.blob(digest, size, handle)
    manifest = {
        "schemaVersion": 2,
        "mediaType": MANIFEST,
        "artifactType": ARTIFACT,
        "config": {"mediaType": EMPTY, "digest": empty_digest, "size": len(empty)},
        "layers": [{"mediaType": "application/zip" if path.endswith(".zip") else "application/octet-stream",
                    "digest": digest, "size": size,
                    "annotations": {"org.opencontainers.image.title": os.path.basename(path)}}],
        "annotations": {
            "org.opencontainers.image.source": f"https://github.com/{owner}/{repo}",
            "org.opencontainers.image.revision": revision,
            "org.opencontainers.image.created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }
    data = json.dumps(manifest, separators=(",", ":")).encode()
    for reference in (tag, "latest"):
        target.manifest(reference, data)
    return {"action": "pushed", "ref": f"{target.host}/{name}:{tag}", "digest": "sha256:" + hashlib.sha256(data).hexdigest()}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="publish_package.py")
    parser.add_argument("--url", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--registry", default="https://ghcr.io")
    args = parser.parse_args(argv)
    try:
        result = publish(args.url, args.tag, args.revision, args.file, args.registry)
    except Refused as error:
        print(str(error), file=sys.stderr)
        return error.code
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
