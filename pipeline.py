"""
pipeline.py — lightweight deal pipeline / CRM.

Tracks which scored properties the team is actually pursuing, with a status and notes,
per project. Purely ADDITIVE: it never touches the scoring or underwriting engine — it
just remembers decisions. Persisted next to each project so it survives restarts (on a
mounted volume).
"""
import os, json, time

STATUSES = ["Watching", "Pursuing", "Offer Made", "Under Contract", "Closed", "Dead"]
_SNAP = ("address", "city", "state", "zip", "tier", "total_score", "avm",
         "market_rent", "gross_yield", "lat", "lon")


def _path(proj_dir, pid):
    return os.path.join(proj_dir, pid, "pipeline.json")

def load(proj_dir, pid):
    p = _path(proj_dir, pid)
    if os.path.exists(p):
        try:
            return json.load(open(p))
        except Exception:
            return {}
    return {}

def save(proj_dir, pid, data):
    os.makedirs(os.path.join(proj_dir, pid), exist_ok=True)
    json.dump(data, open(_path(proj_dir, pid), "w"), indent=2, default=str)

def upsert(proj_dir, pid, prop, status="Watching", note=None):
    """Add a property (or refresh its snapshot); keeps an existing status/note on re-add."""
    d = load(proj_dir, pid); apn = str(prop.get("apn"))
    now = time.strftime("%Y-%m-%d %H:%M")
    e = d.get(apn) or {"status": status or "Watching", "note": "", "added": now}
    e["apn"] = apn
    for k in _SNAP:
        if prop.get(k) is not None:
            e["score" if k == "total_score" else k] = prop.get(k)
    if note is not None:
        e["note"] = note
    e.setdefault("note", "")
    e["updated"] = now
    d[apn] = e; save(proj_dir, pid, d)
    return e

def update(proj_dir, pid, apn, status=None, note=None):
    d = load(proj_dir, pid); apn = str(apn)
    if apn not in d:
        return None
    if status in STATUSES:
        d[apn]["status"] = status
    if note is not None:
        d[apn]["note"] = note
    d[apn]["updated"] = time.strftime("%Y-%m-%d %H:%M")
    save(proj_dir, pid, d)
    return d[apn]

def remove(proj_dir, pid, apn):
    d = load(proj_dir, pid); d.pop(str(apn), None); save(proj_dir, pid, d)

def listing(proj_dir, pid):
    """All entries (newest-updated first) + status counts."""
    d = load(proj_dir, pid)
    rows = sorted(d.values(), key=lambda e: e.get("updated", ""), reverse=True)
    counts = {s: 0 for s in STATUSES}
    for e in d.values():
        counts[e.get("status", "Watching")] = counts.get(e.get("status", "Watching"), 0) + 1
    active = sum(counts[s] for s in ("Watching", "Pursuing", "Offer Made", "Under Contract"))
    return {"rows": rows, "counts": counts, "total": len(d), "active": active, "statuses": STATUSES}
