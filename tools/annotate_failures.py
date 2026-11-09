"""Turns each failing test in unittest's output into a GitHub Actions error annotation."""
import re
import sys

text = open(sys.argv[1], encoding="utf-8", errors="replace").read()
blocks = re.split(r"\n={20,}\n", text)
found = 0
for block in blocks:
    if not block.startswith(("ERROR:", "FAIL:")):
        continue
    found += 1
    title, _, body = block.partition("\n")
    body = body.strip("-\n")[-3500:]
    msg = (title + "\n" + body).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title={title[:120]}::{msg}")
if not found:
    tail = text[-3500:].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::error title=Tests failed (no test name found)::{tail}")
