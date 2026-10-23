import json
import unittest

from helpers import FactoryCase

from factory import VERSION


class CliTest(FactoryCase):
    def test_json_on_success_and_json_flag_is_a_no_op(self):
        code, out, err = self.call("config", "show", "--json")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(json.loads(out)["parallel"], 3)

    def test_errors_exit_1_with_a_plain_message(self):
        code, out, err = self.call("run", "show", "nope")
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err, "factory: no run nope\n")

    def test_argument_errors_exit_2(self):
        code, out, err = self.call("run", "new")
        self.assertEqual(code, 2)
        self.assertTrue(err.startswith("factory: "))
        self.assertEqual(self.call("nothing")[0], 2)
        self.assertEqual(self.call("job", "rework", "a/b")[0], 2)

    def test_help_and_version(self):
        code, out, _ = self.call("--help")
        self.assertEqual(code, 0)
        self.assertIn("run", out)
        code, out, _ = self.call("--version")
        self.assertEqual((code, out.strip()), (0, f"factory {VERSION}"))
        self.assertEqual(self.call("run", "new", "--help")[0], 0)

    def test_tables(self):
        self.project()
        table = self.cli("project", "list", "--table")
        self.assertEqual(table.splitlines()[0].split(), ["name", "key", "repos", "archived", "description"])
        self.assertIn("shop", table)


if __name__ == "__main__":
    unittest.main()
