"""🐣 Mentoring - turns on the training card of a device through this device's office server.
Usage (this device's server must be running):
  python -I -X utf8 office_mentor.py start --who <trainee> [--mentor <id>]   # leader server only · one egg
  python -I -X utf8 office_mentor.py check --who <trainee> --n 3             # mentor server only · 7 checks; the egg hatches when all are done
  python -I -X utf8 office_mentor.py qa --who <trainee> --n 2 --ok           # mentor server only · 5 questions (--no = wrong, retry next day)
  python -I -X utf8 office_mentor.py rev --who <trainee> --n 2               # trainee server only · reverse review: number of blocked spots (1~3)
Seven checks: find own device notes · shared folder read/write (md5) · start the office server · start the watcher and round-trip a message
              · one lesson line · practise the docs write gate · list the jobs that need the owner's OK
Mentor checks and graduation are not approvals (hooks, settings and installs still need the owner in that device's window)."""
import argparse
import json
import urllib.request


def _port(given):
    """Port from --port, else from config.json next to this file, else 8765."""
    if given:
        return given
    try:
        import importlib.util
        import os
        spec = importlib.util.spec_from_file_location("office_config", os.path.join(os.path.dirname(os.path.abspath(__file__)), "office_config.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m.load()["port"]
    except Exception:
        return 8765


ap = argparse.ArgumentParser()
ap.add_argument("op", choices=["start", "check", "qa", "rev"])
ap.add_argument("--who", required=True)
ap.add_argument("--mentor")
ap.add_argument("--n", type=int, default=0)
g = ap.add_mutually_exclusive_group()
g.add_argument("--ok", dest="ok", action="store_true", default=None)
g.add_argument("--no", dest="ok", action="store_false")
ap.add_argument("--port", type=int, default=0)
A = ap.parse_args()
body = {"op": A.op, "who": A.who, "n": A.n}
if A.mentor:
    body["mentor"] = A.mentor
if A.op == "qa":
    if A.ok is None:
        raise SystemExit("qa 는 --ok 나 --no 가 있어야 함")
    body["ok"] = A.ok
base = "http://127.0.0.1:%d" % _port(A.port)
req = urllib.request.Request(base + "/api/mentor", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json", "Origin": base})
try:
    r = json.loads(urllib.request.urlopen(req, timeout=5).read())
except urllib.error.HTTPError as e:
    r = json.loads(e.read() or b"{}")
except OSError as e:
    raise SystemExit("사무실 서버에 못 닿음(%s) — 이 기기 서버가 켜져 있나" % e)
print(json.dumps(r, ensure_ascii=False))
