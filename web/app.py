"""
Flask web app for reservation search.
"""

import os
import sys
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from flask import (
    Flask, render_template, request, session,
    redirect, url_for, jsonify
)
from dotenv import load_dotenv

# Add project root to path so we can import bot modules
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from bot.search import (
    _parse_description, _search_resy_venues,
    _check_availability, _resolve_date, TIME_WINDOWS, DAY_OPTIONS
)
from bot.resy import ResyClient

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())

RESY_API_KEY = os.getenv("RESY_API_KEY", "")
if 'api_key="' in RESY_API_KEY:
    RESY_API_KEY = RESY_API_KEY.split('api_key="')[1].rstrip('"')


def resy_login(email: str, password: str) -> dict:
    """Authenticate with Resy. Returns dict with 'token' on success or 'error' on failure."""
    try:
        response = requests.post(
            "https://api.resy.com/3/auth/password",
            headers={
                "Authorization": f'ResyAPI api_key="{RESY_API_KEY}"',
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://resy.com",
                "Referer": "https://resy.com/",
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "X-Resy-Universal-Auth": "",
            },
            data={"email": email, "password": password},
            timeout=10,
        )
        data = response.json()

        if response.status_code == 200:
            # Resy returns token at top level
            token = (
                data.get("token")
                or data.get("auth_token")
                or data.get("em_token")
            )
            if token:
                return {"token": token, "email": email}
            # Shouldn't happen, but surface it
            return {"error": f"Login succeeded but no token found in response: {list(data.keys())}"}

        # 419 = bad credentials, 429 = rate limited
        if response.status_code == 419:
            return {"error": "Wrong email or password — double-check your Resy login details."}
        if response.status_code == 429:
            return {"error": "Too many login attempts — wait a minute and try again."}

        msg = data.get("message") or f"Resy returned status {response.status_code}"
        return {"error": msg}

    except requests.exceptions.Timeout:
        return {"error": "Resy took too long to respond — try again."}
    except Exception as e:
        return {"error": f"Login error: {str(e)}"}


def get_resy_client() -> ResyClient | None:
    """Build a ResyClient using the logged-in user's token."""
    token = session.get("resy_token")
    if not token:
        return None
    # Temporarily override env vars for this request
    os.environ["RESY_AUTH_TOKEN"] = token
    try:
        return ResyClient()
    except Exception:
        return None


@app.route("/")
def index():
    if session.get("resy_token"):
        return redirect(url_for("search"))
    return render_template("login.html")


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not email or not password:
        return render_template("login.html", error="Please enter your email and password.")

    result = resy_login(email, password)
    if "error" in result:
        return render_template("login.html", error=result["error"])

    session["resy_token"] = result["token"]
    session["resy_email"] = result["email"]
    return redirect(url_for("search"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


@app.route("/search")
def search():
    if not session.get("resy_token"):
        return redirect(url_for("index"))
    return render_template("search.html", email=session.get("resy_email", ""))


@app.route("/api/search", methods=["POST"])
def api_search():
    if not session.get("resy_token"):
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json()
    description = data.get("description", "").strip()
    if not description:
        return jsonify({"error": "No description provided"}), 400

    # Parse description with Claude
    parsed = _parse_description(description)
    query = parsed.get("query") or description
    neighborhood = parsed.get("neighborhood")

    # Resolve time window
    time_key = data.get("time_window") or parsed.get("time_window")
    if time_key and time_key in TIME_WINDOWS:
        start_time, end_time, time_label = TIME_WINDOWS[time_key]
    else:
        start_time, end_time, time_label = TIME_WINDOWS["4"]  # default: dinner

    # Resolve date
    date_str = data.get("date")
    day = data.get("day") or parsed.get("day")
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
        return jsonify({"error": "Could not determine date. Try including a day like 'Friday' or 'this Thursday'."}), 400

    # Party size
    party_size = data.get("party_size") or parsed.get("party_size") or 2
    try:
        party_size = int(party_size)
    except (TypeError, ValueError):
        party_size = 2

    # Search venues
    os.environ["RESY_AUTH_TOKEN"] = session["resy_token"]
    hits = _search_resy_venues(query)

    if not hits:
        return jsonify({"results": [], "meta": {"query": query, "neighborhood": neighborhood}})

    # Filter by neighborhood
    if neighborhood:
        filtered = [h for h in hits if neighborhood.lower() in (h.get("neighborhood") or "").lower()]
        hits = filtered if filtered else hits

    # Check availability
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
                "name": hit.get("name", slug),
                "slug": slug,
                "rating": round(rating.get("average", 0), 2) if rating.get("average") else None,
                "review_count": rating.get("count", 0),
                "cuisine": ", ".join(hit.get("cuisine", [])),
                "price": "$" * (hit.get("price_range_id") or 2),
                "neighborhood": hit.get("neighborhood", ""),
                "slots": [
                    {
                        "time": s["time"],
                        "type": s.get("type", "").strip(),
                        "config_token": s.get("config_token", ""),
                        "date": s.get("date", ""),
                    }
                    for s in slots[:4]
                ],
            })

    return jsonify({
        "results": results,
        "meta": {
            "query": query,
            "neighborhood": neighborhood,
            "date": target_date.strftime("%A, %B %-d"),
            "time_label": time_label,
            "party_size": party_size,
        }
    })


@app.route("/api/book", methods=["POST"])
def api_book():
    if not session.get("resy_token"):
        return jsonify({"error": "Not logged in"}), 401

    data = request.get_json()
    slug = data.get("slug")
    date_str = data.get("date")
    time_str = data.get("time")
    party_size = int(data.get("party_size", 2))

    if not all([slug, date_str, time_str]):
        return jsonify({"error": "Missing booking details"}), 400

    try:
        os.environ["RESY_AUTH_TOKEN"] = session["resy_token"]
        client = ResyClient()
        target_date = datetime.strptime(date_str, "%Y-%m-%d")

        result = client.attempt_booking(
            slug=slug,
            date=target_date,
            party_size=party_size,
            start_time=time_str,
            end_time=time_str,
            dry_run=False,
        )

        if result:
            return jsonify({"success": True, "venue": result["venue"], "time": result["time"], "date": result["date"]})
        else:
            return jsonify({"error": "Booking failed — that slot may have just been taken."}), 409

    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
