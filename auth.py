"""
auth.py — optional session auth + capability gating (deploy-ready, mirrors the
Sales Rate Agent's RBAC at a lighter weight). OFF by default so local dev and the
static preview need no login. Turn on with AUTH_ENABLED=1.

Sign-in methods:
  * users.json (data/users.json) with PBKDF2 hashes — managed via add_user()
  * bootstrap admin via ADMIN_EMAILS + ADMIN_PASSWORD env (first-run access)
Capabilities: read · underwrite · chat · admin.
"""
import os, json
from flask import session, request, redirect, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

DATA = os.environ.get("RE_DATA", os.path.join(os.path.dirname(__file__), "..", "data"))
USERS_PATH = os.path.join(DATA, "users.json")
DEFAULT_CAPS = ["read", "underwrite", "chat"]

def enabled():
    return os.environ.get("AUTH_ENABLED", "").lower() in ("1", "true", "yes")

def _admins():
    return {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}

def _load():
    try:
        return json.load(open(USERS_PATH))
    except Exception:
        return {}

def verify(email, password):
    email = (email or "").strip().lower()
    u = _load().get(email)
    if u and password and check_password_hash(u.get("hash", ""), password):
        if u.get("status", "active") != "active":
            return {"_status": u.get("status", "pending")}   # blocked: pending/disabled
        return {"email": email, "name": u.get("name", email), "role": u.get("role", "Analyst"),
                "caps": u.get("caps", DEFAULT_CAPS)}
    if email in _admins() and password and password == os.environ.get("ADMIN_PASSWORD"):
        return {"email": email, "name": "Admin", "role": "Admin", "caps": DEFAULT_CAPS + ["admin"]}
    return None

def register(email, password, name=""):
    email = (email or "").strip().lower()
    if not email or not password or len(password) < 8:
        return None, "Email and an 8+ char password are required."
    us = _load()
    if email in us:
        return None, "An account with that email already exists."
    auto = email in _admins()
    us[email] = {"hash": generate_password_hash(password), "name": name or email,
                 "role": "Admin" if auto else "Analyst",
                 "caps": DEFAULT_CAPS + (["admin"] if auto else []),
                 "status": "active" if auto else "pending",
                 "created": __import__("time").strftime("%Y-%m-%d")}
    os.makedirs(DATA, exist_ok=True); json.dump(us, open(USERS_PATH, "w"), indent=2)
    return us[email], None

def list_users():
    return [{"email": e, "name": u.get("name", e), "role": u.get("role", "Analyst"),
             "status": u.get("status", "active"), "created": u.get("created", "")}
            for e, u in _load().items()]

def set_user(email, status=None, role=None):
    us = _load(); e = email.strip().lower()
    if e not in us: return False
    if status: us[e]["status"] = status
    if role:
        us[e]["role"] = role
        us[e]["caps"] = DEFAULT_CAPS + (["admin"] if role == "Admin" else [])
    json.dump(us, open(USERS_PATH, "w"), indent=2); return True

def current():
    return session.get("user")

def has_cap(cap):
    if not enabled():
        return True
    u = current()
    return bool(u and cap in u.get("caps", []))

def add_user(email, password, role="Analyst", caps=None):
    us = _load()
    us[email.strip().lower()] = {"hash": generate_password_hash(password),
                                 "role": role, "caps": caps or DEFAULT_CAPS}
    os.makedirs(DATA, exist_ok=True)
    json.dump(us, open(USERS_PATH, "w"), indent=2)
    return us[email.strip().lower()]

if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 3:
        add_user(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "Analyst")
        print("user added:", sys.argv[1])
    else:
        print("usage: python auth.py <email> <password> [role]")
