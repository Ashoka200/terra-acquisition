"""
data_guard.py — fail-closed input-data integrity gate (mirrors the Sales Rate
Agent's ingest gate). Nothing enters the canonical store without passing review.
Validates an incoming ATTOM-style universe export before the engine scores it.
"""
import hashlib, json, os, pandas as pd

REQUIRED = ["apn", "state", "zip", "proptype", "yearbuilt", "sqft", "avm",
            "sale_serial", "corp"]

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def validate(df):
    """Return (ok, report). FAIL-CLOSED on any blocking issue."""
    problems, warns = [], []
    miss = [c for c in REQUIRED if c not in df.columns]
    if miss:
        problems.append(f"missing required columns: {miss}")
        return False, {"blocking": problems, "warnings": warns}
    n = len(df)
    if n == 0:
        problems.append("empty file")
    av = pd.to_numeric(df["avm"], errors="coerce")
    if (av < 0).any():
        problems.append("negative AVM values")
    yb = pd.to_numeric(df["yearbuilt"], errors="coerce")
    if (yb > 2026).sum() > 0:
        warns.append(f"{int((yb>2026).sum())} rows with future year-built")
    if df["apn"].isna().mean() > 0.01:
        problems.append(">1% rows missing APN (identity)")
    sq = pd.to_numeric(df["sqft"], errors="coerce")
    if (sq <= 0).sum() / max(n, 1) > 0.5:
        problems.append(">50% rows non-positive sqft (broken export)")
    return (len(problems) == 0), {"rows": n, "blocking": problems, "warnings": warns}

def review(path, manifest_path="../data/manifest.json"):
    """Two-step: review only. Caller must explicitly approve to import."""
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    ok, rep = validate(df)
    digest = sha256(path)
    seen = {}
    if os.path.exists(manifest_path):
        seen = json.load(open(manifest_path))
    rep["sha256"] = digest
    rep["duplicate"] = digest in seen
    rep["decision"] = "IMPORT-READY" if ok and not rep["duplicate"] else "REJECT"
    return rep

if __name__ == "__main__":
    import sys
    print(json.dumps(review(sys.argv[1]), indent=2))
