"""jellypaw-office - Jellypaw Office MCP server (standard library only, stdio JSON-RPC).

Lets a Claude Code window do office chores as tools instead of long script commands:
- answer office messages (= read receipt) · gripe board · meeting notes · approvals · patrol · notion slice · package sync
- doc_bake (steps.json / HTML -> PDF, page count, overflowing pages, PNG per page) · memo_rows (read-only)
- every writing tool previews by default and writes only with apply=true
- text that goes to the shared folder is scanned with the patrol patterns first; on a hit nothing is written
Register: claude mcp add -s user jellypaw-office -- python -I -X utf8 <this file> --config <config.json> --device <id>
"""

import argparse
import glob
import hashlib
import html as htmllib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

NAME = "jellypaw-office"
VERSION = "0.5.0"
PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
HERE = os.path.dirname(os.path.abspath(__file__))
OFFICE = os.path.normpath(os.path.join(HERE, ".."))
_spec = importlib.util.spec_from_file_location("office_config", os.path.join(OFFICE, "office_config.py"))
C = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(C)

ap = argparse.ArgumentParser()
ap.add_argument("--config", default="")
ap.add_argument("--device", default="")
ap.add_argument("--port", type=int, default=0)
ap.add_argument("--patrol-words", default="")
ap.add_argument("--private", action="store_true")
ARGS, _ = ap.parse_known_args()
CFG = C.load(ARGS.config or None)
ARGS.device = C.pick_device(CFG, ARGS.device)
ARGS.port = ARGS.port or CFG["port"]
DEVS = tuple(C.device_ids(CFG))
PRIVATE = ARGS.private or ARGS.device in CFG["private_devices"]
OWNER = CFG["owner_title"]
SHARED = C.shared_dir(CFG)
BASE = CFG["share_dir"] or CFG["local_dir"]
EXPORTS = os.path.join(BASE, "exports")
if not ARGS.patrol_words and os.path.exists(os.path.join(CFG["local_dir"], "patrol_words.local.txt")):
    ARGS.patrol_words = os.path.join(CFG["local_dir"], "patrol_words.local.txt")
STATE = {"init": False}
TOOLS = []


def log(*a):
    try:
        sys.stderr.write("[%s] %s\n" % (NAME, " ".join(str(x) for x in a)))
        sys.stderr.flush()
    except Exception:
        pass


def _text(s):
    return {"type": "text", "text": s}


def _ok(obj):
    return [_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str))], False


def _err(msg):
    return [_text(msg)], True


def _write(obj):
    sys.stdout.buffer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def _put(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".part", "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(p + ".part", p)


def _read(p, default=None):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _load(name):
    """Load a sibling module of the office folder by file path (-I keeps it off sys.path)."""
    p = os.path.join(OFFICE, name + ".py")
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _leak(text):
    """공유 폴더로 나갈 글을 순찰 꼴로 훑음 — 걸리면 무엇만 돌려줌(글은 안 찍음)."""
    try:
        pat = _load("office_patrol")
        wp = ARGS.patrol_words
        if wp and os.path.abspath(wp).lower().startswith(os.path.abspath(SHARED).lower()):
            return ["낱말 목록이 공유 폴더 안"]
        if wp and not os.path.exists(wp):
            if PRIVATE:
                return ["낱말 목록 없음 → 안 씀"]
            log("낱말 목록 없음:", wp)
        words = pat.load_words(wp) if wp and os.path.exists(wp) else []
        return [h[-1] if isinstance(h, (list, tuple)) else str(h) for h in pat.scan_text(text, words)]
    except Exception as e:
        log("순찰 못 실음:", e)
        if PRIVATE:
            return ["순찰 못 실어 안 씀"]
        return []


def _post(path, body):
    """이 기기 사무실 서버에 POST(명부는 서버가 잠그고 셈) — 못 닿으면 ok 거짓."""
    import urllib.request
    import urllib.error
    url = "http://127.0.0.1:%d%s" % (ARGS.port, path)
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % ARGS.port})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")
    except Exception as e:
        return {"ok": False, "err": "사무실 서버에 못 닿음(%s)" % str(e)[:60]}


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()[:8]


def t_status(a):
    now, out = time.time(), {}
    for d in DEVS:
        s = _read(os.path.join(SHARED, "state_%s.json" % d), {}) or {}
        out[d] = {"나이(초)": int(now - s["t"]) if s.get("t") else None, "판": s.get("ver"),
                  "순찰 걸림": ((s.get("site") or {}).get("patrol") or {}).get("n"),
                  "듣는 중": os.path.exists(os.path.join(SHARED, "inbox", d + ".live")) and now - os.path.getmtime(os.path.join(SHARED, "inbox", d + ".live")) < 90}
    box = os.path.join(SHARED, "inbox", ARGS.device)
    unread = sorted(os.path.basename(f)[:-5] for f in glob.glob(os.path.join(box, "*.json")))
    return _ok({"이 기기": ARGS.device, "공유": SHARED, "기기": out, "안 읽은 사무실 말": unread, "jellypaw-office": VERSION})


def t_reply(a):
    """사무실 말에 답 = 읽음(회의 10번은 9 안에서 「답 보내기 = 읽음」)."""
    oid, text = a["id"], a["text"]
    if not os.path.exists(os.path.join(SHARED, "orders", oid + ".json")):
        return _err("그런 말 id 없음: " + oid)
    hits = _leak(text)
    if hits:
        return _err("공유 폴더로 나가면 안 되는 꼴이 있어 안 씀: " + " · ".join(sorted(set(hits))))
    p = os.path.join(SHARED, "replies", "%s_%s.json" % (oid, ARGS.device))
    old = (_read(p, {}) or {}).get("text", "")
    new = (old + "\n\n" if old else "") + text[:4000]
    if not a.get("apply"):
        return _ok({"미리보기": True, "쓸 곳": os.path.basename(p), "글": new[-600:]})
    _put(p, {"t": time.time(), "text": new[-8000:]})
    return _ok({"답 남김": os.path.basename(p)})


def t_gripe(a):
    D = os.path.join(SHARED, "gripes")
    cmd = a["cmd"]
    if cmd == "list":
        rows = []
        for f in sorted(glob.glob(os.path.join(D, "*_*.json")), reverse=True):
            if ".ans_" in f:
                continue
            g = _read(f, {}) or {}
            base = os.path.basename(f)[:-5]
            ans = sorted(glob.glob(os.path.join(D, base + ".ans_*.json")), key=os.path.getmtime)
            rows.append({"id": g.get("id"), "by": g.get("by"), "제목": g.get("title"), "공감": len(glob.glob(os.path.join(D, base + ".plus_*"))),
                         "상태": (_read(ans[-1], {}) or {}).get("status") if ans else "접수"})
        return _ok(rows[:40])
    if cmd == "post":
        title, body = a.get("title", ""), a.get("text", "")
        if not title or not body:
            return _err("post 는 title · text 가 있어야 함")
        hits = _leak(title + "\n" + body)
        if hits:
            return _err("공유 폴더로 나가면 안 되는 꼴이 있어 안 씀: " + " · ".join(sorted(set(hits))))
        gid = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000)
        obj = {"id": gid, "t": time.time(), "by": ARGS.device, "title": title[:40], "body": body[:600]}
        if not a.get("apply"):
            return _ok({"미리보기": True, "올릴 글": obj})
        _put(os.path.join(D, "%s_%s.json" % (gid, ARGS.device)), obj)
        return _ok({"올림": gid})
    hit = [f for f in glob.glob(os.path.join(D, a.get("id", "?") + "_*.json")) if ".ans_" not in f]
    if not hit:
        return _err("그런 글 id 없음: %s" % a.get("id"))
    base = os.path.basename(hit[0])[:-5]
    if cmd == "plus":
        if base.endswith("_" + ARGS.device):
            return _err("자기 글엔 공감 안 함")
        if a.get("apply"):
            open(os.path.join(D, "%s.plus_%s" % (base, ARGS.device)), "w").close()
        return _ok({"공감": base, "미리보기": not a.get("apply")})
    if cmd == "answer":
        st = a.get("status")
        if st not in ("고침", "보류", "안고침") or not a.get("text"):
            return _err("answer 는 status(고침 · 보류 · 안고침) · text 가 있어야 함")
        obj = {"t": time.time(), "by": ARGS.device, "status": st, "text": a["text"][:600]}
        hits = _leak(a["text"])
        if hits:
            return _err("공유 폴더로 나가면 안 되는 꼴이 있어 안 씀: " + " · ".join(sorted(set(hits))))
        if a.get("apply"):
            _put(os.path.join(D, "%s.ans_%s.json" % (base, ARGS.device)), obj)
        return _ok({"답": base, "미리보기": not a.get("apply"), "글": obj})
    return _err("cmd 는 list · post · plus · answer")


def t_meeting(a):
    """회의 답 쓰기 — read · s1 · s2 · g."""
    mid, kind, text = a["id"], a["kind"], a.get("text", "")
    d = os.path.join(SHARED, "meetings", mid)
    if not os.path.isdir(d):
        return _err("그런 회의 없음: " + mid)
    if kind == "read":
        out = {n: open(os.path.join(d, n), encoding="utf-8").read() for n in sorted(os.listdir(d)) if n.endswith(".md")}
        meta = _read(os.path.join(d, "meta.json"), {}) or {}
        mine = "s1_%s.md" % ARGS.device
        if meta.get("stage") == "제안" and mine not in out:
            out = {k: v for k, v in out.items() if not k.startswith("s1_")}
        return _ok({"meta": meta, "글": out})
    if kind not in ("s1", "s2", "g"):
        return _err("kind 는 read · s1 · s2 · g")
    hits = _leak(text)
    if hits:
        return _err("공유 폴더로 나가면 안 되는 꼴이 있어 안 씀: " + " · ".join(sorted(set(hits))))
    long = [l for l in text.split("\n") if len(l) > 60]
    p = os.path.join(d, "%s_%s.md" % (kind, ARGS.device))
    if not a.get("apply"):
        return _ok({"미리보기": True, "쓸 곳": os.path.basename(p), "60자 넘는 줄": len(long), "글": text})
    with open(p + ".part", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(p + ".part", p)
    return _ok({"씀": os.path.basename(p), "60자 넘는 줄": len(long)})


def t_approval(a):
    """결재함 — 올리기 · 보기(도장 = 실제 「해」 는 그 기기 창에서 사장이 · 이 도구는 상태를 안 바꿈)."""
    D = os.path.join(SHARED, "approvals")
    if a["cmd"] == "list":
        rows = []
        for f in sorted(glob.glob(os.path.join(D, "*.json")), reverse=True)[:20]:
            x = _read(f, {}) or {}
            rows.append({"file": os.path.basename(f), "제목": x.get("title"), "올린 이": x.get("by"), "상태": x.get("status", "대기")})
        return _ok(rows)
    if a["cmd"] != "post":
        return _err("cmd 는 list · post")
    doc = a.get("doc") or {}
    need = ["kind", "title", "summary", "points", "why", "risk", "undo", "where"]
    miss = [k for k in need if not doc.get(k)]
    if miss:
        return _err("양식 빠진 칸: %s (approval_form.md)" % ", ".join(miss))
    if doc.get("kind") == "연봉":
        try:
            ask = int(doc.get("ask"))
        except Exception:
            ask = 0
        if not 0 < ask <= 99:
            return _err("연봉협상서는 ask(바라는 주급 1~99 쿠키)가 있어야 함")
        doc["ask"] = ask
    doc = dict(doc, by=ARGS.device, status="대기", t=time.time())
    if PRIVATE:
        return _err("비공개 기기 결재는 원문을 그 기기 approvals_local 에 두고 껍데기만 공유 — 이 도구로는 안 올림")
    hits = _leak(json.dumps(doc, ensure_ascii=False))
    if hits:
        return _err("공유 폴더로 나가면 안 되는 꼴이 있어 안 씀: " + " · ".join(sorted(set(hits))))
    name = time.strftime("%Y%m%d-%H%M%S") + "-%03d" % (int(time.time() * 1000) % 1000) + "_" + ARGS.device + ".json"
    if not a.get("apply"):
        return _ok({"미리보기": True, "파일": name, "문서": doc})
    _put(os.path.join(D, name), doc)
    return _ok({"올림": name})


def t_patrol(a):
    pat = _load("office_patrol")
    words = pat.load_words(a["words"]) if a.get("words") else []
    hits = pat.patrol(SHARED, words)
    return _ok({"걸림": len(hits), "곳": [h if isinstance(h, (str, dict)) else list(h) for h in hits[:50]]})


def t_notion_slice(a):
    """notion_slice — 큰 쪽(notion-fetch 결과 파일)에서 절 · 변경 로그 위 N줄 · 문장 몇 번."""
    ns = _load("notion_slice")
    argv = [a["file"]] + list(a.get("args") or [])
    buf = io.StringIO()
    with redirect_stdout(buf):
        try:
            code = ns.main(argv)
        except SystemExit as e:
            code = e.code
    return [_text(buf.getvalue()[-20000:] or "(나온 것 없음)")], bool(code)


def t_pkg(a):
    """판 받기 — 꾸러미 폴더와 내 설치 폴더를 md5 로 견주고, apply 면 다른 것만 복사(옛것은 _이전판 으로)."""
    src, dst = a["pkg"], a["dest"]
    if not os.path.isdir(src) or not os.path.isdir(dst):
        return _err("폴더 없음: pkg 또는 dest")
    rows, todo = [], []
    for root, dirs, files in os.walk(src):
        dirs[:] = [x for x in dirs if x not in ("passed", "_이전판", "_broken")]
        for n in files:
            if n.endswith((".md", ".txt")) and root == src:
                continue
            s = os.path.join(root, n)
            rel = os.path.relpath(s, src)
            d = os.path.join(dst, rel)
            ms, md = _md5(s), (_md5(d) if os.path.exists(d) else None)
            rows.append({"파일": rel.replace("\\", "/"), "꾸러미": ms, "내것": md, "같음": ms == md})
            if ms != md:
                todo.append((s, d, rel))
    if not a.get("apply"):
        return _ok({"미리보기": True, "다른 것": [r for r in rows if not r["같음"]], "같은 것 수": sum(r["같음"] for r in rows)})
    keep = os.path.join(dst, "_이전판", time.strftime("%Y%m%d-%H%M%S"))
    for s, d, rel in todo:
        if os.path.exists(d):
            os.makedirs(os.path.dirname(os.path.join(keep, rel)), exist_ok=True)
            shutil.copy2(d, os.path.join(keep, rel))
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
    bad = [rel for s, d, rel in todo if _md5(s) != _md5(d)]
    return _ok({"복사": len(todo), "옛것": keep if todo else None, "복사 뒤 어긋남": bad,
                "다음": "서버 파일(office_server.py)이 바뀌었으면 서버를 다시 켜야 함 · 화면만이면 새로 고침"})


ROOM_SLOTS = {"인턴": 8, "수습": 10, "사원": 12, "주임": 14, "대리": 16, "과장": 18, "차장": 20, "부장": 20,
              "이사": 22, "상무": 24, "전무": 26, "부사장": 28, "사장": 30, "대표": 30}


def _items():
    j = _read(os.path.join(OFFICE, "web", "data", "items.json"), {}) or {}
    return {it["id"]: it for it in (j.get("items") or [])}


def t_room(a):
    """숙소 꾸미기 — shop(살 수 있는 것) · mine(내 방) · buy(id · apply) · 지갑 = 쿠키 − 숙소에 쓴 것."""
    me = (_read(os.path.join(SHARED, "staff.json"), {}) or {}).get(ARGS.device) or {}
    p = os.path.join(SHARED, "rooms", ARGS.device + ".json")
    room = _read(p, {}) or {}
    mine = room.get("items") or []
    spent = sum(int(x.get("price", 0)) for x in mine if not x.get("card"))
    gone = set((_read(os.path.join(SHARED, "rooms", ARGS.device + "_seized.json"), {}) or {}).get("ids") or [])
    here = [x for i, x in enumerate(mine) if i not in gone]
    wallet, slots = int(me.get("cookies", 0)) - spent, ROOM_SLOTS.get(me.get("rank", ""), 12)
    items = _items()
    if a["cmd"] == "mine":
        return _ok({"직급": me.get("rank"), "칸": "%d/%d" % (len(here), slots), "지갑": wallet, "물건": [items.get(x["id"], {}).get("ko", x["id"]) for x in here],
                    "집": me.get("house"), "빚": me.get("loan"), "감봉": me.get("cut")})
    if a["cmd"] == "shop":
        L = [it for it in items.values() if it.get("price", 99) <= int(a.get("max") or wallet)
             and (not a.get("cat") or it.get("cat") == a["cat"]) and (not a.get("who") or it.get("who") == a["who"])]
        return _ok({"지갑": wallet, "칸": "%d/%d" % (len(here), slots), "살 수 있는 것": [{"id": it["id"], "ko": it["ko"], "값": it["price"], "갈래": it.get("cat")} for it in L[:60]]})
    if a["cmd"] != "buy":
        return _err("cmd 는 shop · mine · buy")
    it = items.get(a.get("id", ""))
    if not it:
        return _err("그런 물건 없음: %s" % a.get("id"))
    if len(here) >= slots:
        return _err("방이 꽉 찼어(%d칸) — 승진하거나 큰 집으로 이사하면 칸이 늘어" % slots)
    card = bool(a.get("card"))
    if card:
        cj = _post("/api/card", {"who": ARGS.device, "price": it["price"]})
        if not cj.get("ok") or not cj.get("can"):
            return _err("카드 못 씀: %s" % (cj.get("why") or cj.get("err")))
        if not a.get("apply"):
            return _ok({"미리보기": True, "살 것": it["ko"], "값": it["price"], "카드": "한도 %d · 이번 주 쓴 것 %d → %d" % (cj["limit"], cj["used"], cj["used"] + it["price"]),
                        "주의": "주급 날 지갑에서 한꺼번에 냄 · 못 내면 이자 50% 붙고 그 자리에서 비싼 물건부터 압류"})
    elif it["price"] > wallet:
        return _err("쿠키가 모자라(%d/%d) — 모아서 사거나 카드(card 참)로" % (wallet, it["price"]))
    note = "" if PRIVATE else str(a.get("note", ""))[:60]
    hit = _leak(note) if note else []
    if hit:
        return _err("메모에 걸림: %s — 메모 빼고 다시" % " · ".join(hit))
    if not a.get("apply"):
        return _ok({"미리보기": True, "살 것": it["ko"], "값": it["price"], "산 뒤 지갑": wallet - it["price"]})
    mine.append(dict({"id": it["id"], "price": it["price"], "t": time.time(), "note": note}, **({"card": 1} if card else {})))
    _put(p, {"items": mine})
    with open(os.path.join(SHARED, "ledger_%s.jsonl" % ARGS.device), "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.time(), "who": ARGS.device, "kind": "buy", "id": it["id"], "price": it["price"], "card": int(card)}, ensure_ascii=False) + "\n")
    return _ok({"샀어": it["ko"] + (" (카드)" if card else ""), "메모": "안 받음(비공개 기기)" if PRIVATE and a.get("note") else note, "지갑": wallet - (0 if card else it["price"]), "칸": "%d/%d" % (len(here) + 1, slots)})


def t_catch(a):
    """🖍️ 빨간 펜 — 남의 실수를 잡아 줌 · 증거(경로 · md5) 있으면 🍪+1 · 하루 5번 · 제 것 안 됨 · 공유엔 누가 · 누구 · 쿠키만(무엇 글은 안 씀)."""
    tgt = a.get("target")
    if tgt not in DEVS or tgt == ARGS.device:
        return _err("target 은 다른 기기(%s) — 제 실수는 교훈 대기에" % " · ".join(DEVS))
    what = str(a.get("what", ""))[:40]
    hit = _leak(what + " " + str(a.get("evidence", "")))
    if hit:
        return _err("글에 걸림: %s" % " · ".join(hit))
    if not a.get("apply"):
        return _ok({"미리보기": True, "잡은 쪽": ARGS.device, "잡힌 쪽": tgt, "쿠키": 1 if a.get("evidence") else 0, "규칙": "하루 5번 · 증거 없으면 펜만 · 탓하지 않기(잡힌 쪽 「고마워!」)"})
    j = _post("/api/catch", {"who": ARGS.device, "target": tgt, "evidence": bool(a.get("evidence"))})
    if not j.get("ok"):
        return _err("못 남김: %s" % j.get("err"))
    return _ok({"남김": True, "쿠키": j.get("cookie"), "오늘": j.get("today"), "무엇(이 창에만)": what})


def t_house(a):
    """이사 — 이 기기 사무실 서버로 보냄(명부는 서버가 잠그고 씀). 두 칸 위를 빌려서 가려면 office_approval post(kind 대출)."""
    up = int(a.get("up") or 1)
    import urllib.request
    url = "http://127.0.0.1:%d/api/house" % ARGS.port
    rule = "보증금 = 주 수입 × 6 × 칸 · 옛 보증금은 돌려받음 · 모자라면 한 칸은 스스로 빌림(× 4 · 이자 한 번 25%) · 두 칸은 사장 승인 대출 · 세 칸부터는 다 모아야"
    if not a.get("apply"):
        req = urllib.request.Request(url, data=json.dumps({"who": ARGS.device, "up": up, "dry": True}).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % ARGS.port})
        try:
            j = json.loads(urllib.request.urlopen(req, timeout=10).read())
            d = j.get("dry") or {}
            hs = [h for h in ((_read(os.path.join(OFFICE, "web", "data", "houses.json"), {}) or {}).get("houses") or []) if int(h.get("tier", 0)) == d.get("tier")]
            return _ok({"미리보기": True, "이사": "%d 칸 위" % up, "지금 집 칸": d.get("cur"), "갈 집 칸": d.get("tier"),
                        "고를 수 있는 집(house)": [{"id": h["id"], "ko": h["ko"]} for h in hs],
                        "보증금": d.get("deposit"), "모은 쿠키": d.get("have"), "빌릴 몫": d.get("borrow"), "갚을 돈": d.get("due"),
                        "갈 수 있음": j.get("can"), "못 가는 까닭": j.get("why"), "규칙": rule})
        except Exception as e:
            return _ok({"미리보기": True, "이사": "%d 칸 위" % up, "규칙": rule, "서버 셈 못 함": str(e)[:80]})
    req = urllib.request.Request(url, data=json.dumps({"who": ARGS.device, "up": up, "house": a.get("house") or None}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % ARGS.port})
    try:
        j = json.loads(urllib.request.urlopen(req, timeout=10).read())
    except urllib.error.HTTPError as e:
        j = json.loads(e.read() or b"{}")
    if not j.get("ok"):
        return _err("이사 못 함: %s" % j.get("err"))
    st = (j.get("staff") or {}).get(ARGS.device, {})
    return _ok({"이사함": st.get("house"), "빚": st.get("loan"), "쿠키": st.get("cookies")})


EDGE_CANDIDATES = (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                   r"C:\Program Files\Microsoft\Edge\Application\msedge.exe")
BAKE_MEASURE = r"""<script>(function(){
function clip(e,p){var q=e.getBoundingClientRect(),b=q.bottom,rt=q.right;
 for(var a=e.parentElement;a&&a!==p;a=a.parentElement){var s=getComputedStyle(a),r=a.getBoundingClientRect();
  if(s.overflowY!=='visible')b=Math.min(b,r.bottom);if(s.overflowX!=='visible')rt=Math.min(rt,r.right);}
 return [b,rt];}
function inAbs(e,p){for(;e&&e!==p;e=e.parentElement){var s=getComputedStyle(e).position;if(s==='absolute'||s==='fixed')return true;}return false;}
function go(){var o=[];document.querySelectorAll(__SEL__).forEach(function(p,i){
 var r=p.getBoundingClientRect(),pb=parseFloat(getComputedStyle(p).paddingBottom)||0,hit=[],soft=0;
 p.querySelectorAll('*').forEach(function(e){var q=e.getBoundingClientRect();if(!q.width||!q.height)return;
  var c=clip(e,p),dy=c[0]-r.bottom,dx=c[1]-r.right;
  if(dy>1||dx>1)hit.push({e:e,dy:Math.round(dy),dx:Math.round(dx)});
  else if(!e.children.length&&c[0]>r.bottom-pb+1&&!inAbs(e,p))soft++;});
 var bad=hit.filter(function(h){return !hit.some(function(g){return g!==h&&h.e.contains(g.e);});}).slice(0,3).map(function(h){var e=h.e;
  return {el:e.tagName.toLowerCase()+(typeof e.className==='string'&&e.className?'.'+e.className.split(' ')[0]:''),dy:h.dy,dx:h.dx,t:(e.textContent||'').trim().slice(0,24)};});
 var pr=parseFloat(getComputedStyle(p).paddingRight)||0;
 o.push({n:i+1,w:Math.round(r.width),h:Math.round(r.height),oy:p.scrollHeight-p.clientHeight-Math.round(pb),ox:p.scrollWidth-p.clientWidth-Math.round(pr),bad:bad,soft:soft});});
 var e=document.createElement('pre');e.id='__bake';e.textContent=JSON.stringify(o);document.body.appendChild(e);}
(document.fonts&&document.fonts.ready?document.fonts.ready:Promise.resolve()).then(function(){setTimeout(go,50);});})();</script>"""
BAKE_ONLY = r"""<script>document.addEventListener('DOMContentLoaded',function(){var k=__K__;
document.querySelectorAll(__SEL__).forEach(function(p,i){if(i!==k)p.remove();});
document.documentElement.style.margin=document.body.style.margin='0';});</script>"""
BAKE_RUNNER = ("import importlib.util,json,sys\n"
               "s=importlib.util.spec_from_file_location('bake_builder',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
               "h=m.build(json.load(open(sys.argv[2],encoding='utf-8')))\n"
               "open(sys.argv[3],'w',encoding='utf-8').write(h)\n")


def _edge():
    extra = (CFG["edge_path"] or "", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
    for p in extra[:1] + EDGE_CANDIDATES + extra[1:] + tuple(shutil.which(x) or "" for x in ("msedge", "microsoft-edge")):
        if p and os.path.isfile(p):
            return p
    return None


def _under(p, root):
    p, root = os.path.normcase(os.path.abspath(p)), os.path.normcase(os.path.abspath(root))
    return p == root or p.startswith(root.rstrip("\\/") + os.sep)


def _run(cmd, timeout=180):
    """밖 프로세스 — 표준 입력은 안 넘김(MCP 줄을 먹지 않게) · 창 안 띄움."""
    return subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _edge_run(edge, prof, extra, url, timeout=180):
    return _run([edge, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars",
                 "--user-data-dir=" + prof] + extra + [url], timeout)


def _settle(p, tries=120):
    """Edge 가 돌아온 뒤에도 파일을 늦게 씀(build.py 에서 본 것) — 크기가 멈출 때까지."""
    last = -1
    for _ in range(tries):
        time.sleep(0.5)
        if os.path.exists(p):
            sz = os.path.getsize(p)
            if sz > 0 and sz == last:
                return True
            last = sz
    return os.path.exists(p) and os.path.getsize(p) > 0


def _inject(doc, head_add="", body_add=""):
    if head_add:
        m = re.search(r"<head[^>]*>", doc, re.I)
        doc = doc[:m.end()] + head_add + doc[m.end():] if m else head_add + doc
    if body_add:
        i = doc.lower().rfind("</body>")
        doc = doc[:i] + body_add + doc[i:] if i >= 0 else doc + body_add
    return doc


def _visible_text(doc):
    t = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", doc)
    t = re.sub(r"(?s)<[^>]+>", "\n", t)
    return "\n".join(x.strip() for x in htmllib.unescape(t).splitlines() if x.strip())


def _pdf_pages(p):
    try:
        import pypdf
        return len(pypdf.PdfReader(p).pages)
    except Exception:
        with open(p, "rb") as f:
            return len(re.findall(rb"/Type\s*/Page(?![a-zA-Z])", f.read()))


def _keep_old(p, out):
    """덮어쓸 것이 있으면 out/_이전판/<시각>/ 으로 옮김(판 받기와 같은 꼴)."""
    if not os.path.exists(p):
        return None
    keep = os.path.join(out, "_이전판", time.strftime("%Y%m%d-%H%M%S"))
    os.makedirs(keep, exist_ok=True)
    dst = os.path.join(keep, os.path.basename(p))
    shutil.move(p, dst)
    return dst


def t_doc_bake(a):
    """문서 굽기 — steps.json(+ 옆 build.py 의 build(spec)) 또는 HTML → PDF · 쪽 수 · 넘친 쪽 · (png) 쪽마다 PNG."""
    src = os.path.abspath(a["src"])
    if not os.path.isfile(src):
        return _err("원본 없음: " + src)
    edge = _edge()
    if not edge:
        return _err("msedge.exe 를 못 찾음(%s)" % " · ".join(EDGE_CANDIDATES))
    sel = str(a.get("selector") or ".page")
    notes = []
    ext = os.path.splitext(src)[1].lower()
    if ext not in (".json", ".html", ".htm"):
        return _err("src 는 steps.json 또는 .html")
    name = os.path.basename(str(a.get("name") or ""))
    out = a.get("out")
    if out:
        base = EXPORTS
        out = os.path.normpath(out if os.path.isabs(out) else os.path.join(base, out))
        if not os.path.isabs(a["out"]) and not _under(out, base):
            return _err("out 이 exports 폴더 밖으로 나감: %s" % a["out"])
        if _under(out, SHARED):
            return _err("out 이 사무실 폴더(ai_office) 안 — exports/<폴더> 나 다른 곳으로")
    elif a.get("apply"):
        return _err("apply 엔 out 이 있어야 함(exports 아래 폴더 이름 또는 절대 경로)")
    tmp = tempfile.mkdtemp(prefix="jellypaw_bake_")
    try:
        if ext == ".json":
            try:
                spec = json.load(open(src, encoding="utf-8"))
            except Exception as e:
                return _err("steps.json 못 읽음: %s" % e)
            builder = os.path.abspath(a.get("builder") or os.path.join(os.path.dirname(src), "build.py"))
            if not os.path.isfile(builder):
                return _err("steps.json 옆에 build.py 가 없음 — builder 로 build(spec) 가 있는 파일을 줘")
            built = os.path.join(tmp, "built.html")
            r = _run([sys.executable, "-I", "-X", "utf8", "-c", BAKE_RUNNER, builder, src, built], 120)
            if r.returncode or not os.path.exists(built):
                return _err("build.py 의 build(spec) 실패: %s" % r.stderr.decode("utf-8", "replace")[-400:])
            doc = open(built, encoding="utf-8").read()
            root = os.path.dirname(builder)
            name = name or (spec.get("pdf_name") if isinstance(spec, dict) else "") or ""
            how = "steps.json + %s 의 build(spec)" % os.path.basename(builder)
        else:
            doc = open(src, encoding="utf-8", errors="replace").read()
            root = os.path.dirname(src)
            how = "HTML 그대로"
        name = os.path.basename(name or os.path.splitext(os.path.basename(src))[0] + ".pdf")
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        if not re.search(r"<base\s", doc, re.I):
            doc = _inject(doc, head_add='<base href="%s/">' % Path(root).as_uri())
        hits = []
        if out and CFG["share_dir"] and _under(out, CFG["share_dir"]):
            hits = sorted(set(_leak(_visible_text(doc))))
        page_html = os.path.join(tmp, "doc.html")
        open(page_html, "w", encoding="utf-8").write(doc)
        mhtml = os.path.join(tmp, "measure.html")
        open(mhtml, "w", encoding="utf-8").write(_inject(doc, body_add=BAKE_MEASURE.replace("__SEL__", json.dumps(sel))))
        prof = os.path.join(tmp, "edge_profile")
        r = _edge_run(edge, prof, ["--window-size=1600,1200", "--virtual-time-budget=8000", "--dump-dom"], Path(mhtml).as_uri())
        m = re.search(r'<pre id="__bake">(.*?)</pre>', r.stdout.decode("utf-8", "replace"), re.S)
        pages = json.loads(htmllib.unescape(m.group(1))) if m else None
        if pages is None:
            notes.append("넘침을 못 잼(Edge --dump-dom 에 결과가 안 나옴)")
        elif not pages:
            notes.append("고정 크기 쪽(%s)이 없어 넘침은 못 잼 · 쪽마다 PNG 도 못 함 — PDF 쪽 수만" % sel)
        hard = lambda p: p["oy"] > 1 or p["ox"] > 1 or p["bad"]
        over = [{"쪽": p["n"], "아래로 px": max([0, p["oy"]] + [b["dy"] for b in p["bad"]]), "옆으로 px": max([0, p["ox"]] + [b["dx"] for b in p["bad"]]),
                 "넘친 것": ["%s(%s)" % (b["el"], b["t"]) for b in p["bad"]] or ["상자는 안 나갔는데 글이 흘러 넘침"]} for p in (pages or []) if hard(p)]
        soft = [{"쪽": p["n"], "요소 수": p["soft"]} for p in (pages or []) if p["soft"] and not hard(p)]
        tpdf = os.path.join(tmp, "out.pdf")
        r = _edge_run(edge, prof, ["--no-pdf-header-footer", "--virtual-time-budget=8000", "--print-to-pdf=" + tpdf], Path(page_html).as_uri())
        if not _settle(tpdf):
            return _err("PDF 못 만듦: %s" % r.stderr.decode("utf-8", "replace")[-300:])
        n_pdf = _pdf_pages(tpdf)
        res = {"미리보기": not a.get("apply"), "만든 길": how, "PDF 쪽 수": n_pdf, "쪽 요소(%s) 수" % sel: len(pages) if pages is not None else None,
               "넘친 쪽": over or "없음", "아래 여백 침범(넘치진 않음)": soft or "없음"}
        if pages and n_pdf != len(pages):
            res["쪽 수 어긋남"] = "PDF %d쪽 · %s %d개 — 어느 쪽이 인쇄에서 둘로 갈렸거나 빈 쪽이 붙음" % (n_pdf, sel, len(pages))
        want_png = bool(a.get("png")) and bool(pages)
        if a.get("png") and not pages:
            notes.append("png: 쪽 요소가 없어 쪽마다 찍기 못 함")
        if hits:
            res["순찰"] = hits
        notes.append("넘침은 화면(screen) 꼴로 잼 — @media print 규칙이 따로 있으면 인쇄와 다를 수 있음")
        if want_png:
            notes.append("PNG 는 Edge 화면 찍기(그 쪽만 남김) · PDF 를 그림으로 푼 것은 아님(fitz · pdf2image · 고스트스크립트 없음)")
        if not out:
            res["쓸 곳"] = "out 을 주면 exports/<out>/%s" % name
            res["주의"] = notes
            return _ok(res)
        dst_pdf = os.path.join(out, name)
        png_dir = os.path.join(out, os.path.splitext(name)[0] + "_png")
        if not a.get("apply"):
            res["쓸 곳"] = {"PDF": dst_pdf, "PNG": png_dir if want_png else None}
            res["주의"] = notes
            return _ok(res)
        if hits:
            return _err("공유 폴더로 나가면 안 되는 꼴이 있어 안 씀: " + " · ".join(hits))
        made_png = []
        if want_png:
            scale = min(max(float(a.get("png_scale") or 1), 0.5), 3)
            tpng = os.path.join(tmp, "png")
            os.makedirs(tpng, exist_ok=True)
            for p in pages:
                one = os.path.join(tmp, "only_%02d.html" % p["n"])
                open(one, "w", encoding="utf-8").write(_inject(doc, head_add=BAKE_ONLY.replace("__K__", str(p["n"] - 1)).replace("__SEL__", json.dumps(sel))))
                shot = os.path.join(tpng, "p%02d.png" % p["n"])
                _edge_run(edge, prof, ["--window-size=%d,%d" % (p["w"], p["h"]), "--force-device-scale-factor=%g" % scale,
                                       "--virtual-time-budget=8000", "--screenshot=" + shot], Path(one).as_uri())
                if _settle(shot, 20):
                    made_png.append(os.path.basename(shot))
        os.makedirs(out, exist_ok=True)
        old = [x for x in (_keep_old(dst_pdf, out), _keep_old(png_dir, out) if want_png else None) if x]
        shutil.move(tpdf, dst_pdf)
        if want_png:
            shutil.move(os.path.join(tmp, "png"), png_dir)
        res.update({"씀": {"PDF": dst_pdf, "PNG": ("%s (%d장)" % (png_dir, len(made_png))) if want_png else None}, "옛것": old or None, "주의": notes})
        if want_png and len(made_png) != len(pages):
            res["PNG 빠짐"] = sorted(set("p%02d.png" % p["n"] for p in pages) - set(made_png))
        return _ok(res)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


MEMO_COL = "넣을 노션 쪽"


def _memo_dir(given):
    if given:
        return given if os.path.isdir(given) else None
    cands = [d for pat in ("exports/*/book_memo", "exports/book_memo", "*/book_memo", "book_memo")
             for d in glob.glob(os.path.join(BASE, pat)) if os.path.isdir(d)]
    return max(cands, key=lambda d: (len(glob.glob(os.path.join(d, "*.md"))), os.path.getmtime(d))) if cands else None


def _cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def _norm(s):
    s = re.sub(r"[*`]", "", s or "")
    return re.sub(r"\s+", " ", s).strip().casefold()


def _memo_parse(path):
    """한 메모 파일 → [(줄 번호, 넣을 쪽, {칸: 값})]. 두 꼴: 「| … | 넣을 노션 쪽 | … |」 표 · 「## 쪽 이름 (n)」 밑 「- 장 · 쪽 | 알맹이 | 노션에 있나」 줄."""
    rows, head, cur = [], None, None
    lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    has_table = any(l.lstrip().startswith("|") and MEMO_COL in l for l in lines)
    for n, ln in enumerate(lines, 1):
        s = ln.strip()
        if has_table:
            if s.startswith("|") and MEMO_COL in s:
                head = _cells(s)
                continue
            if head and s.startswith("|"):
                c = _cells(s)
                if all(re.fullmatch(r":?-{2,}:?", x or "--") for x in c):
                    continue
                col = head.index(MEMO_COL)
                k = len(c) - (len(head) - col)
                if 0 <= k < len(c):
                    d = dict(zip(head, c)) if len(c) == len(head) else {"줄": s}
                    rows.append((n, c[k], d))
                continue
            if head and not s.startswith("|"):
                head = None
        else:
            if s.startswith("## "):
                cur = re.sub(r"\s*\(\d+\)\s*$", "", s[3:]).strip()
                continue
            if cur and s.startswith("- ") and s.count(" | ") >= 2:
                c = [x.strip() for x in s[2:].split(" | ")]
                d = {"장 · 쪽": c[0], "알맹이": " | ".join(c[1:-1]), "노션에 있나": c[-1]}
                rows.append((n, cur, d))
    return rows


def t_memo_rows(a):
    """책 메모 줄 뽑기 — 공유 book_memo 메모 파일마다 「넣을 노션 쪽」 칸이 page 와 같거나 page 를 품은 줄을 다(파일 · 줄 번호와 함께)."""
    d = _memo_dir(a.get("dir"))
    if not d:
        return _err("book_memo 폴더를 못 찾음 — dir 로 줘")
    files = sorted(glob.glob(os.path.join(d, "*.md")))
    allrows = [(os.path.basename(f), n, tgt, row) for f in files for n, tgt, row in _memo_parse(f)]
    if a.get("list"):
        cnt = {}
        for _, _, tgt, _ in allrows:
            cnt[tgt] = cnt.get(tgt, 0) + 1
        return _ok({"메모 폴더": d, "파일 수": len(files), "줄 수": len(allrows),
                    "넣을 쪽 이름(줄 수)": [{"쪽": k, "줄": v} for k, v in sorted(cnt.items(), key=lambda x: -x[1])]})
    q = _norm(a.get("page", ""))
    if not q:
        return _err("page(노션 쪽 이름)가 있어야 함 — 이름 목록은 list 참")
    exact = bool(a.get("exact"))
    hit = []
    for fn, n, tgt, row in allrows:
        t = _norm(tgt)
        if t == q or (not exact and q in t):
            hit.append(dict({"파일": fn, "줄": n, "맞음": "같음" if t == q else "포함", MEMO_COL: tgt}, **{k: v for k, v in row.items() if k != MEMO_COL}))
    lim = int(a.get("max") or 300)
    res = {"메모 폴더": d, "파일 수": len(files), "찾은 쪽": a.get("page"), "맞은 줄": len(hit),
           "파일별": {fn: sum(1 for h in hit if h["파일"] == fn) for fn in sorted(set(h["파일"] for h in hit))}, "줄들": hit[:lim]}
    if len(hit) > lim:
        res["잘림"] = "%d줄 중 %d줄만 — max 로 늘림" % (len(hit), lim)
    if not hit:
        words = [w for w in re.split(r"[\s·()/,]+", q) if len(w) > 1]
        near = sorted({tgt for _, _, tgt, _ in allrows if any(w in _norm(tgt) for w in words)})
        res["비슷한 쪽 이름"] = near[:20]
    return _ok(res)


def _p(desc, **kw):
    return {"type": "object", "properties": kw, "required": [k for k, v in kw.items() if v.pop("req", False)], "description": desc}


def tools():
    S = lambda d, req=False: {"type": "string", "description": d, "req": req}
    B = lambda d: {"type": "boolean", "description": d}
    return [
        {"name": "office_status", "description": "사무실 한눈에 — 기기마다 상태 나이 · 판 · 순찰 걸림 · 듣는 중 · 이 기기 안 읽은 사무실 말.",
         "inputSchema": _p("인자 없음"), "fn": t_status},
        {"name": "office_reply", "description": "사무실에서 온 말(id)에 답 = 읽음. 첫 줄이 캐릭터 말풍선. 기밀 원본 · 고객 · 경로는 쓰지 않음(걸리면 안 씀). apply 참일 때만 씀.",
         "inputSchema": _p("말 답", id=S("말 id(알림의 id)", True), text=S("답 글", True), apply=B("참이면 씀 · 기본 미리보기")), "fn": t_reply},
        {"name": "office_gripe", "description": "불만함 — list · post(title · text) · plus(id · 나도 그래) · answer(id · status 고침/보류/안고침 · text). 쓰기는 apply 참일 때만.",
         "inputSchema": _p("불만함", cmd=S("list · post · plus · answer", True), id=S("글 id"), title=S("제목 40자 안"), text=S("내용 · 답"), status=S("고침 · 보류 · 안고침"), apply=B("참이면 씀")), "fn": t_gripe},
        {"name": "office_meeting", "description": "회의 — read(봉인 지킴) · s1(1단) · s2(투표 「번호 찬/반」) · g(건의) 를 meetings/<id>/<kind>_<기기>.md 에. 줄마다 40자 안 권장.",
         "inputSchema": _p("회의", id=S("회의 id 예: 20261031-0217", True), kind=S("read · s1 · s2 · g", True), text=S("쓸 글"), apply=B("참이면 씀")), "fn": t_meeting},
        {"name": "office_approval", "description": "결재함 — list · post(doc = approval_form.md 칸: kind · title · summary · points · why · preview · risk · undo · where · card · review · meeting). 도장(실제 「해」)은 그 기기 창에서 사장이. 비공개 기기는 이 도구로 안 올림(껍데기 규칙).",
         "inputSchema": _p("결재함", cmd=S("list · post", True), doc={"type": "object", "description": "결재 문서"}, apply=B("참이면 올림")), "fn": t_approval},
        {"name": "office_patrol", "description": "현장 순찰 — 공유 폴더에 경로 · 메일 · 토큰 · 기기 안 낱말 꼴이 섞였는지 「파일:줄 · 무엇」 만(글은 안 찍음). words = 기기 안 낱말 목록 파일(공유 폴더 밖).",
         "inputSchema": _p("순찰", words=S("낱말 목록 파일(선택)")), "fn": t_patrol},
        {"name": "notion_slice", "description": "큰 노션 쪽 대조(notion_slice) — notion-fetch 결과 파일에서 절 · 변경 로그 위 N줄 · 문장 몇 번. args 예: [\"--log\",\"3\"] · [\"--count\",\"낱말\"].",
         "inputSchema": _p("큰 쪽 대조", file=S("fetch 결과 파일", True), args={"type": "array", "items": {"type": "string"}, "description": "notion_slice 인자"}), "fn": t_notion_slice},
        {"name": "office_room", "description": "숙소 꾸미기 — 내 주급(쿠키)으로 내가 고른 물건을 내 방에 · shop(살 수 있는 것 · cat · who · max) · mine(내 방 · 지갑 · 집 · 빚) · buy(id · note · apply). 지갑 = 쿠키 − 숙소에 쓴 것 · 방 칸은 직급마다.",
         "inputSchema": _p("숙소", cmd=S("shop · mine · buy", True), id=S("물건 id"), cat=S("갈래"), who=S("동물 취향"), max={"type": "integer", "description": "값 상한"}, note=S("한 줄 일기"), card=B("참이면 젤리발 카드로(한도 주 수입 × 2 · 주급 날 갚음 · 못 갚으면 이자 50% · 바로 압류)"), apply=B("참이면 삼")), "fn": t_room},
        {"name": "office_catch", "description": "빨간 펜 — 다른 기기의 실수를 짚어 줬을 때 남김(target · what 40자 · evidence 경로나 md5). 증거 있으면 쿠키 1 · 하루 5번 · 제 것 안 됨 · 공유엔 누가 · 누구 · 쿠키만. apply 참일 때만.",
         "inputSchema": _p("빨간 펜", target=S("잡힌 기기 id", True), what=S("무엇(40자 · 이 창에만)"), evidence=S("증거 경로나 md5"), apply=B("참이면 남김")), "fn": t_catch},
        {"name": "office_house", "description": "이사 — up 칸 위 집으로(보증금 = 주 수입 × 6 × 칸 · 모자라면 한 칸은 스스로 빌림 · 두 칸은 사장 승인 대출 · 세 칸부터는 다 모아야). 돈을 모아 더 좋은 집을 사는 것도 자유.",
         "inputSchema": _p("이사", up={"type": "integer", "description": "몇 칸 위(1~)"}, house=S("그 띠 집 id(미리보기 목록 · 안 주면 아무거나)"), apply=B("참이면 이사")), "fn": t_house},
        {"name": "office_pkg", "description": "판 받기 — 꾸러미 폴더(pkg)와 내 설치 폴더(dest)를 md5 로 견줌 · apply 면 다른 것만 복사하고 옛것은 dest/_이전판/<시각> 에 둠 · 복사 뒤 md5 대조.",
         "inputSchema": _p("판 받기", pkg=S("꾸러미 폴더", True), dest=S("내 설치 폴더", True), apply=B("참이면 복사")), "fn": t_pkg},
        {"name": "doc_bake", "description": "문서 굽기 — steps.json(옆 build.py 의 build(spec)) 또는 HTML → Edge 헤드리스로 PDF · 쪽 수 · 넘친 쪽(고정 크기 .page 마다 · 넘친 요소) · 쪽 수 어긋남 · png 참이면 쪽마다 PNG(Edge 화면 찍기). "
                                                "미리보기는 임시 폴더에서 굽고 지움(파일 안 남김) · apply 참일 때만 out(= <share_dir 또는 local_dir>/exports/<폴더> 또는 사용자가 준 절대 경로)에 씀 · 옛것은 out/_이전판 · 공유로 나갈 글은 순찰 꼴로 훑어 걸리면 안 씀.",
         "inputSchema": _p("문서 굽기", src=S("steps.json 또는 .html 경로", True), out=S("exports 아래 폴더 이름 또는 절대 경로(apply 에 필요)"), name=S("PDF 파일 이름(기본: steps.json 의 pdf_name · 아니면 원본 이름)"),
                           builder=S("build(spec) 가 있는 .py(기본: steps.json 옆 build.py)"), selector=S("쪽 요소 CSS(기본 .page)"), png=B("참이면 쪽마다 PNG 도"),
                           png_scale={"type": "number", "description": "PNG 배율 0.5~3(기본 1)"}, apply=B("참이면 씀 · 기본 미리보기")), "fn": t_doc_bake},
        {"name": "memo_rows", "description": "책 메모 줄 뽑기(읽기만) — 공유 book_memo 메모 파일들에서 「넣을 노션 쪽」 칸이 page 와 같거나 page 를 품은 줄을 다(파일 · 줄 번호 · 장 · 쪽 · 알맹이 · 노션에 있나). "
                                                 "「## 쪽 이름」 밑 줄 꼴도 읽음. list 참이면 넣을 쪽 이름과 줄 수 목록.",
         "inputSchema": _p("책 메모 줄", page=S("노션 쪽 이름(같음 또는 포함)"), exact=B("참이면 같은 이름만"), list=B("참이면 넣을 쪽 이름 목록만"),
                           dir=S("메모 폴더(기본: <share_dir 또는 local_dir>/exports/*/book_memo)"), max={"type": "integer", "description": "돌려줄 줄 상한(기본 300)"}), "fn": t_memo_rows},
    ]


def _instructions():
    return ("젤리발 오피스(AI 사무실) 도구 %d개(v%s: %s) · 이 기기 = %s · 공유 = %s. 쓰는 도구는 apply 참일 때만 씀(기본 미리보기). "
            "공유 폴더엔 기밀 원본 · 고객 · 경로 · 비밀번호를 쓰지 않음(걸리면 도구가 안 씀). 결재 도장(실제 「해」)은 그 기기 창에서 %s이."
            % (len(TOOLS), VERSION, " · ".join(t["name"] for t in TOOLS), ARGS.device, SHARED, OWNER))


def _error(mid, code, message):
    _write({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}})


def handle(msg):
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        if isinstance(msg, dict) and msg.get("id") is not None:
            _error(msg.get("id"), -32600, "Invalid Request")
        return
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return
    if method == "ping":
        _write({"jsonrpc": "2.0", "id": mid, "result": {}})
        return
    if method == "initialize":
        want = (msg.get("params") or {}).get("protocolVersion")
        STATE["init"] = True
        _write({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": want if want in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": NAME, "title": "젤리발 오피스 도구", "version": VERSION},
            "instructions": _instructions()}})
        return
    if method not in ("tools/list", "tools/call"):
        _error(mid, -32601, "Method not found: %s" % method)
        return
    if not STATE["init"]:
        _error(mid, -32600, "Invalid Request: initialize 전이다")
        return
    if method == "tools/list":
        _write({"jsonrpc": "2.0", "id": mid, "result": {"tools": [{k: v for k, v in t.items() if k != "fn"} for t in TOOLS]}})
        return
    p = msg.get("params") or {}
    t = next((x for x in TOOLS if x["name"] == p.get("name")), None)
    if t is None:
        _error(mid, -32602, "Unknown tool: %s" % p.get("name"))
        return
    args = p.get("arguments") or {}
    extra = sorted(set(args) - set(t["inputSchema"]["properties"]))
    miss = [k for k in t["inputSchema"]["required"] if k not in args]
    if extra or miss:
        content, is_err = _err("인자 확인 — 모르는 것: %s · 빠진 것: %s · 받는 인자: %s" % (extra, miss, ", ".join(t["inputSchema"]["properties"])))
    else:
        try:
            content, is_err = t["fn"](args)
        except Exception as e:
            log("도구 실패 %s: %s" % (t["name"], e))
            content, is_err = _err("도구 실패: %s: %s" % (type(e).__name__, e))
    _write({"jsonrpc": "2.0", "id": mid, "result": {"content": content, "isError": bool(is_err)}})


def main():
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    TOOLS[:] = tools()
    log("v%s 시작 — 기기 %s · 공유 %s · 도구 %d" % (VERSION, ARGS.device, SHARED, len(TOOLS)))
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8"))
        except Exception as e:
            log("JSON 을 못 읽었다:", e)
            _error(None, -32700, "Parse error")
            continue
        try:
            handle(msg)
        except Exception as e:
            log("처리 실패:", e)
            if isinstance(msg, dict) and msg.get("id") is not None:
                _error(msg.get("id"), -32603, "Internal error: %s" % e)
    log("표준 입력이 닫혔다 — 끝")


if __name__ == "__main__":
    main()
