import http.client
import json
import os
import threading
import unittest

from helpers import FactoryCase, read, write

from factory import server

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


class ServerCase(FactoryCase):
    def setUp(self):
        super().setUp()
        self.seeded = self.cli("demo")
        self.runs = self.seeded["runs"]
        self.server = server.Server(0, quiet=True)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def request(self, method, path, body=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sent = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            sent["Content-Type"] = "application/json"
        sent.update(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=data, headers=sent)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        kind = response.getheader("Content-Type") or ""
        return response.status, (json.loads(payload) if kind.startswith("application/json") else payload), response

    def get(self, path, status=200):
        code, data, _ = self.request("GET", path)
        self.assertEqual(code, status, data)
        return data

    def post(self, path, body, status=200, headers=None):
        code, data, _ = self.request("POST", path, body, headers)
        self.assertEqual(code, status, data)
        return data


class ReadRoutesTest(ServerCase):
    def test_page_and_headers(self):
        code, page, response = self.request("GET", "/")
        self.assertEqual(code, 200)
        self.assertIn(b"<title>factory</title>", page)
        self.assertIn("default-src 'none'", response.getheader("Content-Security-Policy"))
        self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
        self.assertNotRegex(page, rb"(src|href)\s*=\s*[\"']?(https?:)?//")
        self.assertNotIn(b"<link", page)
        self.assertNotIn(b"@import", page)

    def test_runs_jobs_and_questions(self):
        runs = self.get("/api/runs")
        self.assertEqual(sorted(run["id"] for run in runs), sorted(self.runs))
        self.assertEqual(self.get("/api/runs?project=nope"), [])
        self.assertEqual(self.get("/api/runs?archived=1"), [])
        self.assertEqual(len(self.get("/api/runs?archived=all")), 3)
        detail = self.get(f"/api/runs/{self.runs[0]}")
        for key in ("jobs", "questions", "events", "skips", "repos", "usage", "files", "steps", "history"):
            self.assertIn(key, detail)
        self.assertEqual(detail["usage"]["prices"]["source"], "built-in")
        self.assertIn("review-security", detail["steps"])
        self.assertTrue(detail["steps"]["build"]["focus"])
        self.assertTrue(any(file["name"] == "plan.md" for file in detail["files"]))
        self.get("/api/runs/nope", status=404)
        jobs = self.get("/api/jobs")
        self.assertEqual(sorted(job["id"] for job in jobs), ["cart-login", "lead-endpoint", "lead-form"])
        questions = self.get("/api/questions")
        kinds = sorted((question["kind"], question["status"]) for question in questions)
        self.assertEqual(kinds, [("plan_confirm", "open"), ("question", "open")])
        confirm = next(question for question in questions if question["kind"] == "plan_confirm")
        self.assertEqual(confirm["jobs"][0]["id"], "cart-login")
        asked = next(question for question in questions if question["kind"] == "question")
        self.assertEqual(asked["job"]["id"], "lead-form")
        self.assertEqual(self.get("/api/questions?status=resolved"), [])
        confirming = self.get(f"/api/runs/{confirm['run_id']}")
        self.assertEqual([question["kind"] for question in confirming["questions"]], ["plan_confirm"])

    def test_issues_projects_catalogue_improvements_meta(self):
        issues = self.get("/api/issues?project=demo")
        self.assertEqual(len(issues), 5)
        self.assertEqual([issue["key"] for issue in self.get("/api/issues?type=bug")], ["DEMO-3"])
        self.assertEqual([issue["key"] for issue in self.get("/api/issues?q=csv")], ["DEMO-2"])
        self.assertEqual(len(self.get("/api/issues?status=in_progress,done")), 3)
        self.assertEqual(self.get("/api/issues?label=web")[0]["key"], "DEMO-1")
        issue = self.get("/api/issues/DEMO-1")
        self.assertIn("Visitors leave their name", issue["body"])
        self.get("/api/issues/DEMO-99", status=404)
        self.assertEqual(self.get("/api/projects")[0]["name"], "demo")
        project = self.get("/api/projects/demo")
        self.assertEqual([repo["name"] for repo in project["repos"]], ["api", "web"])
        self.assertIn("catalogue", project)
        self.assertIn("installs", project)
        self.get("/api/projects/nope", status=404)
        self.assertIn("build", self.get("/api/catalogue")["steps"])
        self.assertIn("build", self.get("/api/catalogue?project=demo")["steps"])
        self.assertEqual(len(self.get("/api/improvements")), 3)
        self.assertEqual(len(self.get("/api/improvements?status=open&project=demo")), 2)
        meta = self.get("/api/meta")
        self.assertEqual(meta["prices"]["source"], "built-in")
        self.get("/api/nothing", status=404)


class WriteRoutesTest(ServerCase):
    def question(self, kind):
        return next(item for item in self.get("/api/questions") if item["kind"] == kind)

    def test_answer_and_clarify(self):
        asked = self.question("question")
        clarified = self.post(f"/api/questions/{asked['id']}/clarify", {"text": "Who reads the phone number?"})
        self.assertEqual(clarified["status"], "clarify")
        self.assertIsNone(clarified["thread"][-1]["delivered_at"])
        confirm = self.question("plan_confirm")
        answered = self.post(f"/api/questions/{confirm['id']}/answer", {"text": "yes"})
        self.assertEqual((answered["status"], answered["answer"]), ("answered", "yes"))
        waited = self.cli("question", "wait", self.runs[1], "--timeout", "0")
        self.assertEqual(waited[0]["text"], "yes")
        self.post(f"/api/questions/{confirm['id']}/answer", {"text": "again"}, status=400)
        self.post("/api/questions/999/answer", {"text": "x"}, status=404)
        self.post(f"/api/questions/{asked['id']}/answer", {"text": ""}, status=400)

    def test_an_agent_answers_over_http(self):
        asked = self.question("question")
        answered = self.post(f"/api/questions/{asked['id']}/answer", {"text": "A", "by": "assistant"})
        self.assertEqual(answered["thread"][-1]["author"], "assistant")
        agents = self.get("/api/agents")
        self.assertEqual([(agent["name"], agent["answers"]) for agent in agents], [("assistant", 1)])
        shown = self.get("/api/agents/assistant")
        self.assertEqual(shown["recent"][0]["question_id"], asked["id"])
        self.get("/api/agents/nobody", status=404)
        confirm = self.question("plan_confirm")
        self.post(f"/api/questions/{confirm['id']}/answer", {"text": "yes", "by": "two words"}, status=400)
        self.post(f"/api/questions/{confirm['id']}/answer", {"text": "yes", "by": 3}, status=400)

    def test_inbox_and_events(self):
        found = self.get("/api/inbox?as=assistant")
        kinds = {item["kind"] for item in found["items"] if item["type"] == "question"}
        self.assertIn("plan_confirm", kinds)
        improvement = next(item for item in found["items"] if item["type"] == "improvement")
        self.assertEqual(improvement["decide"][-2:], ["--by", "assistant"])
        self.assertIsNotNone(self.get("/api/agents/assistant")["seen_at"])
        cursor = found["cursor"]
        self.assertEqual(self.get(f"/api/events?since={cursor}")["events"], [])
        confirm = next(item for item in found["items"] if item.get("kind") == "plan_confirm")
        self.post(f"/api/questions/{confirm['id']}/answer", {"text": "yes"})
        events = self.get(f"/api/events?since={cursor}&kind=question&wait=1&timeout=5")["events"]
        self.assertEqual([event["kind"] for event in events], ["question.status"])
        self.get("/api/events?since=x", status=404)

    def test_decide_and_archive(self):
        items = self.get("/api/improvements?status=open")
        decided = self.post(f"/api/improvements/{items[0]['id']}/decide", {"status": "accepted", "note": "good"})
        self.assertEqual((decided["status"], decided["note"], decided["decided_by"]), ("accepted", "good", "dashboard"))
        by_agent = self.post(f"/api/improvements/{items[1]['id']}/decide", {"status": "rejected", "note": "no", "by": "bertil"})
        self.assertEqual(by_agent["decided_by"], "bertil")
        self.post(f"/api/improvements/{items[1]['id']}/decide", {"status": "open", "by": 7}, status=400)
        self.assertTrue(decided["issue_key"].startswith("DEMO-"))
        self.post(f"/api/improvements/{items[0]['id']}/decide", {"status": "maybe"}, status=400)
        self.post(f"/api/improvements/{items[0]['id']}/decide", {"note": "x"}, status=400)
        done_run = self.runs[2]
        self.assertTrue(self.post(f"/api/runs/{done_run}/archive", {"archived": True})["archived"])
        self.assertEqual(self.get("/api/runs?archived=1")[0]["id"], done_run)
        self.assertFalse(self.post(f"/api/runs/{done_run}/archive", {"archived": False})["archived"])
        self.post(f"/api/runs/{self.runs[0]}/archive", {"archived": True}, status=400)
        self.post(f"/api/runs/{done_run}/archive", {"archived": "yes"}, status=400)

    def test_issue_create_and_update(self):
        created = self.post("/api/issues", {"project": "demo", "title": "From the dashboard", "type": "bug",
                                            "priority": "high", "labels": ["ui"], "body": "Steps."})
        self.assertEqual((created["key"], created["type"], created["labels"], created["body"]), ("DEMO-6", "bug", ["ui"], "Steps."))
        key = created["key"]
        updated = self.post(f"/api/issues/{key}", {"status": "ready", "priority": "low", "title": "Renamed",
                                                   "labels": ["ui", "cart"], "body": "New body"})
        self.assertEqual((updated["status"], updated["priority"], updated["title"], updated["labels"], updated["body"]),
                         ("ready", "low", "Renamed", ["cart", "ui"], "New body"))
        self.assertEqual(self.post(f"/api/issues/{key}", {"labels": []})["labels"], [])
        self.post(f"/api/issues/{key}", {"colour": "red"}, status=400)
        self.post(f"/api/issues/{key}", {"status": "someday"}, status=400)
        self.post("/api/issues", {"project": "nope", "title": "x"}, status=404)
        self.post("/api/issues", {"title": "x"}, status=400)


class GuardsTest(ServerCase):
    def test_host_check(self):
        for host in ("evil.example.com", f"127.0.0.1:{self.port + 1}", "127.0.0.1", f"example.com:{self.port}"):
            code, data, _ = self.request("GET", "/api/runs", headers={"Host": host})
            self.assertEqual(code, 403, host)
        code, _, _ = self.request("GET", "/api/runs", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(code, 200)

    def test_extra_hosts(self):
        self.server.hosts = frozenset({"box.tailnet.ts.net"})
        for host in ("box.tailnet.ts.net", "box.tailnet.ts.net:8765", "box.tailnet.ts.net:443"):
            code, _, _ = self.request("GET", "/api/runs", headers={"Host": host})
            self.assertEqual(code, 200, host)
        for host in ("evil.box.tailnet.ts.net", "box.tailnet.ts.net.evil.com", "box.tailnet.ts.net:x", "tailnet.ts.net"):
            code, _, _ = self.request("GET", "/api/runs", headers={"Host": host})
            self.assertEqual(code, 403, host)
        asked = next(item for item in self.get("/api/questions") if item["kind"] == "question")
        path = f"/api/questions/{asked['id']}/clarify"
        for origin in ("https://evil.example.com", "ftp://box.tailnet.ts.net", "https://box.tailnet.ts.net.evil.com"):
            self.post(path, {"text": "x"}, status=403, headers={"Origin": origin})
        self.post(path, {"text": "ok"}, headers={"Host": "box.tailnet.ts.net:8765", "Origin": "https://box.tailnet.ts.net:8765"})

    def test_origin_and_json_only_posts(self):
        asked = next(item for item in self.get("/api/questions") if item["kind"] == "question")
        path = f"/api/questions/{asked['id']}/clarify"
        self.post(path, {"text": "x"}, status=403, headers={"Origin": "http://evil.example.com"})
        self.post(path, {"text": "x"}, status=403, headers={"Origin": "null"})
        code, data, _ = self.request("POST", path, raw=b"text=x", headers={"Content-Type": "application/x-www-form-urlencoded"})
        self.assertEqual(code, 415)
        code, data, _ = self.request("POST", path, raw=b"{not json", headers={"Content-Type": "application/json"})
        self.assertEqual(code, 400)
        code, data, _ = self.request("POST", path, raw=b"[1]", headers={"Content-Type": "application/json"})
        self.assertEqual(code, 400)
        self.post(path, {"text": "ok"}, headers={"Origin": f"http://localhost:{self.port}"})
        code, _, _ = self.request("PUT", path, body={"text": "x"})
        self.assertEqual(code, 405)

    def test_text_limits(self):
        asked = next(item for item in self.get("/api/questions") if item["kind"] == "question")
        self.post(f"/api/questions/{asked['id']}/answer", {"text": "x" * 4001}, status=413)
        self.post(f"/api/questions/{asked['id']}/answer", {"text": 5}, status=400)
        items = self.get("/api/improvements?status=open")
        self.post(f"/api/improvements/{items[0]['id']}/decide", {"status": "rejected", "note": "n" * 4001}, status=413)
        self.post("/api/issues/DEMO-1", {"body": "b" * 100_001}, status=413)
        self.post("/api/issues/DEMO-1", {"body": "b" * 100_000})
        self.post("/api/issues", {"project": "demo", "title": "t" * 201}, status=413)

    def test_file_guard(self):
        run_dir = os.path.join(self.home, "runs", self.runs[0])
        plan = os.path.join(run_dir, "plan.md")
        self.assertIn("# Plan", self.get(f"/api/file?path={plan}")["text"])
        relative = f"runs/{self.runs[0]}/plan.md"
        self.assertIn("# Plan", self.get(f"/api/file?path={relative}")["text"])
        self.assertIn("Visitors", self.get("/api/file?path=issues/DEMO-1.md")["text"])
        self.get(f"/api/file?path=runs/{self.runs[0]}/../../config.json", status=403)
        self.get(f"/api/file?path={os.path.join(self.home, 'config.json')}", status=403)
        secret = write(os.path.join(self.tmp, "outside.md"), "secret\n")
        os.symlink(secret, os.path.join(run_dir, "link.md"))
        self.get(f"/api/file?path={os.path.join(run_dir, 'link.md')}", status=403)
        write(os.path.join(run_dir, "notes.txt"), "x")
        self.get(f"/api/file?path={os.path.join(run_dir, 'notes.txt')}", status=403)
        self.get(f"/api/file?path={os.path.join(run_dir, 'missing.md')}", status=404)
        self.get("/api/file", status=400)

    def test_png_guard(self):
        design = os.path.join(self.home, "runs", self.runs[0], "design")
        good = os.path.join(design, "home.png")
        with open(good, "wb") as handle:
            handle.write(PNG)
        code, data, response = self.request("GET", f"/api/image?path={good}")
        self.assertEqual((code, response.getheader("Content-Type"), data), (200, "image/png", PNG))
        write(os.path.join(design, "fake.png"), "<html>not an image</html>")
        self.get(f"/api/image?path={os.path.join(design, 'fake.png')}", status=415)
        self.get(f"/api/image?path={os.path.join(self.home, 'issues', 'DEMO-1.md')}", status=403)
        outside = os.path.join(self.tmp, "outside.png")
        with open(outside, "wb") as handle:
            handle.write(PNG)
        self.get(f"/api/image?path={outside}", status=403)


class RedactionTest(ServerCase):
    def test_secret_settings_and_outside_paths(self):
        folder = os.path.join(self.home, "catalogue", "adapters", "acme-host")
        write(os.path.join(folder, "adapter.json"), json.dumps({
            "name": "acme-host", "slot": "code_host", "version": 1, "vendors": [],
            "capabilities": {"push": {"via": "git"}},
            "settings": {"workspace": {"type": "string", "default": None, "secret": True},
                         "draft": {"type": "bool", "default": True}},
            "patterns": {},
        }))
        self.cli("project", "set", "demo", "integrations.code_host", '{"adapter": "acme-host", "workspace": "hidden-team"}')
        outside = write(os.path.join(self.user, "private", "notes.md"), "private\n")
        inside = write(os.path.join(self.seeded["repos"][0], "docs", "overview.md"), "docs\n")
        self.cli("project", "set", "demo", "knowledge", json.dumps({"read_first": [outside, inside]}))
        shown = self.get("/api/projects/demo")
        settings = shown["integrations"]["code_host"]["settings"]
        self.assertEqual(settings, {"workspace": "***", "draft": True})
        self.assertEqual(shown["knowledge"]["read_first"], [inside])
        text = json.dumps(shown)
        self.assertNotIn("hidden-team", text)
        self.assertNotIn(outside, text)
        self.assertIn("hidden-team", read(os.path.join(self.home, "projects", "demo", "project.json")))


class PollerTest(ServerCase):
    def test_poll_once_reads_pr_states(self):
        result = server.poll_once()
        self.assertEqual(result, {"prs": [], "issues_done": []})


class DemoTest(FactoryCase):
    def test_demo_seeds_once(self):
        seeded = self.cli("demo")
        self.assertEqual(len(seeded["runs"]), 3)
        statuses = sorted(run["status"] for run in self.cli("run", "list"))
        self.assertEqual(statuses, ["confirm", "done", "running"])
        self.assertIn("exists already", self.fails("demo"))


if __name__ == "__main__":
    unittest.main()
