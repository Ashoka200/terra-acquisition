"""
listings.py — live for-sale ("ready to sell") listings, provider-pluggable.

Default provider: RentCast (rentcast.io) — Sale Listings endpoint supports search by
zip code OR by a circular area (lat/lon + radius). Free tier: 50 calls/mo, no card.
Set RENTCAST_API_KEY to enable. ATTOM can be added the same way later.

Honest: without a key, every call returns a clear "how to enable" message — no fake data.
"""
import os
try:
    import requests
except ImportError:
    requests = None

RENTCAST_BASE = "https://api.rentcast.io/v1/listings/sale"

def provider():
    if os.environ.get("RENTCAST_API_KEY") and requests:
        return "rentcast"
    return None

def _need():
    return {"provider": None, "error": "no live-listings provider configured",
            "how": "Set RENTCAST_API_KEY (free tier — 50 calls/mo, no card — at rentcast.io/api), "
                   "then restart. Or wire ATTOM the same way in listings.py."}

def _rentcast(params):
    key = os.environ.get("RENTCAST_API_KEY")
    r = requests.get(RENTCAST_BASE, params={**params, "status": "Active", "limit": 200},
                     headers={"X-Api-Key": key, "accept": "application/json"}, timeout=25)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else data.get("listings", data.get("data", []))

def _norm(x):
    return {"address": x.get("formattedAddress") or x.get("addressLine1"), "city": x.get("city"),
            "state": x.get("state"), "zip": x.get("zipCode"), "lat": x.get("latitude"),
            "lon": x.get("longitude"), "price": x.get("price"), "beds": x.get("bedrooms"),
            "baths": x.get("bathrooms"), "sqft": x.get("squareFootage"), "lot": x.get("lotSize"),
            "yearbuilt": x.get("yearBuilt"), "type": x.get("propertyType"), "status": x.get("status"),
            "listed": (x.get("listedDate") or "")[:10], "dom": x.get("daysOnMarket")}

def _friendly(e):
    code = getattr(getattr(e, "response", None), "status_code", None)
    if code in (401, 403):
        return ("RentCast rejected the key (HTTP %s). Activate your API subscription — the free plan — "
                "at app.rentcast.io/app/api, and make sure RENTCAST_API_KEY matches the active key "
                "(no extra spaces). Creating a key is not enough; the subscription must be activated." % code)
    if code == 429:
        return "RentCast rate/quota limit hit (HTTP 429) — the free plan is 50 calls/month. Upgrade or wait."
    return f"listings request failed: {str(e)[:140]}"

def sale_near(lat, lon, radius=3):
    if not provider(): return _need()
    try:
        data = _rentcast({"latitude": lat, "longitude": lon, "radius": radius})
        return {"provider": "rentcast", "count": len(data), "listings": [_norm(x) for x in data]}
    except Exception as e:
        return {"provider": "rentcast", "error": _friendly(e)}

def sale_by_zip(zipcode):
    if not provider(): return _need()
    try:
        data = _rentcast({"zipCode": str(zipcode)})
        return {"provider": "rentcast", "count": len(data), "listings": [_norm(x) for x in data]}
    except Exception as e:
        return {"provider": "rentcast", "error": _friendly(e)}
