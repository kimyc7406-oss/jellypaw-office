"""Shared config loader for the Jellypaw Office server, MCP server and helper scripts.

The config file is plain JSON (see config.example.json). Every key is optional:
a missing file means "one device, single-PC mode, everything under ./data".
Relative paths in the file are resolved against the folder that holds the file.
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ANIMALS = ("bear", "cat", "rabbit", "squirrel")
ANIMAL_KO = {"bear": "곰", "cat": "고양이", "rabbit": "토끼", "squirrel": "다람쥐"}
ANIMAL_EMOJI = {"bear": "🐻", "cat": "🐱", "rabbit": "🐰", "squirrel": "🐿️"}
RESERVED = {"all", "boss", "intern", "worker", "kk", "archive", "meeting", "lounge", "pantry", "arcade", "off"}
ID_RX = re.compile(r"^[a-z0-9][a-z0-9_-]{0,15}$")

DEFAULTS = {
    "devices": [{"id": "me", "label": "나", "animal": "bear"}],
    "leader": None,
    "share_dir": None,
    "local_dir": "./data",
    "private_devices": [],
    "owner_title": "감독",
    "checklist_path": None,
    "lessons_path": None,
    "weather": None,
    "edge_path": None,
    "worker_dirs": [],
    "meet_at": ["11:00", "19:00"],
    "port": 8765,
    "play": True,
}


def default_path():
    return os.environ.get("JELLYPAW_CONFIG") or os.path.join(HERE, "config.json")


def _abs(base, p):
    if not p:
        return None
    p = os.path.expanduser(str(p))
    return os.path.normpath(p if os.path.isabs(p) else os.path.join(base, p))


def load(path=None):
    """Read the config file (missing file = defaults) and return a normalised dict."""
    path = path or default_path()
    raw = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise SystemExit("config: top level must be a JSON object: %s" % path)
    base = os.path.dirname(os.path.abspath(path))
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in raw.items() if v is not None or k in ("share_dir", "weather")})
    devs, seen = [], set()
    for i, d in enumerate(cfg.get("devices") or DEFAULTS["devices"]):
        if isinstance(d, str):
            d = {"id": d}
        did = str(d.get("id", "")).strip().lower()
        if not ID_RX.match(did) or did in RESERVED or did in seen:
            raise SystemExit("config: bad or duplicate device id %r (a-z 0-9 _ -, max 16, not %s)" % (did, ", ".join(sorted(RESERVED))))
        seen.add(did)
        animal = d.get("animal") if d.get("animal") in ANIMALS else ANIMALS[i % len(ANIMALS)]
        devs.append({"id": did, "label": str(d.get("label") or did)[:12], "animal": animal,
                     "session_names": [str(x) for x in (d.get("session_names") or [])]})
    if not devs:
        raise SystemExit("config: devices is empty")
    cfg["devices"] = devs
    ids = [d["id"] for d in devs]
    cfg["leader"] = cfg.get("leader") if cfg.get("leader") in ids else ids[0]
    cfg["private_devices"] = [x for x in (cfg.get("private_devices") or []) if x in ids]
    cfg["share_dir"] = _abs(base, cfg.get("share_dir"))
    cfg["local_dir"] = _abs(base, cfg.get("local_dir") or "./data")
    for k in ("checklist_path", "lessons_path", "edge_path"):
        cfg[k] = _abs(base, cfg.get(k))
    cfg["worker_dirs"] = [_abs(base, x) for x in (cfg.get("worker_dirs") or []) if x]
    w = cfg.get("weather")
    if isinstance(w, dict) and isinstance(w.get("lat"), (int, float)) and isinstance(w.get("lon"), (int, float)):
        cfg["weather"] = {"lat": float(w["lat"]), "lon": float(w["lon"]), "timezone": str(w.get("timezone") or "auto")}
    else:
        cfg["weather"] = None
    cfg["meet_at"] = [x for x in (cfg.get("meet_at") or []) if re.fullmatch(r"\d\d:\d\d", str(x))]
    cfg["owner_title"] = str(cfg.get("owner_title") or "감독")[:12]
    cfg["port"] = int(cfg.get("port") or 8765)
    cfg["play"] = bool(cfg.get("play", True))
    cfg["path"] = os.path.abspath(path)
    return cfg


def device_ids(cfg):
    return [d["id"] for d in cfg["devices"]]


def device(cfg, did):
    return next((d for d in cfg["devices"] if d["id"] == did), None)


def label(cfg, did):
    d = device(cfg, did)
    return d["label"] if d else did


def shared_dir(cfg):
    """Folder all devices read and write. Single-PC mode (share_dir null) keeps it under local_dir."""
    if cfg["share_dir"]:
        return os.path.join(cfg["share_dir"], "ai_office")
    return os.path.join(cfg["local_dir"], "shared")


def pick_device(cfg, wanted):
    ids = device_ids(cfg)
    if not wanted:
        return ids[0]
    if wanted not in ids:
        raise SystemExit("--device %r is not in config devices: %s" % (wanted, ", ".join(ids)))
    return wanted
