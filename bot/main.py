#!/usr/bin/env python3
"""
Reservation Bot main entry point.
Orchestrates the reservation booking process across Resy and OpenTable.

Flow:
1. Find available slots across all restaurants
2. Group by weekend - auto-book ONE per weekend, save others as "optional"
3. Send notification with 45-minute countdown for auto-bookings
4. Wait 45 minutes (user can cancel during this time)
5. Book the auto-selected reservations
6. User can manually confirm optional slots with --confirm
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from collections import defaultdict

import yaml
from dotenv import load_dotenv

from .scheduler import get_target_dates
from .resy import ResyClient
from .opentable import OpenTableClient
from .notify import send_notification, notify_booking_success, notify_run_complete

# Load environment variables
load_dotenv()

# Directories
BASE_DIR = Path(__file__).parent.parent
LOG_DIR = BASE_DIR / "logs"
PENDING_DIR = BASE_DIR / "pending"

LOG_DIR.mkdir(exist_ok=True)
PENDING_DIR.mkdir(exist_ok=True)

LOG_FILE = LOG_DIR / "run.log"

# Confirmation delay (in minutes)
CONFIRMATION_DELAY_MINUTES = 45

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def load_config() -> dict:
    """Load configuration from config.yaml."""
    config_path = BASE_DIR / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_weekend_id(date_str: str) -> str:
    """
    Get the weekend identifier for a date.
    Weekend is identified by the Saturday date.
    Friday 4/17 -> weekend 4/18, Saturday 4/18 -> weekend 4/18
    """
    date = datetime.strptime(date_str, "%Y-%m-%d")
    weekday = date.weekday()

    if weekday == 4:  # Friday
        saturday = date + timedelta(days=1)
    elif weekday == 5:  # Saturday
        saturday = date
    else:
        # For other days, find the next Saturday
        days_until_saturday = (5 - weekday) % 7
        saturday = date + timedelta(days=days_until_saturday)

    return saturday.strftime("%Y-%m-%d")


def save_booking(booking: dict, status: str = "pending") -> Path:
    """Save a booking to the pending directory."""
    safe_name = booking["venue"].lower().replace(" ", "_").replace("'", "")
    filename = f"{safe_name}_{booking['date']}.json"
    filepath = PENDING_DIR / filename

    booking["pending_since"] = datetime.now().isoformat()
    booking["status"] = status
    booking["weekend_id"] = get_weekend_id(booking["date"])

    with open(filepath, "w") as f:
        json.dump(booking, f, indent=2)

    logger.info(f"Saved {status} booking: {filepath}")
    return filepath


def get_bookings_by_status(status: str) -> list[dict]:
    """Get all bookings with a specific status."""
    bookings = []
    for filepath in PENDING_DIR.glob("*.json"):
        try:
            with open(filepath) as f:
                booking = json.load(f)
                booking["_filepath"] = str(filepath)
                if booking.get("status") == status:
                    bookings.append(booking)
        except Exception as e:
            logger.error(f"Error reading {filepath}: {e}")
    return bookings


def get_pending_bookings() -> list[dict]:
    """Get all pending (auto-book) bookings."""
    return get_bookings_by_status("pending")


def get_optional_bookings() -> list[dict]:
    """Get all optional (manual confirm) bookings."""
    return get_bookings_by_status("optional")


def update_booking_status(filepath: str, status: str, extra: dict = None) -> None:
    """Update a booking's status."""
    with open(filepath) as f:
        booking = json.load(f)

    booking["status"] = status
    booking[f"{status}_at"] = datetime.now().isoformat()

    if extra:
        booking.update(extra)

    with open(filepath, "w") as f:
        json.dump(booking, f, indent=2)


def cancel_booking(name_filter: str = None, include_optional: bool = False) -> int:
    """Cancel pending (and optionally, optional) bookings."""
    cancelled = 0
    statuses = ["pending"]
    if include_optional:
        statuses.append("optional")

    for filepath in PENDING_DIR.glob("*.json"):
        try:
            with open(filepath) as f:
                booking = json.load(f)

            if booking.get("status") not in statuses:
                continue

            if name_filter:
                if name_filter.lower() not in booking.get("venue", "").lower():
                    continue

            update_booking_status(str(filepath), "cancelled")
            logger.info(f"Cancelled booking: {booking['venue']}")
            cancelled += 1

        except Exception as e:
            logger.error(f"Error cancelling {filepath}: {e}")

    return cancelled


def confirm_optional_booking(name_filter: str) -> int:
    """Confirm optional bookings (move to pending for immediate booking)."""
    confirmed = 0

    for filepath in PENDING_DIR.glob("*.json"):
        try:
            with open(filepath) as f:
                booking = json.load(f)

            if booking.get("status") != "optional":
                continue

            if name_filter.lower() not in booking.get("venue", "").lower():
                continue

            # Move to pending status
            update_booking_status(str(filepath), "pending")
            logger.info(f"Confirmed optional booking: {booking['venue']}")
            confirmed += 1

        except Exception as e:
            logger.error(f"Error confirming {filepath}: {e}")

    return confirmed


def clear_old_files() -> None:
    """Remove pending/optional files older than 24 hours."""
    cutoff = datetime.now() - timedelta(hours=24)
    for filepath in PENDING_DIR.glob("*.json"):
        try:
            with open(filepath) as f:
                booking = json.load(f)
            pending_since = datetime.fromisoformat(booking.get("pending_since", "2000-01-01"))
            if pending_since < cutoff:
                filepath.unlink()
                logger.info(f"Cleaned up old file: {filepath}")
        except Exception:
            pass


def find_available_slots(platform: str = "all") -> list[dict]:
    """Find all available slots without booking."""
    logger.info("=" * 60)
    logger.info(f"PHASE 1: Finding available slots at {datetime.now()}")
    logger.info("=" * 60)

    config = load_config()
    preferences = config.get("preferences", {})
    restaurants = config.get("restaurants", [])

    party_size = preferences.get("party_size", 2)
    weeks_ahead = preferences.get("booking_window_weeks", 4)
    slots_config = preferences.get("slots", {})

    target_dates = get_target_dates(weeks_ahead=weeks_ahead)

    # Initialize clients
    resy_client = None
    opentable_client = None

    if platform in ("resy", "all"):
        try:
            resy_client = ResyClient()
            logger.info("Resy client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Resy client: {e}")

    if platform in ("opentable", "all"):
        try:
            opentable_client = OpenTableClient()
            logger.info("OpenTable client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize OpenTable client: {e}")

    available_slots = []

    for restaurant in restaurants:
        name = restaurant.get("name", "Unknown")
        rest_platform = restaurant.get("platform", "").lower()
        slug = restaurant.get("slug", "")
        desired_slots = restaurant.get("slots", [])

        if platform != "all" and rest_platform != platform:
            continue

        logger.info("-" * 40)
        logger.info(f"Checking: {name} ({rest_platform})")

        if rest_platform == "resy" and resy_client:
            client = resy_client
        elif rest_platform == "opentable" and opentable_client:
            client = opentable_client
        else:
            logger.warning(f"Skipping {name}: no client available")
            continue

        # Find first available slot for this restaurant
        found_slot = None

        for slot_type in desired_slots:
            if found_slot:
                break

            slot_config = slots_config.get(slot_type, {})
            start_time = slot_config.get("start", "19:00")
            end_time = slot_config.get("end", "22:00")
            dates = target_dates.get(slot_type, [])

            for date in dates:
                if found_slot:
                    break

                try:
                    result = client.attempt_booking(
                        slug=slug,
                        date=date,
                        party_size=party_size,
                        start_time=start_time,
                        end_time=end_time,
                        dry_run=True,
                    )

                    if result:
                        result["platform"] = rest_platform
                        result["slug"] = slug
                        result["party_size"] = party_size
                        result["start_time"] = start_time
                        result["end_time"] = end_time
                        result["slot_type"] = slot_type
                        found_slot = result
                        logger.info(f"  FOUND: {result['venue']} at {result['time']} on {result['date']}")

                except Exception as e:
                    logger.error(f"  Error checking {name}: {e}")

                time.sleep(1.5)

        if found_slot:
            available_slots.append(found_slot)
        else:
            logger.info(f"  No availability for {name}")

        time.sleep(2)

    return available_slots


def group_slots_by_weekend(slots: list[dict]) -> dict[str, list[dict]]:
    """Group slots by weekend (identified by Saturday date)."""
    grouped = defaultdict(list)
    for slot in slots:
        weekend_id = get_weekend_id(slot["date"])
        grouped[weekend_id].append(slot)
    return dict(grouped)


def select_auto_and_optional(slots: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Select which slots to auto-book vs mark as optional.
    Auto-books ONE per weekend, rest are optional.

    Returns:
        (auto_book_slots, optional_slots)
    """
    by_weekend = group_slots_by_weekend(slots)
    auto_book = []
    optional = []

    for weekend_id in sorted(by_weekend.keys()):
        weekend_slots = by_weekend[weekend_id]

        # First slot for this weekend gets auto-booked
        auto_book.append(weekend_slots[0])

        # Rest are optional
        for slot in weekend_slots[1:]:
            optional.append(slot)

    return auto_book, optional


def wait_for_confirmation(minutes: int = CONFIRMATION_DELAY_MINUTES) -> list[dict]:
    """Wait for the confirmation period, checking for cancellations."""
    logger.info(f"Waiting {minutes} minutes for confirmation...")
    end_time = datetime.now() + timedelta(minutes=minutes)

    reminder_intervals = [30, 15, 5, 1]

    while datetime.now() < end_time:
        remaining = (end_time - datetime.now()).total_seconds() / 60
        pending = get_pending_bookings()

        if not pending:
            logger.info("All bookings cancelled - nothing to book")
            return []

        for interval in reminder_intervals:
            if interval - 0.5 < remaining < interval + 0.5:
                venues = ", ".join(b["venue"] for b in pending)
                send_notification(
                    f"Booking in {interval} min",
                    f"{venues}\nRun: python3 -m bot.main --cancel",
                )
                reminder_intervals.remove(interval)
                break

        time.sleep(60)

    return get_pending_bookings()


def complete_bookings(pending_bookings: list[dict]) -> dict:
    """Complete the actual bookings for pending reservations."""
    logger.info("=" * 60)
    logger.info(f"PHASE 2: Completing bookings at {datetime.now()}")
    logger.info("=" * 60)

    config = load_config()
    party_size = config.get("preferences", {}).get("party_size", 2)

    resy_client = None
    opentable_client = None

    try:
        resy_client = ResyClient()
    except Exception as e:
        logger.error(f"Failed to initialize Resy client: {e}")

    try:
        opentable_client = OpenTableClient()
    except Exception as e:
        logger.error(f"Failed to initialize OpenTable client: {e}")

    results = {"booked": 0, "failed": 0, "bookings": []}

    for booking in pending_bookings:
        venue = booking["venue"]
        platform = booking["platform"]
        slug = booking["slug"]
        date_str = booking["date"]
        filepath = booking["_filepath"]

        logger.info(f"Booking: {venue} on {date_str}")

        if platform == "resy" and resy_client:
            client = resy_client
        elif platform == "opentable" and opentable_client:
            client = opentable_client
        else:
            logger.error(f"No client for {platform}")
            results["failed"] += 1
            continue

        try:
            date = datetime.strptime(date_str, "%Y-%m-%d")

            result = client.attempt_booking(
                slug=slug,
                date=date,
                party_size=booking.get("party_size", party_size),
                start_time=booking.get("start_time", "19:00"),
                end_time=booking.get("end_time", "22:00"),
                dry_run=False,
            )

            if result:
                logger.info(f"  SUCCESS: {result['venue']} at {result['time']}")
                results["booked"] += 1
                results["bookings"].append(result)

                update_booking_status(filepath, "booked")

                notify_booking_success(
                    result["venue"],
                    result["date"],
                    result["time"],
                    party_size,
                )
            else:
                logger.error(f"  FAILED: Could not complete booking")
                results["failed"] += 1
                update_booking_status(filepath, "failed")

        except Exception as e:
            logger.error(f"  Error booking {venue}: {e}")
            results["failed"] += 1

        time.sleep(2)

    return results


def run_bot(dry_run: bool = False, platform: str = "all", skip_wait: bool = False) -> dict:
    """Main bot execution with confirmation flow."""
    clear_old_files()

    # Phase 1: Find available slots
    available = find_available_slots(platform=platform)

    if not available:
        logger.info("No availability found at any restaurant")
        send_notification(
            "No Reservations Available",
            "Checked all restaurants - no slots found",
        )
        return {"found": 0, "booked": 0}

    # Separate into auto-book (1 per weekend) and optional
    auto_book, optional = select_auto_and_optional(available)

    logger.info(f"\nFound {len(available)} total slots:")
    logger.info(f"  Auto-booking: {len(auto_book)} (one per weekend)")
    logger.info(f"  Optional extras: {len(optional)}")

    # Save auto-book slots as pending
    for slot in auto_book:
        save_booking(slot, status="pending")

    # Save optional slots
    for slot in optional:
        save_booking(slot, status="optional")

    # Build notification
    auto_list = "\n".join(f"  {s['venue']}: {s['date']} {s['time']}" for s in auto_book)
    optional_list = "\n".join(f"  {s['venue']}: {s['date']} {s['time']}" for s in optional) if optional else "  (none)"

    logger.info(f"\nAUTO-BOOKING (in {CONFIRMATION_DELAY_MINUTES} min):\n{auto_list}")
    logger.info(f"\nOPTIONAL EXTRAS (run --confirm to add):\n{optional_list}")

    send_notification(
        f"Found {len(auto_book)} reservation(s)!",
        f"Auto-booking in {CONFIRMATION_DELAY_MINUTES} min.\n"
        f"Cancel: --cancel\n"
        f"Add more: --confirm \"Name\"" if optional else "",
    )

    if dry_run:
        logger.info("\nDRY RUN - skipping wait and booking")
        return {"found": len(available), "auto": len(auto_book), "optional": len(optional)}

    if skip_wait:
        logger.info("SKIP_WAIT - proceeding immediately")
        remaining = get_pending_bookings()
    else:
        remaining = wait_for_confirmation(CONFIRMATION_DELAY_MINUTES)

    if not remaining:
        logger.info("All bookings were cancelled")
        return {"found": len(available), "booked": 0}

    # Complete bookings
    results = complete_bookings(remaining)

    notify_run_complete(
        total=len(available),
        booked=results["booked"],
        failed=results["failed"],
    )

    return {
        "found": len(available),
        "booked": results["booked"],
        "failed": results["failed"],
        "optional_remaining": len(get_optional_bookings()),
    }


def validate_venues(platform: str = "all") -> None:
    """Validate that all venue slugs in config resolve correctly."""
    config = load_config()
    restaurants = config.get("restaurants", [])

    resy_client = None
    opentable_client = None

    if platform in ("resy", "all"):
        try:
            resy_client = ResyClient()
        except Exception as e:
            logger.error(f"Failed to initialize Resy client: {e}")

    if platform in ("opentable", "all"):
        try:
            opentable_client = OpenTableClient()
        except Exception as e:
            logger.error(f"Failed to initialize OpenTable client: {e}")

    print("\nValidating venue slugs...\n")

    for restaurant in restaurants:
        name = restaurant.get("name", "Unknown")
        rest_platform = restaurant.get("platform", "").lower()
        slug = restaurant.get("slug", "")

        if platform != "all" and rest_platform != platform:
            continue

        if rest_platform == "resy" and resy_client:
            venue = resy_client.get_venue_by_slug(slug)
            status = f"ID: {venue['venue_id']}" if venue else "NOT FOUND"
            print(f"[RESY] {name}: {status}")
            time.sleep(1)

        elif rest_platform == "opentable" and opentable_client:
            venue = opentable_client.get_restaurant_by_slug(slug)
            status = f"ID: {venue['restaurant_id']}" if venue else "NOT FOUND"
            print(f"[OPENTABLE] {name}: {status}")
            time.sleep(1)


def show_status() -> None:
    """Show all pending and optional bookings."""
    pending = get_pending_bookings()
    optional = get_optional_bookings()

    print("\n" + "=" * 50)
    print("RESERVATION STATUS")
    print("=" * 50)

    if pending:
        print(f"\nAUTO-BOOKING ({len(pending)}):")
        print("  These will be booked automatically.\n")
        for b in sorted(pending, key=lambda x: x["date"]):
            weekend = get_weekend_id(b["date"])
            print(f"  [{weekend}] {b['venue']}: {b['date']} at {b['time']}")
        print(f"\n  To cancel: python3 -m bot.main --cancel")
    else:
        print("\nNo pending auto-bookings.")

    if optional:
        print(f"\nOPTIONAL EXTRAS ({len(optional)}):")
        print("  Run --confirm to add these.\n")
        for b in sorted(optional, key=lambda x: x["date"]):
            weekend = get_weekend_id(b["date"])
            print(f"  [{weekend}] {b['venue']}: {b['date']} at {b['time']}")
        print(f"\n  To add: python3 -m bot.main --confirm \"Restaurant Name\"")
    else:
        print("\nNo optional extras available.")

    print()


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Restaurant Reservation Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m bot.main                     # Find slots, auto-book 1/weekend
  python3 -m bot.main --chat              # Chat with AI assistant
  python3 -m bot.main --add "Carbone"     # Search & add a restaurant
  python3 -m bot.main --status            # Show pending & optional bookings
  python3 -m bot.main --cancel "Cosme"    # Cancel specific restaurant
  python3 -m bot.main --confirm "Cosme"   # Add an optional booking
  python3 -m bot.main --dry-run           # Find slots without booking
  python3 -m bot.main --validate          # Validate venue slugs
        """,
    )
    parser.add_argument("--search", action="store_true", help="Interactive one-off restaurant search")
    parser.add_argument("--dry-run", action="store_true", help="Find slots but don't book")
    parser.add_argument("--chat", action="store_true", help="Chat with AI assistant")
    parser.add_argument("--add", metavar="NAME", help="Search and add a restaurant")
    parser.add_argument("--validate", action="store_true", help="Validate venue slugs only")
    parser.add_argument("--platform", choices=["resy", "opentable", "all"], default="all")
    parser.add_argument("--cancel", nargs="?", const="__all__", metavar="NAME",
                        help="Cancel pending bookings")
    parser.add_argument("--confirm", metavar="NAME",
                        help="Confirm an optional booking (add to this weekend)")
    parser.add_argument("--status", action="store_true", help="Show pending & optional bookings")
    parser.add_argument("--skip-wait", action="store_true", help="Skip confirmation wait (testing)")

    args = parser.parse_args()

    if args.search:
        from .search import run_search
        run_search()
    elif args.chat:
        from .assistant import run_chat
        run_chat()
    elif args.add:
        from .search import interactive_add
        interactive_add(args.add)
    elif args.cancel:
        name_filter = None if args.cancel == "__all__" else args.cancel
        count = cancel_booking(name_filter)
        print(f"\nCancelled {count} booking(s).\n")
        show_status()
    elif args.confirm:
        count = confirm_optional_booking(args.confirm)
        if count:
            print(f"\nConfirmed {count} booking(s). Booking now...\n")
            # Immediately book the confirmed reservation
            pending = get_pending_bookings()
            if pending:
                results = complete_bookings(pending)
                print(f"Booked: {results['booked']}, Failed: {results['failed']}")
        else:
            print(f"\nNo optional bookings found matching '{args.confirm}'")
            show_status()
    elif args.status:
        show_status()
    elif args.validate:
        validate_venues(platform=args.platform)
    else:
        run_bot(dry_run=args.dry_run, platform=args.platform, skip_wait=args.skip_wait)


if __name__ == "__main__":
    main()
