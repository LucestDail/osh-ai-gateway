#!/usr/bin/env python3
"""Rebuild deploy/www/index.html from shell + CSS + JS."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
css = (ROOT / "style.css").read_text()
js = (ROOT / "status.js").read_text()
shell = (ROOT / "index.shell.html").read_text()

html = shell.replace("{{CSS}}", css).replace("{{JS}}", js)
(ROOT / "index.html").write_text(html)
print("rebuilt index.html", len(html), "bytes")
