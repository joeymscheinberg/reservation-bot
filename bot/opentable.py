"""
OpenTable API client for finding and booking reservations.
"""

import os
import time
import logging
import re
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from .scheduler import format_date_for_api, is_time_in_range

load_dotenv()

logger = logging.getLogger(__name__)

OPENTABLE_API_BASE = "https://www.opentable.com"


class OpenTableClient:
    """Client for interacting with OpenTable's web API."""

    def __init__(self):
        self.cookies_raw = os.getenv("OT_AUTH_TOKEN", "")

        if not self.cookies_raw:
            raise ValueError("OT_AUTH_TOKEN not found in environment")

        # Parse cookies string into dict
        self.cookies = self._parse_cookies(self.cookies_raw)

        self.session = requests.Session()
        self.session.cookies.update(self.cookies)
        self.session.headers.update({
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/json",
            "Origin": "https://www.opentable.com",
            "Referer": "https://www.opentable.com/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })

    def _parse_cookies(self, cookies_str: str) -> dict:
        """Parse a cookie string into a dict."""
        cookies = {}
        for part in cookies_str.split(";"):
            part = part.strip()
            if "=" in part:
                key, _, value = part.partition("=")
                cookies[key.strip()] = value.strip()
        return cookies

    def get_restaurant_by_slug(self, slug: str) -> Optional[dict]:
        """
        Look up a restaurant by its URL slug.

        Args:
            slug: The restaurant slug (e.g., "hawksmoor-nyc")

        Returns:
            Restaurant data dict or None if not found
        """
        url = f"{OPENTABLE_API_BASE}/restref/api/profile"
        params = {"slug": slug, "corrid": ""}

        try:
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            if "rid" in data:
                logger.info(f"Found restaurant: {data.get('name')} (ID: {data['rid']})")
                return {
                    "restaurant_id": data["rid"],
                    "name": data.get("name", slug),
                    "slug": slug,
                }
            else:
                logger.warning(f"Restaurant not found for slug: {slug}")
                return None

        except requests.RequestException as e:
            logger.error(f"Error looking up restaurant {slug}: {e}")
            return None

    def find_available_slots(
        self,
        restaurant_id: int,
        date: datetime,
        party_size: int,
        start_time: str,
        end_time: str,
    ) -> list[dict]:
        """
        Find available reservation slots for a restaurant.

        Args:
            restaurant_id: The OpenTable restaurant ID
            date: Target date
            party_size: Number of guests
            start_time: Earliest acceptable time (HH:MM)
            end_time: Latest acceptable time (HH:MM)

        Returns:
            List of available slot dicts
        """
        # Convert time range to OpenTable format (they use time slots)
        # We'll request availability and filter
        url = f"{OPENTABLE_API_BASE}/restref/api/availability"

        date_str = format_date_for_api(date)

        params = {
            "rid": restaurant_id,
            "partySize": party_size,
            "dateTime": f"{date_str}T19:00",  # Start with dinner time
            "enableFutureAvailability": "false",
            "corrid": "",
        }

        try:
            response = self.session.get(url, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()

            slots = data.get("availability", {}).get("times", [])
            matching_slots = []

            for slot in slots:
                slot_time = slot.get("time", "")  # Format like "7:30 PM"
                if slot_time and is_time_in_range(slot_time, start_time, end_time):
                    matching_slots.append({
                        "time": slot_time,
                        "date": date_str,
                        "slot_hash": slot.get("slotHash", ""),
                        "slot_lock_hash": slot.get("slotLockHash", ""),
                    })

            logger.info(f"Found {len(matching_slots)} matching slots for restaurant {restaurant_id}")
            return matching_slots

        except requests.RequestException as e:
            logger.error(f"Error finding slots for restaurant {restaurant_id}: {e}")
            return []

    def lock_slot(self, slot_hash: str) -> Optional[str]:
        """
        Lock a slot for booking.

        Args:
            slot_hash: The slot hash from availability

        Returns:
            Lock ID or None
        """
        url = f"{OPENTABLE_API_BASE}/restref/api/slot/lock"
        data = {"slotHash": slot_hash}

        try:
            response = self.session.post(url, json=data, timeout=15)
            response.raise_for_status()
            result = response.json()
            return result.get("slotLockHash")

        except requests.RequestException as e:
            logger.error(f"Error locking slot: {e}")
            return None

    def book_reservation(
        self,
        restaurant_id: int,
        slot_lock_hash: str,
        party_size: int,
        date: str,
        time_str: str,
    ) -> Optional[dict]:
        """
        Complete a reservation booking.

        Args:
            restaurant_id: Restaurant ID
            slot_lock_hash: Locked slot hash
            party_size: Number of guests
            date: Reservation date
            time_str: Reservation time

        Returns:
            Booking confirmation dict or None
        """
        url = f"{OPENTABLE_API_BASE}/restref/api/reservation"

        data = {
            "rid": restaurant_id,
            "slotLockHash": slot_lock_hash,
            "partySize": party_size,
            "dateTime": f"{date}T{self._convert_to_24h(time_str)}",
            "isConfirmed": True,
        }

        try:
            response = self.session.post(url, json=data, timeout=15)
            response.raise_for_status()
            result = response.json()

            conf_number = result.get("confirmationNumber")
            if conf_number:
                logger.info(f"Booking successful! Confirmation: {conf_number}")
                return {
                    "confirmation_number": conf_number,
                    "restaurant_id": restaurant_id,
                }
            else:
                logger.error(f"Booking failed: {result}")
                return None

        except requests.RequestException as e:
            logger.error(f"Error booking reservation: {e}")
            return None

    def _convert_to_24h(self, time_str: str) -> str:
        """Convert 12h time to 24h format."""
        try:
            dt = datetime.strptime(time_str.strip(), "%I:%M %p")
            return dt.strftime("%H:%M")
        except ValueError:
            return time_str

    def attempt_booking(
        self,
        slug: str,
        date: datetime,
        party_size: int,
        start_time: str,
        end_time: str,
        dry_run: bool = False,
    ) -> Optional[dict]:
        """
        Full booking flow: look up restaurant, find slots, book first available.

        Args:
            slug: Restaurant URL slug
            date: Target date
            party_size: Number of guests
            start_time: Earliest acceptable time
            end_time: Latest acceptable time
            dry_run: If True, don't actually book

        Returns:
            Booking result dict or None
        """
        # Step 1: Get restaurant ID
        restaurant = self.get_restaurant_by_slug(slug)
        if not restaurant:
            logger.error(f"Could not find restaurant: {slug}")
            return None

        time.sleep(1)  # Rate limiting

        # Step 2: Find available slots
        slots = self.find_available_slots(
            restaurant["restaurant_id"], date, party_size, start_time, end_time
        )
        if not slots:
            logger.info(f"No available slots for {restaurant['name']} on {date.date()}")
            return None

        # Step 3: Try to book the first available slot
        slot = slots[0]
        logger.info(f"Attempting to book {restaurant['name']} at {slot['time']} on {slot['date']}")

        if dry_run:
            logger.info("DRY RUN - skipping actual booking")
            return {
                "venue": restaurant["name"],
                "date": slot["date"],
                "time": slot["time"],
                "dry_run": True,
            }

        time.sleep(1)  # Rate limiting

        # Step 4: Lock the slot
        lock_hash = slot.get("slot_lock_hash") or self.lock_slot(slot["slot_hash"])
        if not lock_hash:
            logger.error("Could not lock slot")
            return None

        time.sleep(1)  # Rate limiting

        # Step 5: Complete the booking
        result = self.book_reservation(
            restaurant["restaurant_id"],
            lock_hash,
            party_size,
            slot["date"],
            slot["time"],
        )

        if result:
            return {
                "venue": restaurant["name"],
                "date": slot["date"],
                "time": slot["time"],
                "confirmation_number": result["confirmation_number"],
            }
        return None


if __name__ == "__main__":
    # Test restaurant lookup
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("Testing OpenTable client...")
    print()

    try:
        client = OpenTableClient()

        # Test restaurant lookups
        test_slugs = ["hawksmoor-nyc", "le-veau-dor-new-york"]

        for slug in test_slugs:
            print(f"Looking up: {slug}")
            restaurant = client.get_restaurant_by_slug(slug)
            if restaurant:
                print(f"  Found: {restaurant['name']} (ID: {restaurant['restaurant_id']})")
            else:
                print(f"  Not found")
            time.sleep(1.5)

    except Exception as e:
        print(f"Error: {e}")
