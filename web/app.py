"""
Flask web app for TableFinder — no login required.
Uses a shared RESY_AUTH_TOKEN from environment (valid until May 26, 2026).
"""

import os
import sys
import time
import requests
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, jsonify
from dotenv import load_dotenv

# Add project root to path so we can import bot modules
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from bot.search import (
    _parse_description, _search_resy_venues,
    _check_availability, _resolve_date, TIME_WINDOWS,
)
from bot.resy import ResyClient

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "tablefinder-2026")

# Parse API key (handles both raw key and full "ResyAPI api_key=..." format)
RESY_API_KEY = os.getenv("RESY_API_KEY", "")
if 'api_key="' in RESY_API_KEY:
    RESY_API_KEY = RESY_API_KEY.split('api_key="')[1].rstrip('"')

RESY_AUTH_TOKEN = os.getenv("RESY_AUTH_TOKEN", "")

# Inject token so bot modules pick it up
if RESY_AUTH_TOKEN:
    os.environ["RESY_AUTH_TOKEN"] = RESY_AUTH_TOKEN


@app.route("/")
def index():
    return render_template("search.html")


@app.route("/api/search", methods=["POST"])
def api_search():
    if not RESY_AUTH_TOKEN:
        return jsonify({"error": "Server not configured — RESY_AUTH_TOKEN missing."}), 500

    data = request.get_json()
    description = (data.get("description") or "").strip()
    if not description:
        return jsonify({"error": "No description provided"}), 400

    # Parse with Claude
    parsed = _parse_description(description)
    query        = parsed.get("query") or description
    neighborhood = parsed.get("neighborhood")

    # Time window
    time_key = data.get("time_window") or parsed.get("time_window")
    start_time, end_time, time_label = TIME_WINDOWS.get(time_key, TIME_WINDOWS["4"])

    # Date
    date_str = data.get("date")
    day      = data.get("day") or parsed.get("day")
    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            target_date = None
    elif day:
        target_date = _resolve_date(day)
    else:
        target_date = None

    if not target_date:
        return jsonify({"error": "Couldn't figure out the date — try adding a day like 'Friday' or 'this Saturday'."}), 400

    # Party size
    party_size = data.get("party_size") or parsed.get("party_size") or 2
    try:
        party_size = int(party_size)
    except (TypeError, ValueError):
        party_size = 2

    # Search venues
    hits = _search_resy_venues(query)
    if not hits:
        return jsonify({"results": [], "meta": {"query": query, "neighborhood": neighborhood}})

    # Neighborhood filter
    if neighborhood:
        filtered = [h for h in hits if neighborhood.lower() in (h.get("neighborhood") or "").lower()]
        hits = filtered if filtered else hits

    # Check availability (up to 5 results, check up to 15 venues)
    results = []
    checked = 0
    for hit in hits:
        if len(results) >= 5 or checked >= 15:
            break
        slug = hit.get("url_slug")
        if not slug:
            continue
        checked += 1

        slots = _check_availability(slug, target_date, party_size, start_time, end_time)
        time.sleep(0.5)

        if slots:
            rating = hit.get("rating", {})
            results.append({
                "name":         hit.get("name", slug),
                "slug":         slug,
                "rating":       round(rating.get("average", 0), 2) if rating.get("average") else None,
                "review_count": rating.get("count", 0),
                "cuisine":      ", ".join(hit.get("cuisine", [])),
                "price":        "$" * (hit.get("price_range_id") or 2),
                "neighborhood": hit.get("neighborhood", ""),
                "slots": [
                    {
                        "time":         s["time"],
                        "type":         s.get("type", "").strip(),
                        "config_token": s.get("config_token", ""),
                        "date":         s.get("date", ""),
                    }
                    for s in slots[:4]
                ],
            })

    return jsonify({
        "results": results,
        "meta": {
            "query":        query,
            "neighborhood": neighborhood,
            "date":         target_date.strftime("%A, %B %-d"),
            "time_label":   time_label,
            "party_size":   party_size,
        },
    })


@app.route("/api/book", methods=["POST"])
def api_book():
    if not RESY_AUTH_TOKEN:
        return jsonify({"error": "Server not configured"}), 500

    data       = request.get_json()
    slug       = data.get("slug")
    date_str   = data.get("date")
    time_str   = data.get("time")
    party_size = int(data.get("party_size", 2))

    if not all([slug, date_str, time_str]):
        return jsonify({"error": "Missing booking details"}), 400

    try:
        client      = ResyClient()
        target_date = datetime.strptime(date_str, "%Y-%m-%d")
        result      = client.attempt_booking(
            slug=slug, date=target_date, party_size=party_size,
            start_time=time_str, end_time=time_str, dry_run=False,
        )
        if result:
            return jsonify({"success": True, "venue": result["venue"],
                            "time": result["time"], "date": result["date"]})
        return jsonify({"error": "Booking failed — slot may have just been taken."}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
