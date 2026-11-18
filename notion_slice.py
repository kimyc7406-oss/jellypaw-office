"""Big Notion page slicer - pulls only the needed pieces out of a notion-fetch result file,
so a page of hundreds of thousands of characters is not read in full every time. Standard library only, read-only.

Usage (file = the .txt / .json a fetch tool dropped, or a file holding the page text):
  python -I -X utf8 notion_slice.py 파일 --heads                 # heading (## · ###) list with line numbers
  python -I -X utf8 notion_slice.py 파일 --section "운영 메모"     # the whole section under that heading
  python -I -X utf8 notion_slice.py 파일 --log 10                # top 10 entries of a 📅 change log
  python -I -X utf8 notion_slice.py 파일 --log 3 --since 450     # only log numbers (450) and up
  python -I -X utf8 notion_slice.py 파일 --count "넣은 문장"      # how many times a sentence appears + line numbers
  python -I -X utf8 notion_slice.py 파일 --find "낱말" --around 1  # lines containing a word, with context
After writing: --count shows "new text 1 · old text 0", --section lets you proofread only that section."""
import argparse
import json
import re
import sys

HEAD = re.compile(r"^(#{1,6})\s+(.*)")
LOGNO = re.compile(r"\((\d{2,5})\)")


def load(path):
    """fetch 결과 → 쪽 글. {"text": …} · [{"type":"text","text": …}] · 그냥 글 셋 다 받는다."""
    raw = open(path, encoding="utf-8").read()
    try:
        d = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(d, dict) and isinstance(d.get("text"), str):
        return d["text"]
    if isinstance(d, list):
        parts = [x.get("text", "") for x in d if isinstance(x, dict)]
        joined = "\n".join(parts)
        try:
            inner = json.loads(joined)
            if isinstance(inner, dict) and isinstance(inner.get("text"), str):
                return inner["text"]
        except ValueError:
            pass
        return joined
    return raw


def heads(lines):
    out = []
    for i, ln in enumerate(lines):
        m = HEAD.match(ln)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def section(lines, name):
    """name 이 든 첫 제목부터 같은 급이거나 더 큰 다음 제목 전까지."""
    hs = heads(lines)
    for k, (i, lv, t) in enumerate(hs):
        if name in t:
            end = next((j for j, lv2, _ in hs[k + 1:] if lv2 <= lv), len(lines))
            return i, end
    return None


def log_items(lines, n, since=None):
    """📅 변경 로그 절의 맨 위 항목(「- 」 로 시작하는 줄) n 개."""
    rng = section(lines, "변경 로그")
    if not rng:
        return []
    items = []
    for i in range(rng[0] + 1, rng[1]):
        ln = lines[i]
        if not ln.startswith("- "):
            continue
        if since is not None:
            m = LOGNO.search(ln)
            if not m or int(m.group(1)) < since:
                continue
        items.append((i, ln))
        if len(items) >= n:
            break
    return items


def main(argv=None):
    ap = argparse.ArgumentParser(description="노션 fetch 결과에서 조각만 뽑기(읽기만)")
    ap.add_argument("file")
    ap.add_argument("--heads", action="store_true", help="제목 목록")
    ap.add_argument("--section", help="이 글이 든 제목의 절 통째")
    ap.add_argument("--log", type=int, help="변경 로그 맨 위 N 항목")
    ap.add_argument("--since", type=int, help="--log 와 같이 · 로그 번호 이 수 이상만")
    ap.add_argument("--count", action="append", help="이 문장이 몇 번 나오나(여러 번 줄 수 있음)")
    ap.add_argument("--find", help="이 글이 든 줄")
    ap.add_argument("--around", type=int, default=0, help="--find 앞뒤 줄 수")
    ap.add_argument("--max", type=int, default=4000, help="한 번에 찍을 최대 글자(넘으면 자르고 알림)")
    a = ap.parse_args(argv)
    text = load(a.file)
    lines = text.split("\n")
    out = []
    if a.heads:
        out += ["%5d %s %s" % (i + 1, "#" * lv, t) for i, lv, t in heads(lines)]
    if a.section:
        rng = section(lines, a.section)
        out += ["(절 없음: %s)" % a.section] if not rng else \
            ["── %d–%d 줄 ──" % (rng[0] + 1, rng[1])] + lines[rng[0]:rng[1]]
    if a.log:
        items = log_items(lines, a.log, a.since)
        out += ["(변경 로그 절이 없거나 항목 없음)"] if not items else ["%5d %s" % (i + 1, ln) for i, ln in items]
    for c in a.count or []:
        hits = [i + 1 for i, ln in enumerate(lines) if c in ln]
        out.append("%d번 · 「%s」%s" % (text.count(c), c[:40], (" · 줄 " + ", ".join(map(str, hits[:20]))) if hits else ""))
    if a.find:
        for i, ln in enumerate(lines):
            if a.find in ln:
                lo, hi = max(0, i - a.around), min(len(lines), i + a.around + 1)
                out += ["%5d%s %s" % (j + 1, ">" if j == i else " ", lines[j]) for j in range(lo, hi)]
                if a.around:
                    out.append("")
    if not out:
        ap.print_help()
        return 2
    s = "\n".join(out)
    if len(s) > a.max:
        s = s[:a.max] + "\n… (%d자 중 %d자만 · --max 로 늘림)" % (len(s), a.max)
    sys.stdout.write(s + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
