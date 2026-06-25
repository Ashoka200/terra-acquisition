"""
parcel.py — auto lot-size & zoning lookup, provider-pluggable.

Honest reality: there is NO single free national parcel+zoning API (parcels are
county-level). So:
  * If REGRID_API_KEY is set -> Regrid Parcel API (lot acreage + zoning + APN).
    (ATTOM can be wired the same way; you already use ATTOM.)
  * Else -> free OSM building-footprint estimate (existing buildings only; this is
    the BUILDING footprint, NOT the lot — labeled as such).
  * Else -> tell the user to enter area manually. Never fabricate a lot size.
"""
import os, math
try:
    import requests
except ImportError:
    requests = None

REGRID = "https://app.regrid.com/api/v2/parcels/point"
UA = {"User-Agent": "Terra-Parcel/1.0"}

def provider():
    return "regrid" if os.environ.get("REGRID_API_KEY") and requests else None

def _shoelace_sf(coords, lat0):
    if len(coords) < 3: return 0
    mlat = 111320; mlon = 111320 * math.cos(math.radians(lat0))
    pts = [(c[0] * mlon, c[1] * mlat) for c in coords]
    a = 0
    for i in range(len(pts)):
        x1, y1 = pts[i]; x2, y2 = pts[(i + 1) % len(pts)]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2 * 10.7639  # m^2 -> sq ft

def osm_footprint(lat, lon):
    if not requests: return None
    q = f"[out:json][timeout:20];way(around:25,{lat},{lon})[building];out geom 1;"
    try:
        r = requests.post("https://overpass-api.de/api/interpreter", data={"data": q}, headers=UA, timeout=30)
        r.raise_for_status(); els = r.json().get("elements", [])
        if not els: return None
        geom = els[0].get("geometry", [])
        coords = [[g["lon"], g["lat"]] for g in geom]
        sf = round(_shoelace_sf(coords, lat))
        lats = [g["lat"] for g in geom]; lons = [g["lon"] for g in geom]
        h = (max(lats) - min(lats)) * 111320
        w = (max(lons) - min(lons)) * 111320 * math.cos(math.radians(lat))
        bbox = h * w * 10.7639
        rect = sf / bbox if bbox else 1
        shape = "rectangular" if rect > 0.85 else ("L-shaped" if rect > 0.65 else "irregular")
        return {"source": "OSM building footprint", "footprint_sf": sf, "shape": shape,
                "note": "Existing BUILDING footprint, not the lot. Set REGRID_API_KEY for true lot size + zoning."}
    except Exception:
        return None

def regrid(lat, lon):
    key = os.environ.get("REGRID_API_KEY")
    try:
        r = requests.get(REGRID, params={"lat": lat, "lon": lon, "token": key}, headers=UA, timeout=20)
        if r.status_code in (401, 403):
            return {"error": f"Regrid rejected the key (HTTP {r.status_code}) — check REGRID_API_KEY."}
        r.raise_for_status(); j = r.json()
        feats = (j.get("parcels", {}) or {}).get("features") or j.get("features") or []
        if not feats: return None
        props = feats[0].get("properties", {})
        p = props.get("fields", props) or {}
        acre = p.get("ll_gisacre") or p.get("gisacre") or p.get("acres") or p.get("ll_gissqft") and None
        area_sf = round(float(acre) * 43560) if acre else (round(float(p["ll_gissqft"])) if p.get("ll_gissqft") else None)
        return {"source": "Regrid", "area_sf": area_sf, "shape": "rectangular",
                "zoning": p.get("zoning") or p.get("zoning_description"),
                "address": p.get("address"), "apn": p.get("parcelnumb")}
    except Exception as e:
        return {"error": str(e)[:90]}

def lookup(lat, lon):
    if provider():
        rg = regrid(lat, lon)
        if rg and not rg.get("error"): return rg
        if rg and rg.get("error"): return {"source": "regrid", "note": rg["error"]}
    f = osm_footprint(lat, lon)
    if f: return f
    return {"source": None,
            "note": "No free lot-size source at this point (likely an empty/unbuilt parcel). Enter parcel area "
                    "manually, or set REGRID_API_KEY (regrid.com) / wire ATTOM for lot size + zoning."}
