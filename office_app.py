"""Shared app folder - publish · verify · run · pull, so every device can run the same version from the shared folder.

App folder: <share_dir>/ai_office/app (code · web · images) + manifest.json (md5 per file · ver)
- run: start the server straight from the app folder - checks every file against manifest.json first and refuses on mismatch
- pull: (for devices that must not run code from a shared drive) verify, then copy the whole app into a local folder and run it there
- device data (chat_local.json · *_local* · load_local.json · patrol_words.local.txt) stays outside the app folder (--local-dir)
- publish moves changed/removed files to app_이전판/<time>/ (never deletes)

Usage:
  python -I -X utf8 office_app.py publish --src . --share <share_dir>
  python -I -X utf8 office_app.py verify  --share <share_dir>
  python -I -X utf8 office_app.py run     --share <share_dir> --local-dir <device-only folder> -- --device <id>
  python -I -X utf8 office_app.py pull    --share <share_dir> --dest <local folder> --dry   (list only; drop --dry after reading it)
The md5 check catches half-written copies and accidents - someone who can change both files and manifest passes it,
so a cautious device reads the --dry list before pulling."""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

MANIFEST = "manifest.json"
SKIP_DIRS = {"__pycache__", "mcp", "shared_local", "shares_local", "approvals_local"}
SKIP_FILE = ("_local", "config.json")
CODE_EXT = (".py", ".js", ".html", ".css", ".json", ".mjs")


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def wanted(rel):
    """판에 넣을 파일인가 — 옛 판 · 기기 자료 · 캐시 · 그림 원본은 뺌"""
    parts = rel.replace("\\", "/").split("/")
    if parts[0] == "data" or any(p in SKIP_DIRS or p.startswith("_이전판") or p.startswith(".") for p in parts):
        return False
    name = parts[-1]
    return not (name.startswith("_이전판") or any(s in name for s in SKIP_FILE) or name.endswith((".pyc", ".part", ".bak")))


def scan(root, only_wanted=True):
    out = {}
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not (only_wanted and not wanted(os.path.relpath(os.path.join(d, x), root)))]
        for f in files:
            rel = os.path.relpath(os.path.join(d, f), root).replace("\\", "/")
            if rel == MANIFEST or (only_wanted and not wanted(rel)):
                continue
            out[rel] = md5(os.path.join(d, f))
    return out


def ver_of(files):
    return hashlib.md5("\n".join("%s:%s" % kv for kv in sorted(files.items())).encode("utf-8")).hexdigest()[:8]


def app_dir(a):
    return a.app or os.path.join(a.share, "ai_office", "app")


def put(src, dst, tries=20):
    """임시 이름에 쓰고 바꿔 닮 · 다른 기기가 읽는 중이라 막히면(WinError 5 · 32) 잠깐씩 다시"""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    shutil.copyfile(src, tmp)
    for i in range(tries):
        try:
            os.replace(tmp, dst)
            return
        except PermissionError:
            time.sleep(0.25 * (i + 1))
    raise PermissionError("바꿔 달기 막힘: %s" % dst)


def keep_old(root, rel, bak):
    """옛 파일은 지우지 않고 bak/<rel> 로 옮김"""
    s = os.path.join(root, rel)
    if os.path.isfile(s):
        d = os.path.join(bak, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.move(s, d)


def safe_key(r):
    """manifest 경로 키 — 판 폴더 안 상대 경로만(pull 이 키를 그대로 dest 에 붙임)"""
    parts = r.split("/")
    return bool(r) and "\\" not in r and ":" not in r and not r.startswith("/") and ".." not in parts and "" not in parts


def verify(app, quiet=False):
    """manifest 와 판 폴더 대조 — 빠짐 · 다름 · 남는 코드 파일이 하나라도 있으면 실패"""
    mp = os.path.join(app, MANIFEST)
    try:
        man = json.load(open(mp, encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "why": "manifest 못 읽음: %s" % e}
    files = man.get("files") or {}
    badkey = [r for r in files if not safe_key(r)]
    if badkey:
        res = {"ok": False, "why": "manifest 에 이상한 경로 키(.. · 절대 경로 · : · \\)", "bad_keys": badkey[:20]}
        if not quiet:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        return res
    have = scan(app, only_wanted=False)
    missing = [r for r in files if r not in have]
    diff = [r for r in files if r in have and have[r] != files[r]]
    sysname = lambda r: r.split("/")[-1].startswith("._") or r.split("/")[-1] in (".DS_Store", "Thumbs.db", "desktop.ini")
    extra = [r for r in have if r not in files and wanted(r) and not sysname(r)]
    extra_code = [r for r in extra if r.endswith(CODE_EXT)]
    ok = not missing and not diff and not extra_code and ver_of(files) == man.get("ver")
    res = {"ok": ok, "ver": man.get("ver"), "t": man.get("t"), "by": man.get("by"), "n": len(files),
           "missing": missing[:20], "diff": diff[:20], "extra_code": extra_code[:20], "extra_other": len(extra) - len(extra_code)}
    if ver_of(files) != man.get("ver"):
        res["why"] = "manifest ver 가 파일 목록과 안 맞음"
    if not quiet:
        print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def publish(a):
    src, app = os.path.abspath(a.src), app_dir(a)
    new = scan(src)
    if "office_server.py" not in new or "web/index.html" not in new:
        sys.exit("src 에 office_server.py · web/index.html 이 없음: %s" % src)
    os.makedirs(app, exist_ok=True)
    old = scan(app, only_wanted=False)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(os.path.dirname(app), "app_이전판", stamp)
    changed = [r for r in new if old.get(r) != new[r]]
    gone = [r for r in old if r not in new and not r.endswith(".part")]
    if a.dry:
        print(json.dumps({"dry": True, "changed": len(changed), "gone": gone[:20], "n": len(new), "ver": ver_of(new)}, ensure_ascii=False, indent=1))
        return
    for r in changed:
        if r in old:
            os.makedirs(os.path.dirname(os.path.join(bak, r)), exist_ok=True)
            shutil.copy2(os.path.join(app, r), os.path.join(bak, r))
        put(os.path.join(src, r), os.path.join(app, r))
    for r in gone:
        keep_old(app, r, bak)
    man = {"ver": ver_of(new), "t": time.time(), "by": a.device or "", "src_note": a.note or "", "files": new}
    with open(os.path.join(app, MANIFEST + ".part"), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=0)
    os.replace(os.path.join(app, MANIFEST + ".part"), os.path.join(app, MANIFEST))
    res = verify(app, quiet=True)
    print(json.dumps({"published": res["ok"], "ver": man["ver"], "n": len(new), "changed": len(changed), "moved_old": len(gone),
                      "backup": bak if (changed or gone) and os.path.isdir(bak) else "", "verify": res}, ensure_ascii=False, indent=1))
    sys.exit(0 if res["ok"] else 1)


def run(a, rest):
    app = app_dir(a)
    res = verify(app, quiet=True)
    if not res["ok"]:
        print(json.dumps(dict(res, note="판 폴더가 manifest 와 안 맞아 안 켬 — 판을 올린 기기에 알려 줘"), ensure_ascii=False, indent=1))
        sys.exit(2)
    if not a.local_dir:
        sys.exit("--local-dir 이 필요해(기기 자료가 공유 판 폴더에 쓰이지 않게)")
    if os.path.abspath(a.local_dir).lower().startswith(os.path.abspath(app).lower()):
        sys.exit("--local-dir 이 판 폴더 안이면 안 돼")
    cmd = [sys.executable, "-B", "-I", "-X", "utf8", os.path.join(app, "office_server.py"), "--local-dir", a.local_dir, "--share", a.share] + rest
    print("판 %s 대조 통과 · 켬: %s" % (res["ver"], " ".join(cmd[4:])), flush=True)
    sys.exit(subprocess.call(cmd))


def pull(a):
    app, dest = app_dir(a), os.path.abspath(a.dest)
    res = verify(app, quiet=True)
    if not res["ok"]:
        print(json.dumps(dict(res, note="판 폴더가 manifest 와 안 맞아 안 가져옴"), ensure_ascii=False, indent=1))
        sys.exit(2)
    man = json.load(open(os.path.join(app, MANIFEST), encoding="utf-8"))
    files = man["files"]
    if a.dry:
        todo = [r for r, h in files.items() if not (os.path.isfile(os.path.join(dest, r)) and md5(os.path.join(dest, r)) == h)]
        code = [r for r in todo if r.endswith(CODE_EXT)]
        print(json.dumps({"dry": True, "ver": man["ver"], "will_copy": len(todo), "code": code, "other": len(todo) - len(code),
                          "note": "code 목록을 읽은 뒤 --dry 빼고 다시"}, ensure_ascii=False, indent=1))
        return
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = os.path.join(dest, "_이전판", "app_" + stamp)
    took = 0
    for r, h in files.items():
        d = os.path.join(dest, r)
        if os.path.isfile(d) and md5(d) == h:
            continue
        if os.path.isfile(d):
            os.makedirs(os.path.dirname(os.path.join(bak, r)), exist_ok=True)
            shutil.copy2(d, os.path.join(bak, r))
        put(os.path.join(app, r), d)
        took += 1
    with open(os.path.join(dest, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=0)
    bad = [r for r, h in files.items() if not os.path.isfile(os.path.join(dest, r)) or md5(os.path.join(dest, r)) != h]
    print(json.dumps({"pulled": not bad, "ver": man["ver"], "copied": took, "bad": bad[:20], "backup": bak if os.path.isdir(bak) else ""},
                     ensure_ascii=False, indent=1))
    sys.exit(0 if not bad else 1)


def main():
    argv = sys.argv[1:]
    rest = []
    if "--" in argv:
        i = argv.index("--")
        argv, rest = argv[:i], argv[i + 1:]
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["publish", "verify", "run", "pull"])
    ap.add_argument("--share", required=True, help="share_dir (the folder that holds ai_office/)")
    ap.add_argument("--app", default="", help="판 폴더(비우면 <share>/ai_office/app)")
    ap.add_argument("--src", default=os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--device", default="", help="who published (for the manifest)")
    ap.add_argument("--local-dir", default="")
    ap.add_argument("--dest", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "publish":
        publish(a)
    elif a.cmd == "verify":
        sys.exit(0 if verify(app_dir(a))["ok"] else 1)
    elif a.cmd == "run":
        run(a, rest)
    elif a.cmd == "pull":
        if not a.dest:
            sys.exit("--dest 가 필요해")
        pull(a)


if __name__ == "__main__":
    main()
