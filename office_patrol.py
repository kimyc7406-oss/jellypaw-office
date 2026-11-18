"""Patrol - scans the office shared folder for things that must not leak (paths, mail addresses, tokens, private words).
Only "file · line · kind" is reported; the matched text itself is never printed (so the report cannot leak either).
Read-only, standard library only.

Checks: path shapes (drive letter, UNC, mac/linux home), mail, token shapes (sk- · ghp_ · xox · long random strings)
        and an optional device-only word list (--words: client / project names; keep that file OUTSIDE the shared folder).
Usage:
  python -I -X utf8 office_patrol.py                       # shared folder from config.json
  python -I -X utf8 office_patrol.py --shared <folder> --words <list> --json
From code: patrol(shared_folder, words) -> [{file, line, kind, who}].
"""
import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_BYTES = 2 * 1024 * 1024
TEXT_EXT = (".json", ".md", ".txt", ".live")
PATTERNS = [
    ("경로(드라이브)", re.compile(r"(?<![A-Za-z])[A-Za-z]:[\\/]")),
    ("경로(UNC)", re.compile(r"\\\\[A-Za-z0-9_.-]+\\")),
    ("경로(홈)", re.compile(r"/(?:Users|Volumes|home)/[^/\s\"']+")),
    ("메일", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("토큰 꼴", re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16})")),
    ("긴 무작위 글", re.compile(r"(?<![A-Za-z0-9_-])(?=[A-Za-z0-9_-]*[A-Z])(?=[A-Za-z0-9_-]*[a-z])(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{40,}")),
]
OK_URL = re.compile(r"https?://[^\s\"'<>)]+")
WHO = re.compile(r"(?:state|load|s1|s2|g|arcade|grads|ledger|reviews)_([a-z0-9_-]+?)\.(?:json|jsonl|md)$|_([a-z0-9][a-z0-9_-]*)\.json$|[\\/]inbox[\\/]([a-z0-9_-]+)[\\/]")


def load_words(path):
    """한 줄에 낱말 하나 · # 은 주석 · 대소문자 안 가림."""
    if not path or not os.path.exists(path):
        return []
    out = []
    for ln in open(path, encoding="utf-8"):
        w = ln.split("#", 1)[0].strip()
        if len(w) >= 2:
            out.append(w)
    return out


def who_of(rel):
    m = WHO.search(rel.replace("\\", "/"))
    return next((g for g in m.groups() if g), "") if m else ""


def scan_text(text, words):
    """글 한 덩어리 → [(줄 번호, 무엇)] · 같은 줄 같은 종류는 한 번만."""
    hits, low_words = [], [(w, w.lower()) for w in words]
    for n, ln in enumerate(text.splitlines(), 1):
        bare = OK_URL.sub(" ", ln)
        seen = set()
        for kind, rx in PATTERNS:
            src = ln if kind in ("메일", "토큰 꼴") else bare
            if rx.search(src) and kind not in seen:
                seen.add(kind)
                hits.append((n, kind))
        low = ln.lower()
        for i, (w, lw) in enumerate(low_words):
            if lw in low:
                hits.append((n, "기기 안 낱말 #%d" % (i + 1)))
    return hits


def patrol(shared, words=(), max_files=5000):
    """shared(…/ai_office) 아래 글 파일을 훑어 [{file, line, kind, who}]."""
    out, n = [], 0
    for root, dirs, files in os.walk(shared):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fn in files:
            if not fn.lower().endswith(TEXT_EXT):
                continue
            p = os.path.join(root, fn)
            n += 1
            if n > max_files:
                return out
            try:
                if os.path.getsize(p) > MAX_BYTES:
                    continue
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            if fn.lower().endswith(".json"):
                try:
                    text = json.dumps(json.loads(text), ensure_ascii=False, indent=0).replace("\\\\", "\\")
                except ValueError:
                    pass
            rel = os.path.relpath(p, shared).replace("\\", "/")
            for line, kind in scan_text(text, words):
                out.append({"file": rel, "line": line, "kind": kind, "who": who_of(rel)})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="shared folder patrol (read-only; matched text is never printed)")
    ap.add_argument("--config", default="", help="config.json (default: next to this file)")
    ap.add_argument("--shared", default="", help="office shared folder (default: from config)")
    ap.add_argument("--words", default="", help="device-only word list (default <local_dir>/patrol_words.local.txt)")
    ap.add_argument("--who", help="only files written by this device id")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    shared, words_default = a.shared, ""
    if not shared or not a.words:
        import importlib.util
        spec = importlib.util.spec_from_file_location("office_config", os.path.join(HERE, "office_config.py"))
        C = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(C)
        cfg = C.load(a.config or None)
        shared = shared or C.shared_dir(cfg)
        words_default = os.path.join(cfg["local_dir"], "patrol_words.local.txt")
    a.words = a.words or words_default
    if not os.path.isdir(shared):
        print("공유 폴더 없음:", shared)
        return 2
    if os.path.normcase(os.path.abspath(a.words)).startswith(os.path.normcase(os.path.abspath(shared))):
        print("낱말 목록이 공유 폴더 안에 있어 — 기기 안으로 옮겨 줘(목록 자체가 새는 것)")
        return 2
    words = load_words(a.words)
    hits = patrol(shared, words)
    if a.who:
        hits = [h for h in hits if h["who"] == a.who]
    if a.json:
        print(json.dumps({"n": len(hits), "words": len(words), "hits": hits}, ensure_ascii=False))
        return 0
    print("순찰 %s · 낱말 %d개 · 걸림 %d" % (shared, len(words), len(hits)))
    for h in hits:
        print("  %s:%d · %s%s" % (h["file"], h["line"], h["kind"], " · " + h["who"] if h["who"] else ""))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
