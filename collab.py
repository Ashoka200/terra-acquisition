"""collab.py — team discussions. Threads keyed to a property/project, with @mentions."""
import os, json, time
DATA = os.environ.get("RE_DATA", os.path.join(os.path.dirname(__file__), "..", "data"))
PATH = os.path.join(DATA, "collab.json")

def _load():
    try: return json.load(open(PATH))
    except Exception: return {}

def _save(d): json.dump(d, open(PATH, "w"), indent=2)

def _ts(): return time.strftime("%Y-%m-%d %H:%M")

def mentions(text):
    import re
    return re.findall(r"@([A-Za-z0-9_.@-]+)", text or "")

def add_message(key, author, text):
    d = _load(); d.setdefault(key, [])
    d[key].append({"id": len(d[key]) + 1, "author": author, "text": text,
                   "mentions": mentions(text), "ts": _ts()})
    _save(d); return d[key][-1]

def get_thread(key): return _load().get(key, [])

def recent(limit=20):
    d = _load(); out = []
    for key, msgs in d.items():
        for m in msgs: out.append({**m, "key": key})
    return sorted(out, key=lambda x: x["ts"], reverse=True)[:limit]
