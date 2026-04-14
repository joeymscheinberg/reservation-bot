"""
Date scheduling logic for reservation bot.
Determines which Friday/Saturday dates fall within the booking window.
"""

from datetime import datetime, timedelta
from typing import Optional


def get_target_dates(
    weeks_ahead: int = 4,
    reference_date: Optional[datetime] = None
) -> dict[str, list[datetime]]:
    """
    Calculate target dates for Friday dinner, Saturday brunch, and Saturday dinner
    within the specified booking window.

    Args:
        weeks_ahead: Number of weeks to look ahead (default 4)
        reference_date: Starting date (default today)

    Returns:
        Dict mapping slot types to lists of dates
    """
    if reference_date is None:
        reference_date = datetime.now()

    today = reference_date.date()
    end_date = today + timedelta(weeks=weeks_ahead)

    # Find the first Friday and Saturday from today
    days_until_friday = (4 - today.weekday()) % 7  # Friday = 4
    days_until_saturday = (5 - today.weekday()) % 7  # Saturday = 5

    # If today is the target day, include it
    if days_until_friday == 0:
        first_friday = today
    else:
        first_friday = today + timedelta(days=days_until_friday)

    if days_until_saturday == 0:
        first_saturday = today
    else:
        first_saturday = today + timedelta(days=days_until_saturday)

    fridays = []
    saturdays = []

    # Collect all Fridays in window
    current = first_friday
    while current <= end_date:
        fridays.append(datetime.combine(current, datetime.min.time()))
        current += timedelta(weeks=1)

    # Collect all Saturdays in window
    current = first_saturday
    while current <= end_date:
        saturdays.append(datetime.combine(current, datetime.min.time()))
        current += timedelta(weeks=1)

    return {
        "friday_dinner": fridays,
        "saturday_brunch": saturdays,
        "saturday_dinner": saturdays,
    }


def parse_time_range(start: str, end: str) -> tuple[int, int]:
    """
    Parse time strings into hour integers.

    Args:
        start: Start time like "19:00"
        end: End time like "22:00"

    Returns:
        Tuple of (start_hour, end_hour)
    """
    start_hour = int(start.split(":")[0])
    end_hour = int(end.split(":")[0])
    return start_hour, end_hour


def is_time_in_range(time_str: str, start: str, end: str) -> bool:
    """
    Check if a time falls within a range.

    Args:
        time_str: Time to check, e.g. "19:30" or "7:30 PM"
        start: Range start like "19:00"
        end: Range end like "22:00"

    Returns:
        True if time is within range
    """
    # Handle both 24h and 12h formats
    try:
        # Try 24h format first (e.g., "19:30")
        if ":" in time_str and len(time_str.split(":")[0]) <= 2:
            parts = time_str.replace(" ", "").upper()
            if "PM" in parts or "AM" in parts:
                # 12h format
                dt = datetime.strptime(parts, "%I:%M%p")
            else:
                dt = datetime.strptime(time_str, "%H:%M")
            check_hour = dt.hour
            check_minute = dt.minute
        else:
            return False
    except ValueError:
        return False

    start_hour, start_min = map(int, start.split(":"))
    end_hour, end_min = map(int, end.split(":"))

    check_total = check_hour * 60 + check_minute
    start_total = start_hour * 60 + start_min
    end_total = end_hour * 60 + end_min

    return start_total <= check_total <= end_total


def format_date_for_api(date: datetime) -> str:
    """Format a date for API requests (YYYY-MM-DD)."""
    return date.strftime("%Y-%m-%d")


if __name__ == "__main__":
    # Test the scheduler
    from datetime import datetime

    # Test with today's date (2026-04-11, a Saturday)
    test_date = datetime(2026, 4, 11)
    dates = get_target_dates(weeks_ahead=4, reference_date=test_date)

    print("Target dates for 4-week window starting 2026-04-11:")
    print()

    for slot, date_list in dates.items():
        print(f"{slot}:")
        for d in date_list:
            print(f"  {d.strftime('%Y-%m-%d %A')}")
        print()

    # Test time range checking
    print("Time range tests:")
    print(f"  19:30 in 19:00-22:00? {is_time_in_range('19:30', '19:00', '22:00')}")
    print(f"  7:30 PM in 19:00-22:00? {is_time_in_range('7:30 PM', '19:00', '22:00')}")
    print(f"  11:30 in 11:00-13:00? {is_time_in_range('11:30', '11:00', '13:00')}")
    print(f"  18:00 in 19:00-22:00? {is_time_in_range('18:00', '19:00', '22:00')}")
