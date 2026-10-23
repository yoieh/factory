import os
import re

from .util import read_text

PATTERNS = {
    "DONE": re.compile(r"^STATUS:[ \t]*DONE[ \t]*$", re.I),
    "BLOCKED": re.compile(r"^STATUS:[ \t]*BLOCKED(?:[ \t]+(.*?))?[ \t]*$", re.I),
    "PASS": re.compile(r"^VERDICT:[ \t]*PASS[ \t]*$", re.I),
    "CHANGES": re.compile(r"^VERDICT:[ \t]*CHANGES[ \t]*$", re.I),
    "QUESTION": re.compile(r"^QUESTION:[ \t]*(.*?)[ \t]*$", re.I),
    "REPLY": re.compile(r"^REPLY:[ \t]*(.*?)[ \t]*$", re.I),
}

OUTPUT = ("DONE", "BLOCKED", "QUESTION")
GATE = ("PASS", "CHANGES", "QUESTION")
PLAN = ("QUESTION",)
REPLY = ("REPLY",)
REPORT = ("DONE", "BLOCKED")


def first_line(text):
    for line in text.lstrip("﻿").splitlines():
        if line.strip():
            return line.strip()
    return ""


def parse(text, allowed):
    line = first_line(text)
    for kind in allowed:
        match = PATTERNS[kind].match(line)
        if match:
            detail = match.group(1) if match.groups() else None
            return {"kind": kind, "detail": (detail or "").strip(), "line": line}
    return {"kind": "INVALID", "detail": "", "line": line}


def read(path, allowed):
    if not os.path.isfile(path):
        return {"kind": "MISSING", "detail": "", "line": "", "text": None}
    text = read_text(path)
    head = parse(text, allowed)
    head["text"] = text
    return head


def sections(text):
    found = {}
    current = None
    for line in (text or "").splitlines():
        heading = re.match(r"^##[ \t]+(.+?)[ \t]*$", line)
        if heading and not line.startswith("###"):
            current = heading.group(1).strip().lower()
            found.setdefault(current, [])
            continue
        if current is not None:
            found[current].append(line)
    return found


def items(lines):
    collected = []
    for line in lines or []:
        bullet = re.match(r"^\s*[-*][ \t]+(.*\S)\s*$", line)
        if bullet:
            collected.append(bullet.group(1))
        elif collected and line.strip() and line.startswith((" ", "\t")):
            collected[-1] = collected[-1] + " " + line.strip()
    return collected
