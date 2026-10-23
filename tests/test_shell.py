import glob
import os
import re
import shutil
import subprocess
import unittest

from helpers import ROOT, read

PLACEHOLDER = re.compile(r"<[A-Za-z][^<>\n]*>")


def sources():
    found = [os.path.join(ROOT, "skills", "factory", "SKILL.md")]
    found += sorted(glob.glob(os.path.join(ROOT, "agents", "*.md")))
    found += sorted(glob.glob(os.path.join(ROOT, "catalogue", "adapters", "*", "adapter.md")))
    return found


def sh_blocks(path):
    blocks = []
    lines = read(path).splitlines()
    index = 0
    while index < len(lines):
        opening = re.match(r"^(\s*)```sh\s*$", lines[index])
        if not opening:
            index += 1
            continue
        indent = len(opening.group(1))
        start = index + 1
        body = []
        index += 1
        while index < len(lines) and lines[index].strip() != "```":
            body.append(lines[index][indent:] if lines[index][:indent].strip() == "" else lines[index])
            index += 1
        blocks.append((start, "\n".join(body) + "\n"))
        index += 1
    return blocks


def plain(script):
    return PLACEHOLDER.sub("word", script)


def check(shell, script):
    return subprocess.run([shell, "-n"], input=script, capture_output=True, text=True)


class ShellSyntaxTest(unittest.TestCase):
    def run_with(self, shell):
        failures, count = [], 0
        for path in sources():
            for line, script in sh_blocks(path):
                count += 1
                done = check(shell, plain(script))
                if done.returncode != 0:
                    failures.append(f"{os.path.relpath(path, ROOT)}:{line}: {done.stderr.strip()}")
        self.assertGreater(count, 20)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_sh(self):
        self.run_with("sh")

    def test_bash(self):
        if not shutil.which("bash"):
            self.skipTest("bash is not installed")
        self.run_with("bash")

    def test_zsh(self):
        if not shutil.which("zsh"):
            self.skipTest("zsh is not installed, so the zsh check is skipped")
        self.run_with("zsh")

    def test_placeholders_become_plain_words(self):
        self.assertEqual(plain('git -C "<path>" fetch <remote name> 2>/dev/null <<\'EOF\''),
                         'git -C "word" fetch word 2>/dev/null <<\'EOF\'')

    def test_a_broken_block_is_caught(self):
        self.assertNotEqual(check("sh", plain('if [ "<x>" ]; then echo\n')).returncode, 0)


if __name__ == "__main__":
    unittest.main()
