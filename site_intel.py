"""
site_intel.py — Site Intelligence, Highest-&-Best-Use (HBU) recommendation, and
build-out massing for ANY use, from a lat/lon. Decision is driven by MARKET
SIGNALS (nearby demand generators + facilities), not parcel size.

Reliable, free sources:
  * OpenStreetMap Overpass  -> facilities + demand generators + distance to each
  * FEMA National Flood Hazard Layer -> flood zone at the point (official)

HBU scores candidate uses (Hotel, Gas/C-store, Residential, Multifamily, Retail,
Tyre/Auto, Smoke/Convenience, QSR pad, Self-storage, Medical office) from the
surrounding signals, showing WHICH facilities each use needs and their distance.

massing() estimates keys/units a parcel supports — with vs without ground-floor
retail, plus an adjacent-lease note — from user-supplied parcel area, shape,
FAR/height/coverage (no parcel feed required).

Honest limits: litigation/title & environmental need paid data (flagged, never
faked). Real parcel polygon + zoning sharpen massing; manual inputs give a scenario.
"""
import math, os
try:
    import requests
except ImportError:
    requests = None

OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
FEMA = "https://hazards.fema.gov/arcgis/rest/services/public/NFHL/MapServer/28/query"
UA = {"User-Agent": "Terra-SiteIntel/2.0 (United Brothers)"}

def _hav(la1, lo1, la2, lo2):
    R = 6371000; p = math.pi / 180
    a = (math.sin((la2-la1)*p/2)**2 + math.cos(la1*p)*math.cos(la2*p)*math.sin((lo2-lo1)*p/2)**2)
    return 2 * R * math.asin(math.sqrt(a))

def _classify(t):
    a = t.get("amenity"); s = t.get("shop"); to = t.get("tourism")
    le = t.get("leisure"); pt = t.get("public_transport"); rw = t.get("railway")
    aw = t.get("aeroway"); of = t.get("office")
    if aw == "aerodrome": return "airport"
    if a in ("university", "college"): return "university"
    if a in ("school", "kindergarten"): return "school"
    if a in ("hospital", "clinic", "doctors"): return "hospital"
    if a == "pharmacy": return "pharmacy"
    if a in ("restaurant", "fast_food", "cafe"): return "restaurant"
    if a == "fuel": return "fuel"
    if a in ("bank", "atm"): return "bank"
    if a == "casino": return "casino"
    if a in ("conference_centre", "exhibition_centre"): return "convention"
    if a == "cinema": return "cinema"
    if a == "car_wash": return "car_wash"
    if s in ("supermarket", "convenience", "grocery", "greengrocer"): return "grocery"
    if s in ("mall", "department_store"): return "mall"
    if s in ("car_repair", "tyres"): return "auto"
    if s in ("tobacco", "e-cigarette", "alcohol"): return "smoke"
    if s: return "retail"
    if to in ("hotel", "motel"): return "hotel"
    if to in ("attraction", "museum", "theme_park"): return "attraction"
    if le == "stadium": return "stadium"
    if le == "park": return "park"
    if of: return "office"
    if pt == "station" or rw == "station" or a == "bus_station": return "transit"
    return None

def overpass(lat, lon, radius=4000):
    if not requests: return {"ok": False}
    q = (f"[out:json][timeout:30];("
         f'node(around:{radius},{lat},{lon})[amenity~"^(school|kindergarten|college|university|hospital|clinic|pharmacy|restaurant|fast_food|cafe|fuel|bank|bus_station|cinema|casino|conference_centre|exhibition_centre|car_wash)$"];'
         f"node(around:{min(radius,2800)},{lat},{lon})[shop];"
         f"node(around:{min(radius,2800)},{lat},{lon})[office];"
         f'node(around:{radius},{lat},{lon})[tourism~"^(hotel|motel|attraction|museum|theme_park)$"];'
         f'node(around:{radius},{lat},{lon})[leisure~"^(park|stadium)$"];'
         f"node(around:{radius},{lat},{lon})[public_transport=station];"
         f'way(around:1600,{lat},{lon})[highway~"^(motorway|trunk|primary)$"];'
         f"way(around:25000,{lat},{lon})[aeroway=aerodrome];"
         f'node(around:18000,{lat},{lon})[amenity~"^(university|college)$"];'
         f"node(around:18000,{lat},{lon})[leisure=stadium];"
         f'node(around:18000,{lat},{lon})[amenity~"^(casino|conference_centre|exhibition_centre)$"];'
         f");out center 1100;")
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
        if el["type"] == "way" and t.get("highway"):
            c = el.get("center")
            if c and t.get("highway") in ("motorway", "trunk", "primary"):
                d = _hav(lat, lon, c["lat"], c["lon"]); road = d if road is None else min(road, d)
            continue
        cat = _classify(t)
        if not cat: continue
        la = el.get("lat") or (el.get("center") or {}).get("lat")
        lo = el.get("lon") or (el.get("center") or {}).get("lon")
        if la is None: continue
        d = _hav(lat, lon, la, lo)
        cnt[cat] = cnt.get(cat, 0) + 1
        if cat not in near or d < near[cat]["dist"]:
            near[cat] = {"name": t.get("name") or cat.title(), "dist": round(d)}
    if road is not None:
        near["major_road"] = {"name": "major road", "dist": round(road)}; cnt["major_road"] = 1
    return {"ok": True, "near": near, "count": cnt}

def flood(lat, lon):
    if not requests: return {"zone": None, "risk": "unknown", "sfha": False}
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
def _mi(near, cat): return round(near[cat]["dist"]/1609, 2) if cat in near else None

def recommend(near, count, fl):
    """Score each candidate use from market signals; return ranked with reasons + the facilities each needs."""
    uses = []; sfha = fl.get("sfha")
    def add(use, s, w, needs): uses.append({"use": use, "score": max(0, min(100, round(s))), "why": w, "needs": needs})

    # HOTEL — demand generators
    s, w = 20, []
    if _has(near, "airport", 25000): s += 12; w.append(f"airport {_mi(near,'airport')} mi")
    if _has(near, "convention", 12000): s += 16; w.append("convention center nearby")
    if _has(near, "casino", 8000) or _has(near, "attraction", 4000): s += 16; w.append("casino/attraction draw")
    if _has(near, "major_road", 2000): s += 16; w.append("highway access")
    if _has(near, "university", 12000): s += 8; w.append("university nearby")
    if _n(count, "restaurant") >= 6: s += 8; w.append(f"{_n(count,'restaurant')} restaurants")
    h = _n(count, "hotel")
    if 1 <= h <= 6: s += 10; w.append("proven, unsaturated hotel demand")
    elif h > 12: s -= 16; w.append(f"{h} hotels — saturated")
    add("Hotel / Lodging", s, w, ["airport", "convention", "casino", "attraction", "major_road", "university"])

    # GAS STATION / C-STORE
    s, w = 20, []
    if _has(near, "major_road", 700): s += 34; w.append("on a major road (traffic)")
    elif _has(near, "major_road", 1800): s += 16; w.append("major road nearby")
    f = _n(count, "fuel")
    if f == 0: s += 18; w.append("no fuel competitors nearby")
    elif f >= 3: s -= 18; w.append(f"{f} competing stations")
    if _n(count, "restaurant") + _n(count, "grocery") + _n(count, "retail") >= 6: s += 10; w.append("commercial activity")
    add("Gas Station / C-Store", s, w, ["major_road", "fuel", "retail", "restaurant"])

    # RESIDENTIAL subdivision / SFR
    s, w = 20, []
    if _has(near, "school", 1500): s += 20; w.append(f"school {_mi(near,'school')} mi")
    if _has(near, "grocery", 1500): s += 18; w.append(f"grocery {_mi(near,'grocery')} mi")
    if _has(near, "park", 1500): s += 12; w.append("park nearby")
    if _has(near, "hospital", 4000): s += 8; w.append("healthcare access")
    if _has(near, "transit", 1200): s += 8; w.append("transit nearby")
    if _has(near, "major_road", 150): s -= 12; w.append("highway adjacency (noise)")
    if sfha: s -= 25; w.append("FEMA flood zone")
    add("Residential / SFR subdivision", s, w, ["school", "grocery", "park", "hospital", "transit"])

    # MULTIFAMILY
    s, w = 20, []
    if _has(near, "transit", 1000): s += 22; w.append("transit-accessible")
    if _has(near, "grocery", 1200): s += 15; w.append("grocery nearby")
    if _has(near, "university", 6000): s += 10; w.append("university demand")
    if _n(count, "office") >= 5 or _n(count, "bank") >= 3: s += 12; w.append("jobs/commercial nearby")
    if sfha: s -= 20; w.append("flood zone")
    add("Multifamily / Apartments", s, w, ["transit", "grocery", "office", "university"])

    # RETAIL / strip
    s, w = 20, []
    shops = _n(count, "retail") + _n(count, "mall") + _n(count, "grocery")
    if shops >= 12: s += 28; w.append(f"{shops} retail nearby (corridor)")
    elif shops >= 5: s += 14; w.append("some retail nearby")
    if _has(near, "major_road", 600): s += 20; w.append("road visibility/traffic")
    add("Retail / Strip center", s, w, ["retail", "mall", "major_road"])

    # TYRE / AUTO SERVICE
    s, w = 20, []
    if _has(near, "major_road", 800): s += 26; w.append("road frontage/traffic")
    if _n(count, "retail") >= 6: s += 14; w.append("commercial corridor")
    a = _n(count, "auto")
    if a == 0: s += 14; w.append("no auto-service competitors")
    elif a >= 3: s -= 14; w.append(f"{a} auto shops nearby")
    if _n(count, "fuel") >= 1: s += 8; w.append("near fuel (car traffic)")
    add("Tyre / Auto service", s, w, ["major_road", "retail", "auto", "fuel"])

    # SMOKE / CONVENIENCE / LIQUOR
    s, w = 20, []
    if _has(near, "major_road", 700): s += 18; w.append("road traffic")
    if _n(count, "retail") + _n(count, "restaurant") >= 6: s += 16; w.append("foot/vehicle traffic")
    sm = _n(count, "smoke")
    if sm == 0: s += 14; w.append("no smoke/convenience competitor")
    elif sm >= 2: s -= 12; w.append(f"{sm} competitors")
    if _has(near, "university", 4000): s += 8; w.append("university foot traffic")
    add("Smoke / Convenience retail", s, w, ["retail", "restaurant", "smoke", "major_road"])

    # QSR / RESTAURANT PAD
    s, w = 20, []
    if _has(near, "major_road", 600): s += 24; w.append("drive-by traffic")
    if _n(count, "restaurant") >= 5: s += 16; w.append("restaurant corridor (proven demand)")
    if _n(count, "office") >= 4 or _has(near, "university", 5000): s += 12; w.append("daytime population")
    add("QSR / Restaurant pad", s, w, ["major_road", "restaurant", "office", "university"])

    # SELF-STORAGE
    s, w = 18, []
    if _n(count, "office") + _n(count, "retail") >= 8: s += 14; w.append("dense commercial/residential nearby")
    if _has(near, "major_road", 1200): s += 16; w.append("road access")
    if _has(near, "university", 8000): s += 10; w.append("student/relocation churn")
    if _n(count, "hotel") + _n(count, "restaurant") >= 8: s += 8; w.append("transient population")
    add("Self-storage", s, w, ["major_road", "office", "retail"])

    # MEDICAL OFFICE
    s, w = 18, []
    if _has(near, "hospital", 3000): s += 26; w.append(f"hospital {_mi(near,'hospital')} mi (referral cluster)")
    if _has(near, "pharmacy", 2000): s += 10; w.append("pharmacy nearby")
    if _n(count, "office") >= 4: s += 10; w.append("professional district")
    add("Medical office", s, w, ["hospital", "pharmacy", "office"])

    uses.sort(key=lambda x: -x["score"])
    return uses

# ----------------------------------------------------------- build-out massing
GROSS_PER = {"hotel": 450, "extended_stay": 470, "residential": 1000, "office": 320}  # gross sf / key or unit
EFF = {"hotel": 0.68, "extended_stay": 0.72, "residential": 0.80, "office": 0.85}
SHAPE = {"rectangular": 1.00, "L-shaped": 0.92, "irregular": 0.85, "diagonal": 0.85}

def massing(area_sf, use="hotel", shape="rectangular", far=2.0, height_ft=55,
            lot_coverage=0.45, floor_to_floor=10.5, parking_ratio=1.0, ground_retail=True):
    area_sf = float(area_sf)
    footprint = area_sf * float(lot_coverage)
    floors_h = int(height_ft // floor_to_floor)
    floors_far = int((float(far) * area_sf) // footprint) if footprint else 0
    floors = max(1, min(floors_h or 1, floors_far or 1))
    gross = footprint * floors
    sf = SHAPE.get(shape, 0.95); eff = EFF.get(use, 0.7); gpk = GROSS_PER.get(use, 450)
    unit = "keys" if use in ("hotel", "extended_stay") else "units"
    rentable = gross * eff * sf
    total = int(rentable / gpk)
    ground_keys = int((footprint * eff * sf) / gpk)
    with_retail = max(0, total - ground_keys)
    retail_sf = round(footprint * 0.85)
    parking = round(total * float(parking_ratio))
    binding = "height" if floors == floors_h and floors_h <= floors_far else ("FAR" if floors == floors_far else "height")
    return {
        "use": use, "shape": shape, "shape_factor": sf, "floors": floors, "footprint_sf": round(footprint),
        "gross_sf": round(gross), "efficiency": eff, "gross_per_" + ("key" if unit == "keys" else "unit"): gpk,
        "unit_label": unit,
        "without_ground_retail": {unit: total, "per_floor": round(total / floors) if floors else total},
        "with_ground_retail": {unit: with_retail, "ground_floor_retail_sf": retail_sf,
                               "note": "Ground floor leased as retail instead of " + unit + "."},
        "adjacent_lease_note": "If no on-site retail, an adjacent ground-floor lease is viable only where the site "
                               "has a retail corridor / road frontage (see Site signals). Otherwise keep ground floor "
                               "as lobby/amenity.",
        "parking_required": parking,
        "binding_constraint": binding,
        "caveat": "Scenario from your inputs — confirm with the real parcel polygon, zoning FAR/height/setbacks, "
                  "and a parking study. Not a substitute for an architect's test-fit.",
    }

def analyze(lat, lon):
    o = overpass(lat, lon); fl = flood(lat, lon)
    if not o.get("ok"):
        return {"lat": lat, "lon": lon, "flood": fl, "facilities": [], "hbu": [], "counts": {},
                "error": "Couldn't load the surroundings (the free mapping service was busy) — try again.",
                "risks": [{"type": "Flood", "level": fl.get("risk", "?"), "detail": f"zone {fl.get('zone')}", "source": "FEMA NFHL"}]}
    near, count = o.get("near", {}), o.get("count", {})
    hbu = recommend(near, count, fl)
    facilities = [{"cat": k, "name": v["name"], "dist_m": v["dist"], "dist_mi": round(v["dist"]/1609, 2)}
                  for k, v in sorted(near.items(), key=lambda x: x[1]["dist"])]
    risks = []
    if fl.get("sfha"):
        risks.append({"type": "Flood", "level": "High",
                      "detail": f"FEMA Special Flood Hazard Area — zone {fl.get('zone')}", "source": "FEMA NFHL"})
    risks.append({"type": "Litigation / title", "level": "Check manually",
                  "detail": "No reliable free source — order a title search / pull county records before LOI.", "source": "—"})
    risks.append({"type": "Environmental", "level": "Check manually",
                  "detail": "For fuel/auto/industrial uses, commission a Phase I ESA (USTs, contamination).", "source": "—"})
    return {"lat": lat, "lon": lon, "flood": fl, "facilities": facilities, "hbu": hbu, "counts": count, "risks": risks,
            "notes": "HBU = market-signal screening, not a feasibility study. Use the build-out estimator for keys/units."}


if __name__ == "__main__":
    r = analyze(36.0337, -115.0413)
    print("flood:", r["flood"]["zone"], r["flood"]["risk"])
    print("top uses:", [(u["use"], u["score"]) for u in r["hbu"][:5]])
    print("facilities:", [(f["cat"], f["dist_mi"]) for f in r["facilities"][:10]])
    m = massing(40000, use="hotel", shape="rectangular", far=3.0, height_ft=75, lot_coverage=0.5)
    print("massing hotel:", m["floors"], "floors ·", m["without_ground_retail"]["keys"], "keys (no retail) /",
          m["with_ground_retail"]["keys"], "keys + retail · parking", m["parking_required"])
