"""
Search for restaurants on Resy and OpenTable to add to config.
Also provides interactive one-off search with availability checking.
"""

import os
import re
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests
import anthropic
import yaml
from dotenv import load_dotenv
from pathlib import Path

logger = logging.getLogger(__name__)

# Time window options
TIME_WINDOWS = {
    "1": ("11:00", "13:00", "11am–1pm (brunch)"),
    "2": ("13:00", "17:00", "1pm–5pm (afternoon)"),
    "3": ("17:00", "19:00", "5pm–7pm (early dinner)"),
    "4": ("19:00", "21:00", "7pm–9pm (dinner)"),
    "5": ("21:00", "23:00", "9pm–11pm (late dinner)"),
}

DAY_OPTIONS = {
    "1": "monday",
    "2": "tuesday",
    "3": "wednesday",
    "4": "thursday",
    "5": "friday",
    "6": "saturday",
    "7": "sunday",
    "8": "today",
    "9": "tomorrow",
}

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env", override=True)


def slugify(name: str) -> str:
    """Convert a restaurant name to a URL slug."""
    # Lowercase, replace spaces with hyphens, remove special chars
    slug = name.lower()
    slug = re.sub(r"[''`]", "", slug)  # Remove apostrophes
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)  # Remove special chars
    slug = re.sub(r"\s+", "-", slug)  # Spaces to hyphens
    slug = re.sub(r"-+", "-", slug)  # Multiple hyphens to single
    return slug.strip("-")


def search_resy(query: str) -> list[dict]:
    """
    Search for restaurants on Resy.

    Returns list of {name, slug, venue_id, location}
    """
    api_key = os.getenv("RESY_API_KEY", "")
    auth_token = os.getenv("RESY_AUTH_TOKEN", "")

    if 'api_key="' in api_key:
        api_key = api_key.split('api_key="')[1].rstrip('"')

    session = requests.Session()
    session.headers.update({
        "Authorization": f'ResyAPI api_key="{api_key}"',
        "X-Resy-Auth-Token": auth_token,
        "X-Resy-Universal-Auth": auth_token,
        "Origin": "https://resy.com",
        "Referer": "https://resy.com/",
    })

    results = []

    # Try direct slug lookup first
    slug_guess = slugify(query)
    slug_variants = [
        slug_guess,
        f"{slug_guess}-nyc",
        f"{slug_guess}-new-york",
        f"the-{slug_guess}",
    ]

    for slug in slug_variants:
        try:
            url = "https://api.resy.com/3/venue"
            params = {"url_slug": slug, "location": "ny"}
            response = session.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if "id" in data and "resy" in data["id"]:
                    results.append({
                        "name": data.get("name", slug),
                        "slug": data.get("url_slug", slug),
                        "venue_id": data["id"]["resy"],
                        "location": data.get("location", {}).get("neighborhood", ""),
                        "platform": "resy",
                    })
                    break  # Found it
            time.sleep(0.5)
        except Exception:
            pass

    # Also try the search API
    try:
        url = "https://api.resy.com/3/venuesearch/search"
        params = {"query": query, "lat": "40.7128", "long": "-74.0060", "limit": 5}
        response = session.get(url, params=params, timeout=10)

        if response.status_code == 200:
            data = response.json()
            hits = data.get("search", {}).get("hits", [])
            for hit in hits[:5]:
                # Avoid duplicates
                if not any(r["slug"] == hit.get("url_slug") for r in results):
                    results.append({
                        "name": hit.get("name", ""),
                        "slug": hit.get("url_slug", ""),
                        "venue_id": hit.get("id", {}).get("resy", 0),
                        "location": hit.get("location", {}).get("neighborhood", ""),
                        "platform": "resy",
                    })
    except Exception:
        pass

    return results


def search_opentable(query: str) -> list[dict]:
    """
    Search for restaurants on OpenTable.

    Returns list of {name, slug, restaurant_id, location}
    """
    results = []

    # Try direct slug lookup
    slug_guess = slugify(query)
    slug_variants = [
        slug_guess,
        f"{slug_guess}-new-york",
        f"{slug_guess}-nyc",
    ]

    cookies_raw = os.getenv("OT_AUTH_TOKEN", "")
    cookies = {}
    for part in cookies_raw.split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            cookies[key.strip()] = value.strip()

    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    })

    for slug in slug_variants:
        try:
            url = "https://www.opentable.com/restref/api/profile"
            params = {"slug": slug}
            response = session.get(url, params=params, timeout=10)

            if response.status_code == 200:
                data = response.json()
                if "rid" in data:
                    results.append({
                        "name": data.get("name", slug),
                        "slug": slug,
                        "restaurant_id": data["rid"],
                        "location": data.get("neighborhood", ""),
                        "platform": "opentable",
                    })
                    break
            time.sleep(0.5)
        except Exception:
            pass

    return results


def search_all(query: str) -> list[dict]:
    """Search both Resy and OpenTable."""
    results = []

    print(f"\nSearching for '{query}'...")

    # Search Resy
    print("  Checking Resy...", end=" ", flush=True)
    resy_results = search_resy(query)
    print(f"found {len(resy_results)}")
    results.extend(resy_results)

    # Search OpenTable
    print("  Checking OpenTable...", end=" ", flush=True)
    ot_results = search_opentable(query)
    print(f"found {len(ot_results)}")
    results.extend(ot_results)

    return results


def add_to_config(restaurant: dict, slots: list[str]) -> bool:
    """Add a restaurant to config.yaml."""
    config_path = BASE_DIR / "config.yaml"

    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Check if already exists
    for r in config.get("restaurants", []):
        if r.get("slug") == restaurant["slug"]:
            print(f"\n{restaurant['name']} is already in your config.")
            return False

    # Add new restaurant
    new_entry = {
        "name": restaurant["name"],
        "platform": restaurant["platform"],
        "slug": restaurant["slug"],
        "slots": slots,
    }

    config["restaurants"].append(new_entry)

    # Write back
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

    return True


def interactive_add(query: str) -> bool:
    """Interactive flow to add a restaurant."""
    results = search_all(query)

    if not results:
        print(f"\nNo restaurants found matching '{query}'.")
        print("Try searching with a different name or check the spelling.")
        return False

    # Show results
    print(f"\nFound {len(results)} result(s):\n")
    for i, r in enumerate(results, 1):
        location = f" ({r['location']})" if r.get('location') else ""
        print(f"  {i}. [{r['platform'].upper()}] {r['name']}{location}")
        print(f"     slug: {r['slug']}")

    # Select one
    print()
    if len(results) == 1:
        choice = input("Add this restaurant? (y/n): ").strip().lower()
        if choice not in ("y", "yes"):
            print("Cancelled.")
            return False
        selected = results[0]
    else:
        try:
            choice = input(f"Which one? (1-{len(results)}, or 'n' to cancel): ").strip()
            if choice.lower() in ("n", "no", ""):
                print("Cancelled.")
                return False
            selected = results[int(choice) - 1]
        except (ValueError, IndexError):
            print("Invalid choice. Cancelled.")
            return False

    # Select slots
    print(f"\nWhich time slots for {selected['name']}?")
    print("  1. friday_dinner (Fri 7-10pm)")
    print("  2. saturday_brunch (Sat 11am-1pm)")
    print("  3. saturday_dinner (Sat 7-10pm)")
    print("  4. All of the above")
    print()

    slot_choice = input("Enter numbers (e.g., '1,3' or '4' for all): ").strip()

    slot_map = {
        "1": "friday_dinner",
        "2": "saturday_brunch",
        "3": "saturday_dinner",
    }

    if "4" in slot_choice:
        slots = ["friday_dinner", "saturday_brunch", "saturday_dinner"]
    else:
        slots = [slot_map[c.strip()] for c in slot_choice.split(",") if c.strip() in slot_map]

    if not slots:
        slots = ["friday_dinner", "saturday_dinner"]  # Default
        print("Using default: friday_dinner, saturday_dinner")

    # Add to config
    if add_to_config(selected, slots):
        print(f"\nAdded {selected['name']} to your config!")
        print(f"  Platform: {selected['platform']}")
        print(f"  Slug: {selected['slug']}")
        print(f"  Slots: {', '.join(slots)}")
        return True

    return False


def _build_resy_session() -> requests.Session:
    """Build an authenticated Resy requests session."""
    api_key = os.getenv("RESY_API_KEY", "")
    auth_token = os.getenv("RESY_AUTH_TOKEN", "")
    if 'api_key="' in api_key:
        api_key = api_key.split('api_key="')[1].rstrip('"')
    session = requests.Session()
    session.headers.update({
        "Authorization": f'ResyAPI api_key="{api_key}"',
        "X-Resy-Auth-Token": auth_token,
        "X-Resy-Universal-Auth": auth_token,
        "Content-Type": "application/json",
        "Origin": "https://resy.com",
        "Referer": "https://resy.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    })
    return session


def _parse_description(description: str) -> dict:
    """
    Use Claude to extract structured info from a free-text restaurant description.
    Returns:
      {
        "query": str,               # cuisine/type for Resy search
        "neighborhood": str|None,   # neighborhood if mentioned
        "day": str|None,            # e.g. "thursday", "friday", "today"
        "time_window": str|None,    # one of "1","2","3","4" matching TIME_WINDOWS keys
        "party_size": int|None,     # number of people if mentioned
      }
    """
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{
            "role": "user",
            "content": (
                f"Extract the following from this restaurant description. Return valid JSON only.\n\n"
                f"Fields:\n"
                f"- query: short cuisine/type for search (2-4 words, e.g. 'Italian wine bar', 'steakhouse', 'cocktail bar')\n"
                f"- neighborhood: neighborhood name if mentioned, else null\n"
                f"- day: day of week if mentioned (lowercase: 'monday','tuesday','wednesday','thursday','friday','saturday','sunday','today','tomorrow'), else null\n"
                f"- time_window: if a time is mentioned, map it to one of these keys: "
                f"'1' (11am-1pm brunch), '2' (1pm-5pm afternoon), '3' (5pm-7pm early dinner), '4' (7pm-9pm dinner), '5' (9pm-11pm late dinner). Null if no time mentioned.\n"
                f"- party_size: number of people if mentioned, else null\n\n"
                f"Description: {description}\n\n"
                f"Example output: {{\"query\": \"Italian wine bar\", \"neighborhood\": \"West Village\", \"day\": \"thursday\", \"time_window\": \"3\", \"party_size\": 2}}"
            )
        }]
    )
    import json, re
    try:
        text = response.content[0].text.strip()
        text = re.sub(r"^```json\s*|^```\s*|```$", "", text, flags=re.MULTILINE).strip()
        return json.loads(text)
    except Exception:
        return {"query": description, "neighborhood": None, "day": None, "time_window": None, "party_size": None}


def _resolve_date(day_choice: str) -> Optional[datetime]:
    """Resolve a day keyword to a concrete date (always the next occurrence)."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    weekday = today.weekday()  # 0=Mon … 6=Sun

    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }

    if day_choice == "today":
        return today
    elif day_choice == "tomorrow":
        return today + timedelta(days=1)
    elif day_choice in day_map:
        target_weekday = day_map[day_choice]
        days = (target_weekday - weekday) % 7 or 7
        return today + timedelta(days=days)
    return None


def _search_resy_venues(query: str, neighborhood: str = None) -> list[dict]:
    """Search Resy venue search API and return hits sorted by rating.

    Searches cuisine+neighborhood together first for precision.
    Falls back to cuisine-only if that returns nothing.
    """
    session = _build_resy_session()

    def _do_search(q):
        try:
            response = session.post(
                "https://api.resy.com/3/venuesearch/search",
                json={"query": q, "geo": {"latitude": 40.7589, "longitude": -73.9851}},
                timeout=15,
            )
            response.raise_for_status()
            hits = response.json().get("search", {}).get("hits", [])
            hits.sort(key=lambda h: h.get("rating", {}).get("average", 0), reverse=True)
            return hits
        except requests.RequestException as e:
            logger.error(f"Venue search error: {e}")
            return []

    # Try with neighborhood in query first
    if neighborhood:
        combined_query = f"{query} {neighborhood}"
        hits = _do_search(combined_query)
        if hits:
            return hits

    # Fall back to cuisine-only
    return _do_search(query)


def _check_availability(slug: str, date: datetime, party_size: int, start_time: str, end_time: str) -> list[dict]:
    """Check slot availability for a venue slug."""
    from .resy import ResyClient
    from .scheduler import is_time_in_range
    try:
        client = ResyClient()
        venue = client.get_venue_by_slug(slug)
        if not venue:
            return []
        time.sleep(1)
        return client.find_available_slots(venue["venue_id"], date, party_size, start_time, end_time)
    except Exception as e:
        logger.error(f"Availability error for {slug}: {e}")
        return []


def run_search():
    """Interactive one-off restaurant search wizard."""
    print("\n" + "=" * 50)
    print("RESERVATION SEARCH")
    print("=" * 50)

    # Step 1: Description — parse immediately to extract anything already specified
    description = input("\nDescribe the restaurant:\n(e.g. 'cozy Italian West Village Thursday 8pm for 2')\n> ").strip()
    if not description:
        print("No description entered.")
        return

    print("\nParsing...")
    parsed = _parse_description(description)
    query = parsed.get("query") or description
    neighborhood = parsed.get("neighborhood")

    # Step 2: Time window — skip if already parsed
    if parsed.get("time_window") and parsed["time_window"] in TIME_WINDOWS:
        start_time, end_time, time_label = TIME_WINDOWS[parsed["time_window"]]
    else:
        print("\nWhat time window?")
        for k, (_, _, label) in TIME_WINDOWS.items():
            print(f"  {k}. {label}")
        time_choice = input("> ").strip()
        if time_choice not in TIME_WINDOWS:
            print("Invalid choice.")
            return
        start_time, end_time, time_label = TIME_WINDOWS[time_choice]

    # Step 3: Day — skip if already parsed
    if parsed.get("day"):
        target_date = _resolve_date(parsed["day"])
    else:
        print("\nWhat day?")
        for k, v in DAY_OPTIONS.items():
            print(f"  {k}. {v.capitalize()}")
        print("  0. Specific date (YYYY-MM-DD)")
        day_choice = input("> ").strip()

        target_date = None
        if day_choice in DAY_OPTIONS:
            target_date = _resolve_date(DAY_OPTIONS[day_choice])
        elif day_choice == "0":
            date_str = input("Enter date (YYYY-MM-DD)\n> ").strip()
            try:
                target_date = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                print("Invalid date.")
                return
        else:
            print("Invalid choice.")
            return

    if not target_date:
        print("Could not resolve date.")
        return

    # Step 4: Party size — skip if already parsed
    if parsed.get("party_size"):
        party_size = parsed["party_size"]
    else:
        party_str = input("\nHow many people?\n> ").strip()
        try:
            party_size = int(party_str)
        except ValueError:
            party_size = 2

    date_display = target_date.strftime("%A, %B %-d")
    print(f"\nSearching: \"{description}\"")
    print(f"  {date_display}  ·  {time_label}  ·  party of {party_size}")

    if neighborhood:
        print(f"  Cuisine/type: \"{query}\"  ·  Neighborhood: \"{neighborhood}\"")
    else:
        print(f"  Cuisine/type: \"{query}\"")
    print("\nFinding venues and checking availability...\n")

    hits = _search_resy_venues(query)
    if not hits:
        print("No venues found. Try a different description.")
        return

    # Filter by neighborhood if specified (fuzzy match)
    if neighborhood:
        neighborhood_lower = neighborhood.lower()
        filtered = [
            h for h in hits
            if neighborhood_lower in (h.get("neighborhood") or "").lower()
        ]
        # Fall back to unfiltered if neighborhood match yields nothing
        hits = filtered if filtered else hits

    # Check availability on top hits, gather up to 5 with open slots
    results = []
    checked = 0
    for hit in hits:
        if len(results) >= 5:
            break
        if checked >= 15:
            break

        slug = hit.get("url_slug")
        if not slug:
            continue

        checked += 1
        slots = _check_availability(slug, target_date, party_size, start_time, end_time)
        time.sleep(1)

        if slots:
            rating = hit.get("rating", {})
            results.append({
                "name": hit.get("name", slug),
                "slug": slug,
                "rating": rating.get("average"),
                "review_count": rating.get("count", 0),
                "cuisine": ", ".join(hit.get("cuisine", [])),
                "price": "$" * (hit.get("price_range_id") or 2),
                "neighborhood": hit.get("neighborhood", ""),
                "slots": slots[:3],
            })

    # Display results
    print("=" * 50)
    if not results:
        print(f"No availability found for {date_display} in that window.")
        print("Try a different date, time, or description.")
        return

    print(f"RESULTS — {date_display}  ·  {time_label}  ·  party of {party_size}")
    print("=" * 50)

    for i, r in enumerate(results, 1):
        rating_str = f"★ {r['rating']:.2f} ({r['review_count']} reviews)" if r["rating"] else "No rating"
        neighborhood_str = f"  ·  {r['neighborhood']}" if r.get("neighborhood") else ""
        print(f"\n{i}. {r['name']}")
        print(f"   {r['cuisine']}  ·  {r['price']}{neighborhood_str}  ·  {rating_str}")
        for s in r["slots"]:
            seat_type = s.get("type", "").strip()
            seat_str = f"  ({seat_type})" if seat_type else ""
            print(f"   {s['time']}{seat_str}")

    print("\n" + "=" * 50)

    # Offer to book
    book_choice = input("Enter a number to book, or press Enter to exit\n> ").strip()
    if not book_choice:
        return

    try:
        idx = int(book_choice) - 1
        if idx < 0 or idx >= len(results):
            print("Invalid selection.")
            return
    except ValueError:
        print("Invalid selection.")
        return

    chosen = results[idx]
    slot = chosen["slots"][0]

    if len(chosen["slots"]) > 1:
        print(f"\nAvailable times for {chosen['name']}:")
        for i, s in enumerate(chosen["slots"], 1):
            seat_type = s.get("type", "").strip()
            seat_str = f"  ({seat_type})" if seat_type else ""
            print(f"  {i}. {s['time']}{seat_str}")
        sc = input("Pick a time (number)\n> ").strip()
        try:
            slot = chosen["slots"][int(sc) - 1]
        except (ValueError, IndexError):
            slot = chosen["slots"][0]

    print(f"\nBooking {chosen['name']} at {slot['time']} on {date_display} for {party_size}...")

    from .resy import ResyClient
    try:
        client = ResyClient()
        result = client.attempt_booking(
            slug=chosen["slug"],
            date=target_date,
            party_size=party_size,
            start_time=slot["time"],
            end_time=slot["time"],
            dry_run=False,
        )
        if result:
            print(f"\nBooked! {result['venue']} at {result['time']} on {result['date']}")
        else:
            print("\nBooking failed — slot may have just been taken. Try another.")
    except Exception as e:
        print(f"\nError during booking: {e}")


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        interactive_add(query)
    else:
        print("Usage: python -m bot.search 'Restaurant Name'")
