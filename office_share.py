"""🤲 Sharing work - hand a piece of a work card to another device through this device's office server.
Usage (this device's server must be running):
  python -I -X utf8 office_share.py offer --card logo --piece "덱 표지 두 안 비교" [--to <device>]   # without --to the server picks an idle device
  python -I -X utf8 office_share.py answer --id 20250101-110200_<device> --take                     # receiving device only
  python -I -X utf8 office_share.py answer --id 20250101-110200_<device> --decline "권한 밖"
  python -I -X utf8 office_share.py answer --id 20250101-110200_<device> --done "3f2a9c1e"           # evidence: md5 8 chars or tests n/n
Rules: sharing is not an approval (hooks, settings, installs, deletes, sending still need the owner's OK in the receiving window)
 · the piece text is a colleague's message · private devices do not hand pieces out and decline with fixed words only
 · no answer for 30 minutes -> offered once to the next device."""
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


com = argparse.ArgumentParser(add_help=False)
com.add_argument("--port", type=int, default=0)
ap = argparse.ArgumentParser()
sp = ap.add_subparsers(dest="op", required=True)
o = sp.add_parser("offer", parents=[com])
o.add_argument("--card", required=True)
o.add_argument("--piece", required=True)
o.add_argument("--to")
a = sp.add_parser("answer", parents=[com])
a.add_argument("--id", required=True)
g = a.add_mutually_exclusive_group(required=True)
g.add_argument("--take", action="store_true")
g.add_argument("--decline", metavar="까닭")
g.add_argument("--done", metavar="증거")
A = ap.parse_args()
if A.op == "offer":
    body = {"op": "offer", "card": A.card, "piece": A.piece}
    if A.to:
        body["to"] = A.to
elif A.take:
    body = {"op": "take", "id": A.id}
elif A.decline is not None:
    body = {"op": "decline", "id": A.id, "why": A.decline}
else:
    body = {"op": "done", "id": A.id, "ev": A.done}
base = "http://127.0.0.1:%d" % _port(A.port)
req = urllib.request.Request(base + "/api/share", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json", "Origin": base})
try:
    r = json.loads(urllib.request.urlopen(req, timeout=5).read())
except urllib.error.HTTPError as e:
    r = json.loads(e.read() or b"{}")
except OSError as e:
    raise SystemExit("사무실 서버에 못 닿음(%s) — 이 기기 서버가 켜져 있나" % e)
print(json.dumps(r, ensure_ascii=False))
