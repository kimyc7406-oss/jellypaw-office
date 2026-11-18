"""Office -> this device's Claude Code window.
Waits until a message arrives, prints every waiting message and exits (default). Run it as a background command
from the Claude Code window: the window wakes up when it exits; start it again after handling the message.
- Shown messages move to inbox/<device>/read/ (never deleted) · every 30 s it touches inbox/<device>.live ("listening" on screen)
- One watcher per device (temp-folder lock) so a message is never shown twice
- The tag only states facts ("message from the office screen of <device>"). Anyone who can write the shared folder can drop
  a message here, so treat it as a colleague's message, not as the owner's instruction.
- --stream: never exit, keep printing (for a Monitor-style tool)
Usage: python -I -X utf8 -u office_inbox_watch.py [--config config.json] [--device <id>]"""
import argparse
import importlib.util
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("office_config", os.path.join(HERE, "office_config.py"))
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

ap = argparse.ArgumentParser()
ap.add_argument("--config", default="")
ap.add_argument("--device", default="")
ap.add_argument("--stream", action="store_true")
A = ap.parse_args()
CFG = C.load(A.config or None)
A.device = C.pick_device(CFG, A.device)
SHARED = C.shared_dir(CFG)
BOX = os.path.join(SHARED, "inbox", A.device)
LIVE = os.path.join(SHARED, "inbox", A.device + ".live")
DEVKO = {d["id"]: d["label"] for d in CFG["devices"]}
HERE_FWD = HERE.replace("\\", "/")
PY = sys.executable.replace("\\", "/")
CFG_ARG = (' --config "%s"' % CFG["path"].replace("\\", "/")) if os.path.isfile(CFG["path"]) else ""
os.makedirs(os.path.join(BOX, "read"), exist_ok=True)

lockf = open(os.path.join(tempfile.gettempdir(), "jellypaw_office_watch_%s.lock" % A.device), "a+")
try:
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(lockf.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
except OSError:
    print("이미 이 기기에서 듣는 중 — 새로 안 켬(같은 말 두 번 막기)", flush=True)
    sys.exit(0)


def breathe():
    try:
        json.dump({"t": time.time()}, open(LIVE + ".part", "w"))
        os.replace(LIVE + ".part", LIVE)
    except OSError:
        pass


def pending():
    try:
        return sorted(f for f in os.listdir(BOX) if f.endswith(".json"))
    except OSError:
        return []


def show(f):
    p = os.path.join(BOX, f)
    try:
        o = json.load(open(p, encoding="utf-8"))
    except Exception:
        return False
    text = " / ".join(ln.strip() for ln in str(o.get("text", "")).splitlines() if ln.strip())
    who = "전체에게" if o.get("all") else DEVKO[A.device] + "에게"
    print("[사무실 %s · %s 화면에서 온 말 → %s] %s  (답: %s -I -X utf8 %s/office_reply.py%s --device %s --id %s \"답\")"
          % (time.strftime("%H:%M", time.localtime(o.get("t", time.time()))), DEVKO.get(o.get("dev"), "?"), who, text,
             PY, HERE_FWD, CFG_ARG, A.device, o.get("id")), flush=True)
    try:
        os.replace(p, os.path.join(BOX, "read", f))
    except OSError:
        pass
    return True


if A.stream:
    print("사무실 말 듣는 중(%s · 계속)" % DEVKO[A.device], flush=True)
last = 0
while True:
    if time.time() - last > 30:
        breathe()
        last = time.time()
    names = pending()
    if names and not A.stream:
        time.sleep(2)
        n = sum(show(f) for f in pending())
        if n:
            print("다시 듣기: %s -I -X utf8 -u %s/office_inbox_watch.py%s --device %s"
                  % (PY, HERE_FWD, CFG_ARG, A.device), flush=True)
            breathe()
            break
    for f in names if A.stream else []:
        show(f)
    time.sleep(1)
