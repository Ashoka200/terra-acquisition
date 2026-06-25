"""
site_intel.py — Site Intelligence & Highest-&-Best-Use (HBU) screening for a point.

Reliable, free sources:
  * OpenStreetMap Overpass  -> nearby facilities + distance to each (no key)
  * FEMA National Flood Hazard Layer -> flood zone at the point (official, no key)

HBU recommendation is a TRANSPARENT market-signal rubric (not parcel size): each
candidate use is scored from the surrounding signals, with the reasons shown.
Honest limits: building massing / room counts need the parcel polygon + zoning;
litigation needs paid court data — both flagged, never faked.
"""
import math, os
try:
    import requests
except ImportError:
    requests = None

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
FEMA = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
UA = {"User-Agent": "Terra-SiteIntel/1.0 (United Brothers)"}

def _hav(la1, lo1, la2, lo2):
    R = 6371000; p = math.pi / 180
    a = (math.sin((la2-la1)*p/2)**2 + math.cos(la1*p)*math.cos(la2*p)*math.sin((lo2-lo1)*p/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

def _classify(t):
    a = t.get("amenity"); s = t.get("shop"); to = t.get("tourism")
    le = t.get("leisure"); pt = t.get("public_transport"); rw = t.get("railway")
    if a in ("school", "college", "university", "kindergarten"): return "school"
    if a in ("hospital", "clinic", "doctors"): return "hospital"
    if a == "pharmacy": return "pharmacy"
    if a in ("restaurant", "fast_food", "cafe"): return "restaurant"
    if a == "fuel": return "fuel"
    if a in ("bank", "atm"): return "bank"
    if s in ("supermarket", "convenience", "grocery", "greengrocer"): return "grocery"
    if s in ("mall", "department_store"): return "retail"
    if s: return "shop"
    if to in ("hotel", "motel"): return "hotel"
    if to in ("attraction", "museum", "theme_park", "casino"): return "attraction"
    if le == "park": return "park"
    if pt == "station" or rw == "station" or a == "bus_station": return "transit"
    return None

def overpass(lat, lon, radius=4000):
    if not requests: return {"ok": False}
    q = (f"[out:json][timeout:30];("
         f'node(around:{radius},{lat},{lon})[amenity~"^(school|college|university|hospital|clinic|pharmacy|restaurant|fast_food|cafe|fuel|bank|bus_station)$"];'
         f'node(around:{radius},{lat},{lon})[shop~"^(supermarket|convenience|grocery|mall|department_store)$"];'
         f'node(around:{radius},{lat},{lon})[tourism~"^(hotel|motel|attraction|museum|casino|theme_park)$"];'
         f"node(around:{radius},{lat},{lon})[leisure=park];"
         f"node(around:{radius},{lat},{lon})[public_transport=station];"
         f'way(around:1600,{lat},{lon})[highway~"^(motorway|trunk|primary)$"];'
         f");out center 800;")
    data = None
    for url in OVERPASS:
        try:
            r = requests.post(url, data={"data": q}, headers=UA, timeout=45); r.raise_for_status()
            data = r.json(); break
        except Exception:
            continue
    if not data: return {"ok": False}
    near, cnt, road = {}, {}, None
    for el in data.get("elements", []):
        t = el.get("tags", {})
        if el["type"] == "way":
            c = el.get("center")
            if c and t.get("highway") in ("motorway", "trunk", "primary"):
                d = _hav(lat, lon, c["lat"], c["lon"]); road = d if road is None else min(road, d)
            continue
        cat = _classify(t)
        if not cat: continue
        d = _hav(lat, lon, el.get("lat"), el.get("lon"))
        cnt[cat] = cnt.get(cat, 0) + 1
        if cat not in near or d < near[cat]["dist"]:
            near[cat] = {"name": t.get("name") or cat.title(), "dist": round(d)}
    if road is not None:
        near["major_road"] = {"name": "major road", "dist": round(road)}; cnt["major_road"] = 1
    return {"ok": True, "near": near, "count": cnt}

def flood(lat, lon):
    if not requests: return {"zone": None, "risk": "unknown"}
    try:
        r = requests.get(FEMA, params={"geometry": f"{lon},{lat}", "geometryType": "esriGeometryPoint",
            "inSR": 4326, "spatialRel": "esriSpatialRelIntersects", "outFields": "FLD_ZONE,ZONE_SUBTY",
            "returnGeometry": "false", "f": "json"}, headers=UA, timeout=20)
        r.raise_for_status(); feats = r.json().get("features", [])
        if not feats: return {"zone": "Unmapped", "risk": "not in FEMA map", "sfha": False}
        z = feats[0]["attributes"].get("FLD_ZONE", "") or "X"
        sfha = z[:1] in ("A", "V")
        return {"zone": z, "subtype": feats[0]["attributes"].get("ZONE_SUBTY"),
                "risk": "High — Special Flood Hazard Area" if sfha else f"Minimal (zone {z})", "sfha": sfha}
    except Exception as e:
        return {"zone": None, "risk": "lookup failed", "err": str(e)[:60], "sfha": False}

def _has(near, cat, m): return cat in near and near[cat]["dist"] <= m
def _n(c, cat): return c.get(cat, 0)

def recommend(near, count, fl):
    uses = []; sfha = fl.get("sfha")
    def add(use, s, w): uses.append({"use": use, "score": max(0, min(100, round(s))), "why": w})
    # Hotel
    s, w = 22, []
    if _has(near, "major_road", 2000): s += 26; w.append("near a major road/highway (access)")
    if _has(near, "attraction", 3000): s += 20; w.append("attractions/casino/museum nearby (demand)")
    if _n(count, "restaurant") >= 5: s += 12; w.append(f"{_n(count,'restaurant')} restaurants nearby")
    h = _n(count, "hotel")
    if 1 <= h <= 6: s += 14; w.append("proven hotel demand, not saturated")
    elif h > 12: s -= 18; w.append(f"{h} hotels nearby — saturated")
    add("Hotel / Lodging", s, w)
    # Gas / C-store
    s, w = 22, []
    if _has(near, "major_road", 800): s += 34; w.append("on/near a major road (traffic)")
    elif _has(near, "major_road", 2000): s += 15; w.append("major road within 2 km")
    f = _n(count, "fuel")
    if f == 0: s += 18; w.append("no competing gas stations nearby")
    elif f >= 3: s -= 20; w.append(f"{f} competing stations nearby")
    if _n(count, "grocery") + _n(count, "restaurant") >= 4: s += 10; w.append("commercial activity nearby")
    add("Gas Station / C-Store", s, w)
    # Residential subdivision
    s, w = 22, []
    if _has(near, "school", 1500): s += 20; w.append("school within 1.5 km")
    if _has(near, "grocery", 1500): s += 18; w.append("grocery within 1.5 km")
    if _has(near, "park", 1500): s += 12; w.append("park nearby")
    if _has(near, "transit", 1200): s += 10; w.append("transit nearby")
    if _has(near, "hospital", 4000): s += 8; w.append("healthcare access")
    if _has(near, "major_road", 150): s -= 12; w.append("highway adjacency (noise)")
    if sfha: s -= 25; w.append("in a FEMA flood zone")
    add("Residential / SFR subdivision", s, w)
    # Multifamily
    s, w = 22, []
    if _has(near, "transit", 1000): s += 22; w.append("transit-accessible")
    if _has(near, "grocery", 1200): s += 15; w.append("grocery nearby")
    if _n(count, "restaurant") + _n(count, "bank") >= 6: s += 14; w.append("urban/commercial setting")
    if _has(near, "school", 2000): s += 9; w.append("schools nearby")
    if sfha: s -= 20; w.append("flood zone")
    add("Multifamily / Apartments", s, w)
    # Retail / service
    s, w = 22, []
    shops = _n(count, "shop") + _n(count, "retail") + _n(count, "grocery")
    if shops >= 10: s += 30; w.append(f"{shops} retail nearby (commercial corridor)")
    elif shops >= 4: s += 15; w.append("some retail nearby")
    if _has(near, "major_road", 600): s += 20; w.append("road visibility/traffic")
    add("Retail / Service (tyre, smoke, etc.)", s, w)
    uses.sort(key=lambda x: -x["score"])
    return uses

def analyze(lat, lon):
    o = overpass(lat, lon)
    fl = flood(lat, lon)
    if not o.get("ok"):   # don't fake an HBU on missing data
        return {"lat": lat, "lon": lon, "flood": fl, "facilities": [], "hbu": [], "counts": {},
                "error": "Couldn't load the surroundings (the free mapping service was busy) — try again in a moment.",
                "risks": [{"type": "Flood", "level": fl.get("risk", "?"), "detail": f"zone {fl.get('zone')}", "source": "FEMA NFHL"}]}
    near, count = o.get("near", {}), o.get("count", {})
    hbu = recommend(near, count, fl)
    facilities = [{"cat": k, "name": v["name"], "dist_m": v["dist"], "dist_mi": round(v["dist"]/1609, 2)}
                  for k, v in sorted(near.items(), key=lambda x: x[1]["dist"])]
    risks = []
    if fl.get("sfha"):
        risks.append({"type": "Flood", "level": "High",
                      "detail": f"FEMA Special Flood Hazard Area — zone {fl.get('zone')}", "source": "FEMA NFHL"})
    risks.append({"type": "Litigation/title", "level": "Check manually",
                  "detail": "No reliable free source — pull county records / a title search before LOI.", "source": "—"})
    return {"lat": lat, "lon": lon, "flood": fl, "facilities": facilities, "hbu": hbu,
            "counts": count, "risks": risks,
            "notes": "HBU = market-signal screening, not a feasibility study. Building massing / room counts "
                     "require the parcel polygon + zoning setbacks (next step)."}


if __name__ == "__main__":
    import json
    r = analyze(36.0337, -115.0413)  # Henderson/Las Vegas, NV
    print("flood:", r["flood"])
    print("top HBU:", [(u["use"], u["score"]) for u in r["hbu"]])
    print("best:", r["hbu"][0]["use"], "—", "; ".join(r["hbu"][0]["why"][:3]))
    print("facilities (nearest):", [(f["cat"], f["dist_mi"]) for f in r["facilities"][:8]])
