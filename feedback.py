"""feedback.py — thumbs + free-text feedback with admin aggregation."""
import os, json, time
DATA = os.environ.get("RE_DATA", os.path.join(os.path.dirname(__file__), "..", "data"))
PATH = os.path.join(DATA, "feedback.jsonl")

def add(rating, comment="", context="", user="anon"):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M"), "rating": rating,
           "comment": (comment or "")[:1000], "context": context[:120], "user": user}
    with open(PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return rec

def all_items():
    if not os.path.exists(PATH): return []
    out = []
    for line in open(PATH, encoding="utf-8"):
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except Exception: pass
    return out

def summary():
    items = all_items()
    up = sum(1 for i in items if i["rating"] == "up")
    down = sum(1 for i in items if i["rating"] == "down")
    return {"total": len(items), "up": up, "down": down,
            "csat": round(up / (up + down), 3) if (up + down) else None,
            "recent": list(reversed(items))[:25]}
