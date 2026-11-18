"""Quick check for web pages - finds lines where a // line comment is followed by code on the same line
(the comment silently swallows that code). Usage: python web_lint.py web/index.html -> line numbers; exit code 1 on hits."""
import re
import sys

hits = 0
for i, line in enumerate(open(sys.argv[1] if len(sys.argv) > 1 else "web/index.html", encoding="utf-8"), 1):
    if line.lstrip().startswith("//"):
        continue
    for m in re.finditer(r'(?<![:"\'/\\])//(?!/)', line):
        tail = line[m.end():]
        if re.search(r'https?:$', line[:m.start()]):
            continue
        if re.search(r'[;{})]\s*(const|let|var|if|for|return)\b|[;{}]\s*[\w.\[\]]+\s*=[^=>]|\w+\([^)]*\);\s*\S|\w+\([^)]*\)\s*[;})]|[;}]\s*$|\)\}\s*$', tail):
            print(i, tail.strip()[:120])
            hits += 1
print("주석 뒤 코드:", hits)
sys.exit(1 if hits else 0)
