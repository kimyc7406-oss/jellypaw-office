"""Claude Code window -> office: this device's character answers a message sent from the office screen
(first line becomes the speech bubble).
Usage: python -I -X utf8 office_reply.py [--config config.json] [--device <id>] --id <message id> "답"
Do not write confidential sources, client names or paths (this goes to the shared folder)."""
import argparse
import importlib.util
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("office_config", os.path.join(HERE, "office_config.py"))
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

ap = argparse.ArgumentParser()
ap.add_argument("--config", default="")
ap.add_argument("--device", default="")
ap.add_argument("--id", required=True)
ap.add_argument("text")
A = ap.parse_args()
CFG = C.load(A.config or None)
A.device = C.pick_device(CFG, A.device)
SHARED = C.shared_dir(CFG)
if not os.path.exists(os.path.join(SHARED, "orders", A.id + ".json")):
    raise SystemExit("그런 말 id 없음: " + A.id)
p = os.path.join(SHARED, "replies", "%s_%s.json" % (A.id, A.device))
os.makedirs(os.path.dirname(p), exist_ok=True)
try:
    old = json.load(open(p, encoding="utf-8")).get("text", "")
except Exception:
    old = ""
text = (old + "\n\n" if old else "") + A.text[:4000]
json.dump({"t": time.time(), "text": text[-8000:]}, open(p + ".part", "w", encoding="utf-8"), ensure_ascii=False)
os.replace(p + ".part", p)
print("답 남김:", p)
