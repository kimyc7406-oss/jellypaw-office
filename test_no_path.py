"""no_path test: every path shape in a message that goes to the shared folder becomes "(경로)",
and none of the patrol path patterns (office_patrol) still match afterwards.
Reads only the PATH_RX line of office_server.py (does not start the server). Usage: python -I -X utf8 test_no_path.py"""
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("office_patrol", os.path.join(HERE, "office_patrol.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

src = open(os.path.join(HERE, "office_server.py"), encoding="utf-8").read()
ns = {"re": re}
exec(next(l for l in src.splitlines() if l.startswith("PATH_RX")), ns)
no_path = lambda s: ns["PATH_RX"].sub("(경로)", s)

BS = chr(92)
DRV = "D" + ":"
USERS = "/" + "Users"
MUST = [
    "파일 " + DRV + BS + "Projects" + BS + "x.py 봐",
    DRV + "/Share/ai_office/meetings 확인",
    "`" + "C:" + USERS + "/alice/a.json`",
    BS * 2 + "NAS" + BS + "share" + BS + "a.txt 봐",
    USERS + "/alice/Desktop/a.png ok",
    "/Volumes/Work/proj 열어",
    "/home/alice/x 봐",
    "옮김: ~/.claude/hooks/state/lessons 봐",
    "~" + BS + "AppData" + BS + "x.json",
    "%USERPROFILE%" + BS + ".claude" + BS + "a.md",
]
KEEP = [
    "rate 3:2 비율",
    "md5 117dea4d · 13:28 올림",
    "http://127.0.0.1:8765/ 열어",
    "💬 그냥 수다 한 줄",
    "약 3~4초 · 10~20%",
    "a~/b 아님",
    "C4D:/ 메뉴",
    "github.com/home/x 봐",
]
EXACT = [
    ("옮김: 「~/.claude/x」 봐", "옮김: 「(경로)」 봐"),
    ("(~/x) 봐", "((경로)) 봐"),
    (DRV + "/Projects/a.py.", "(경로)."),
    ("『" + "C:" + USERS + "/alice/a』", "『(경로)』"),
]
bad = 0
for t in MUST:
    o = no_path(t)
    left = [n for n, rx in P.PATTERNS[:3] if rx.search(o)]
    ok = "(경로)" in o and not left
    bad += not ok
    print("바뀜" if ok else "안 바뀜!", repr(o), left or "")
for t in KEEP:
    o = no_path(t)
    ok = o == t
    bad += not ok
    print("그대로" if ok else "바뀜!", repr(o))
for t, want in EXACT:
    o = no_path(t)
    ok = o == want
    bad += not ok
    print("맞음" if ok else "다름!", repr(o), "" if ok else "← " + repr(want))
print("시험 %d/%d" % (len(MUST) + len(KEEP) + len(EXACT) - bad, len(MUST) + len(KEEP) + len(EXACT)))
sys.exit(1 if bad else 0)
