"""🔎 Cross review - one review line through this device's office server (this device is the reviewer).
Usage (this device's server must be running):
  python -I -X utf8 office_review.py --target <device> --card c12 --res ✓ --ev 1a2b3c4d --note "md5 대조 · 시험 다시 돌림"
  python -I -X utf8 office_review.py --target <device> --card c12 --res ✗ --ev 3/4 --note "빈 목록에서 터짐"
Turn: the device after the maker in config order (skips devices that are off) · nobody reviews their own piece.
✓ needs evidence (md5 first 8 chars or tests n/n) · a review ✓ is not an approval · notes from private devices stay on that device."""
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
ap.add_argument("--target", required=True)
ap.add_argument("--card", required=True)
ap.add_argument("--res", required=True, choices=["✓", "✗", "보류"])
ap.add_argument("--ev", default="")
ap.add_argument("--note", default="")
ap.add_argument("--port", type=int, default=0)
A = ap.parse_args()
base = "http://127.0.0.1:%d" % _port(A.port)
body = {"target": A.target, "card": A.card, "res": A.res, "ev": A.ev, "note": A.note}
req = urllib.request.Request(base + "/api/review", data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json", "Origin": base})
try:
    r = json.loads(urllib.request.urlopen(req, timeout=5).read())
except urllib.error.HTTPError as e:
    r = json.loads(e.read() or b"{}")
except OSError as e:
    raise SystemExit("사무실 서버에 못 닿음(%s) — 이 기기 서버가 켜져 있나" % e)
print(json.dumps(r, ensure_ascii=False))
