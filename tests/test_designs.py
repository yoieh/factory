import json
import os
import unittest

from helpers import FactoryCase, read, write

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16

DESIGN = {
    "name": "acme-design",
    "slot": "design",
    "version": 1,
    "vendors": [],
    "capabilities": {"fetch_design": {"via": "mcp", "requires": {"mcp": ["mcp__*acme*__get_frame"]}}},
    "settings": {"max_frames": {"type": "int", "default": 8}},
    "patterns": {
        "design_url": "https://design\\.example\\.com/file/[A-Za-z0-9]{6,}(?:\\?[^\\s)]*)?",
        "scrub": ["https://assets\\.example\\.com/[^\\s)\"']*", "[?&]Signature=[^\\s)\"']*"],
    },
}


class DesignTest(FactoryCase):
    def setUp(self):
        super().setUp()
        write(os.path.join(self.home, "catalogue", "adapters", "acme-design", "adapter.json"), json.dumps(DESIGN))
        self.api = self.repo("api")
        self.project(repos=[self.api])
        self.cli("project", "set", "shop", "integrations.design", '{"adapter": "acme-design"}')
        self.run = self.cli("run", "new", "Build the form", "--project", "shop")
        self.design = os.path.join(self.run["run_dir"], "design")

    def test_links_from_a_run_file(self):
        issue = write(os.path.join(self.run["run_dir"], "issue.md"),
                      "See https://design.example.com/file/AbC123xyz?node=1 and (https://design.example.com/file/Zz9876qq).\n"
                      "Again https://design.example.com/file/AbC123xyz?node=1 and https://other.example.com/file/AbC123\n")
        found = self.cli("design", "links", issue)
        self.assertEqual(found["adapter"], "acme-design")
        self.assertEqual(found["links"], ["https://design.example.com/file/AbC123xyz?node=1",
                                          "https://design.example.com/file/Zz9876qq"])

    def test_links_without_a_design_adapter(self):
        self.cli("project", "add", "blog", "--key", "BLOG")
        note = write(os.path.join(self.tmp, "note.md"), "https://design.example.com/file/AbC123xyz\n")
        found = self.cli("design", "links", note, "--project", "blog")
        self.assertEqual((found["adapter"], found["links"]), (None, []))
        self.assertIn("no file", self.fails("design", "links", os.path.join(self.tmp, "missing.md"), "--project", "blog"))

    def test_target_names_are_safe_and_unique(self):
        first = self.cli("design", "target", self.run["id"], "--name", "Sign up / Step 1: Email")
        self.assertEqual(first["png"], os.path.join(self.design, "sign-up-step-1-email.png"))
        self.assertEqual(first["md"], os.path.join(self.design, "sign-up-step-1-email.md"))
        self.assertEqual(self.cli("design", "target", self.run["id"], "--name", "Sign up / Step 1: Email"), first)
        clash = self.cli("design", "target", self.run["id"], "--name", "Sign-up step 1 email")
        self.assertEqual(clash["png"], os.path.join(self.design, "sign-up-step-1-email-2.png"))
        odd = self.cli("design", "target", self.run["id"], "--name", "../../etc/passwd")
        self.assertEqual(os.path.dirname(odd["png"]), self.design)
        self.assertEqual(self.cli("design", "target", self.run["id"], "--name", "Notes")["md"],
                         os.path.join(self.design, "notes-2.md"))
        self.assertIn("--name is empty", self.fails("design", "target", self.run["id"], "--name", "  ", code=2))

    def test_check_removes_non_png_and_scrubs(self):
        good = self.cli("design", "target", self.run["id"], "--name", "Home")
        bad = self.cli("design", "target", self.run["id"], "--name", "Login")
        with open(good["png"], "wb") as handle:
            handle.write(PNG)
        write(bad["png"], "<html>expired link</html>")
        write(good["md"], "# Home\n\nSource: link\n\n![img](https://assets.example.com/a/b.png?x=1) "
                          "https://cdn.example.com/i.png?Signature=abc123&Expires=9\n")
        write(os.path.join(self.design, "notes.md"), "Asset https://assets.example.com/zzz\n")
        result = self.cli("design", "check", self.run["id"])
        self.assertEqual(result["removed"], [bad["png"]])
        self.assertFalse(os.path.exists(bad["png"]))
        self.assertEqual(sorted(entry["path"] for entry in result["scrubbed"]), sorted([good["md"], os.path.join(self.design, "notes.md")]))
        text = read(good["md"])
        self.assertNotIn("assets.example.com", text)
        self.assertNotIn("Signature", text)
        self.assertEqual(result["designs"], [
            {"name": "Home", "png": good["png"], "md": good["md"]},
            {"name": "Login", "png": None, "md": None},
        ])
        self.assertEqual(result["notes"], os.path.join(self.design, "notes.md"))

    def test_plan_sees_designs_but_not_the_index(self):
        self.cli("design", "target", self.run["id"], "--name", "Home")
        self.assertEqual(os.listdir(self.design), [".targets.json"])


if __name__ == "__main__":
    unittest.main()
