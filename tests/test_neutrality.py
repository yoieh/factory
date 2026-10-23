import json
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DENYLIST = os.path.join(ROOT, "tests", "denylist.local.txt")
ADAPTERS = os.path.join("catalogue", "adapters")
SHARED = ("examples/", "factory-spec/")


def pattern(entry):
    word = entry.strip().lower()
    if word.endswith(("-", "_")):
        return re.compile(r"(?<![a-z0-9_])" + re.escape(word), re.I)
    return re.compile(r"(?<![a-z0-9_])" + re.escape(word) + r"(?![a-z0-9_])", re.I)


def checkout_files():
    done = subprocess.run(
        ["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True, text=True,
    )
    if done.returncode != 0:
        return None
    return sorted({path for path in done.stdout.split("\0") if path and os.path.isfile(os.path.join(ROOT, path))})


def is_checkout():
    done = subprocess.run(["git", "-C", ROOT, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return done.returncode == 0 and os.path.realpath(done.stdout.strip()) == os.path.realpath(ROOT)


def lines_of(path):
    with open(os.path.join(ROOT, path), "rb") as handle:
        data = handle.read()
    if b"\0" in data:
        return []
    return data.decode("utf-8", errors="replace").splitlines()


def vendor_words():
    words = {}
    folder = os.path.join(ROOT, ADAPTERS)
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        path = os.path.join(folder, name, "adapter.json")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        for word in data.get("vendors", []):
            words.setdefault(word.lower(), set()).add(name)
    return words


def never_words(text):
    words = []
    inside = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            inside = line.lower() == "[never]"
            continue
        if inside:
            words.append(line)
    return words


def hits(files, words, allowed):
    found = []
    compiled = [(word, pattern(word)) for word in words]
    for path in files:
        entries = [(0, path)] + list(enumerate(lines_of(path), start=1))
        for word, regex in compiled:
            if allowed(word, path):
                continue
            for number, line in entries:
                if regex.search(line):
                    found.append(f"{path}:{number}: {word}")
    return found


def vendor_allowed(listing):
    def allowed(word, path):
        if path.startswith(SHARED):
            return True
        return any(path.startswith(f"{ADAPTERS}/{name}/") for name in listing[word])

    return allowed


class MatchingTest(unittest.TestCase):
    def test_whole_words_and_prefixes(self):
        self.assertTrue(pattern("cat").search("a Cat here"))
        self.assertFalse(pattern("cat").search("the catalogue"))
        self.assertTrue(pattern("cat").search("cat-food and cat.md"))
        self.assertFalse(pattern("cat").search("cat_food"))
        self.assertTrue(pattern("cat_").search("cat_food"))
        self.assertTrue(pattern("cat-").search("cat-food"))
        self.assertFalse(pattern("cat-").search("bobcat-food"))

    def test_denylist_sections(self):
        text = "# local names\n[never]\nAcme\n\nzeta-\n[other]\nkept out\n[NEVER]\nomega\n"
        self.assertEqual(never_words(text), ["Acme", "zeta-", "omega"])

    def test_vendor_rule_allows_listing_adapters_and_shared_folders(self):
        allowed = vendor_allowed({"acme": {"acme-host", "acme-tracker"}})
        self.assertTrue(allowed("acme", "catalogue/adapters/acme-tracker/adapter.md"))
        self.assertTrue(allowed("acme", "examples/project.json"))
        self.assertTrue(allowed("acme", "factory-spec/spec/07-integrations.md"))
        self.assertFalse(allowed("acme", "catalogue/adapters/other/adapter.md"))
        self.assertFalse(allowed("acme", "factory/cli.py"))


@unittest.skipUnless(is_checkout(), "not a git checkout of the core; the neutrality test needs git ls-files")
class NeutralityTest(unittest.TestCase):
    def test_vendors(self):
        listing = vendor_words()
        self.assertTrue(listing, "no adapter lists any vendors")
        found = hits(checkout_files(), sorted(listing), vendor_allowed(listing))
        self.assertEqual(found, [], "vendor names outside their adapter folders:\n" + "\n".join(found))

    def test_never(self):
        if not os.path.isfile(DENYLIST):
            self.skipTest("tests/denylist.local.txt is missing, so the [never] check is skipped")
        with open(DENYLIST, encoding="utf-8") as handle:
            words = never_words(handle.read())
        found = hits(checkout_files(), words, lambda word, path: False)
        self.assertEqual(found, [], "names that may appear nowhere:\n" + "\n".join(found))

    def test_denylist_is_ignored_by_git(self):
        done = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", "tests/denylist.local.txt"])
        self.assertEqual(done.returncode, 0, "tests/denylist.local.txt must be in .gitignore")


if __name__ == "__main__":
    unittest.main()
