"""
Resy API client for finding and booking reservations.
"""

import os
import time
import logging
from datetime import datetime
from typing import Optional

import requests
from dotenv import load_dotenv

from .scheduler import format_date_for_api, is_time_in_range

load_dotenv()

logger = logging.getLogger(__name__)

RESY_API_BASE = "https://api.resy.com"


class ResyClient:
    """Client for interacting with the Resy API."""

    def __init__(self):
        self.api_key = os.getenv("RESY_API_KEY")
        self.auth_token = os.getenv("RESY_AUTH_TOKEN")

        if not self.api_key:
            raise ValueError("RESY_API_KEY not found in environment")
        if not self.auth_token:
            raise ValueError("RESY_AUTH_TOKEN not found in environment")

        # Parse API key from format: ResyAPI api_key="xxx"
        if 'api_key="' in self.api_key:
            self.api_key = self.api_key.split('api_key="')[1].rstrip('"')

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f'ResyAPI api_key="{self.api_key}"',
            "X-Resy-Auth-Token": self.auth_token,
            "X-Resy-Universal-Auth": self.auth_token,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://resy.com",
            "Referer": "https://resy.com/",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        })

    def get_venue_by_slug(self, slug: str) -> Optional[dict]:
        """
        Look up a venue by its URL slug.

        Args:
            slug: The venue slug (e.g., "gramercy-tavern")

        Returns:
            Venue data dict or None if not found
        """
        url = f"{RESY_API_BASE}/3/venue"
        params = {"url_slug": slug, "location": "ny"}

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            if "id" in data and "resy" in data["id"]:
                venue_id = data["id"]["resy"]
                name = data.get("name", slug)
                logger.info(f"Found venue: {name} (ID: {venue_id})")
                return {
                    "venue_id": venue_id,
                    "name": name,
                    "slug": slug,
                }
            else:
                logger.warning(f"Venue not found for slug: {slug}")
                return None

        except requests.RequestException as e:
            if hasattr(e, 'response') and e.response is not None and e.response.status_code == 404:
                logger.debug(f"Venue not on Resy: {slug}")
            else:
                logger.error(f"Error looking up venue {slug}: {e}")
            return None

    def find_available_slots(
        self,
        venue_id: int,
        date: datetime,
        party_size: int,
        start_time: str,
        end_time: str,
    ) -> list[dict]:
        """
        Find available reservation slots for a venue.

        Args:
            venue_id: The Resy venue ID
            date: Target date
            party_size: Number of guests
            start_time: Earliest acceptable time (HH:MM)
            end_time: Latest acceptable time (HH:MM)

        Returns:
            List of available slot dicts
        """
        url = f"{RESY_API_BASE}/4/find"
        params = {
            "lat": 40.7589,
            "long": -73.9851,
            "day": format_date_for_api(date),
            "party_size": party_size,
            "venue_id": venue_id,
        }

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            venues = data.get("results", {}).get("venues", [])
            if not venues:
                logger.info(f"No availability for venue {venue_id} on {date.date()}")
                return []

            slots = venues[0].get("slots", [])
            matching_slots = []

            for slot in slots:
                slot_time = slot.get("date", {}).get("start", "")
                if slot_time:
                    # Extract time portion (format: "2026-04-11 19:30:00")
                    time_part = slot_time.split(" ")[1][:5] if " " in slot_time else ""
                    if time_part and is_time_in_range(time_part, start_time, end_time):
                        matching_slots.append({
                            "time": time_part,
                            "config_token": slot.get("config", {}).get("token", ""),
                            "date": format_date_for_api(date),
                            "type": slot.get("config", {}).get("type", ""),
                        })

            logger.info(f"Found {len(matching_slots)} matching slots for venue {venue_id}")
            return matching_slots

        except requests.RequestException as e:
            logger.error(f"Error finding slots for venue {venue_id}: {e}")
            return []

    def get_booking_details(self, config_token: str, date: str, party_size: int) -> Optional[dict]:
        """
        Get booking details required to complete a reservation.

        Args:
            config_token: The slot configuration token
            date: Reservation date (YYYY-MM-DD)
            party_size: Number of guests

        Returns:
            Booking details dict or None
        """
        url = f"{RESY_API_BASE}/3/details"
        params = {
            "config_id": config_token,
            "day": date,
            "party_size": party_size,
        }

        try:
            response = self.session.get(url, params=params)
            response.raise_for_status()
            data = response.json()

            book_token = data.get("book_token", {}).get("value")
            if book_token:
                return {
                    "book_token": book_token,
                    "payment_method_id": data.get("user", {}).get("payment_methods", [{}])[0].get("id"),
                }
            return None

        except requests.RequestException as e:
            logger.error(f"Error getting booking details: {e}")
            return None

    def book_reservation(
        self,
        book_token: str,
        payment_method_id: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Complete a reservation booking.

        Args:
            book_token: Token from booking details
            payment_method_id: User's payment method ID (optional)

        Returns:
            Booking confirmation dict or None
        """
        url = f"{RESY_API_BASE}/3/book"
        data = {"book_token": book_token}

        if payment_method_id:
            data["struct_payment_method"] = f'{{"id": {payment_method_id}}}'

        try:
            response = self.session.post(url, data=data)
            response.raise_for_status()
            result = response.json()

            resy_token = result.get("resy_token")
            if resy_token:
                logger.info(f"Booking successful! Token: {resy_token}")
                return {
                    "resy_token": resy_token,
                    "reservation_id": result.get("reservation_id"),
                }
            else:
                logger.error(f"Booking failed: {result}")
                return None

        except requests.RequestException as e:
            logger.error(f"Error booking reservation: {e}")
            return None

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
        Full booking flow: look up venue, find slots, book first available.

        Args:
            slug: Venue URL slug
            date: Target date
            party_size: Number of guests
            start_time: Earliest acceptable time
            end_time: Latest acceptable time
            dry_run: If True, don't actually book

        Returns:
            Booking result dict or None
        """
        # Step 1: Get venue ID
        venue = self.get_venue_by_slug(slug)
        if not venue:
            logger.error(f"Could not find venue: {slug}")
            return None

        time.sleep(1)  # Rate limiting

        # Step 2: Find available slots
        slots = self.find_available_slots(
            venue["venue_id"], date, party_size, start_time, end_time
        )
        if not slots:
            logger.info(f"No available slots for {venue['name']} on {date.date()}")
            return None

        # Step 3: Try to book the first available slot
        slot = slots[0]
        logger.info(f"Attempting to book {venue['name']} at {slot['time']} on {slot['date']}")

        if dry_run:
            logger.info("DRY RUN - skipping actual booking")
            return {
                "venue": venue["name"],
                "date": slot["date"],
                "time": slot["time"],
                "dry_run": True,
            }

        time.sleep(1)  # Rate limiting

        # Step 4: Get booking details
        details = self.get_booking_details(slot["config_token"], slot["date"], party_size)
        if not details:
            logger.error("Could not get booking details")
            return None

        time.sleep(1)  # Rate limiting

        # Step 5: Complete the booking
        result = self.book_reservation(
            details["book_token"],
            details.get("payment_method_id"),
        )

        if result:
            return {
                "venue": venue["name"],
                "date": slot["date"],
                "time": slot["time"],
                "resy_token": result["resy_token"],
            }
        return None


def validate_venue_slug(slug: str) -> bool:
    """
    Check if a venue slug is valid.

    Args:
        slug: Venue URL slug

    Returns:
        True if venue exists
    """
    try:
        client = ResyClient()
        venue = client.get_venue_by_slug(slug)
        return venue is not None
    except Exception as e:
        logger.error(f"Error validating slug {slug}: {e}")
        return False


if __name__ == "__main__":
    # Test venue lookup
    logging.basicConfig(level=logging.INFO)

    print("Testing Resy client...")
    print()

    try:
        client = ResyClient()

        # Test a few venue lookups
        test_slugs = ["gramercy-tavern", "cosme", "invalid-venue-xyz"]

        for slug in test_slugs:
            print(f"Looking up: {slug}")
            venue = client.get_venue_by_slug(slug)
            if venue:
                print(f"  Found: {venue['name']} (ID: {venue['venue_id']})")
            else:
                print(f"  Not found")
            time.sleep(1.5)  # Rate limiting

    except Exception as e:
        print(f"Error: {e}")
