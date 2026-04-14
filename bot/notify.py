"""
macOS notification system for reservation bot.
"""

import subprocess
import logging

logger = logging.getLogger(__name__)


def send_notification(
    title: str,
    message: str,
    sound: bool = True,
) -> bool:
    """
    Send a macOS notification using osascript.

    Args:
        title: Notification title
        message: Notification message body
        sound: Whether to play a sound

    Returns:
        True if notification was sent successfully
    """
    # Escape quotes for AppleScript
    title_escaped = title.replace('"', '\\"')
    message_escaped = message.replace('"', '\\"')

    cmd = [
        "terminal-notifier",
        "-title", title,
        "-message", message,
        "-appIcon", "/Applications/Calendar.app/Contents/Resources/App.icns",
    ]
    if sound:
        cmd += ["-sound", "Glass"]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=5,
        )

        if result.returncode == 0:
            logger.info(f"Notification sent: {title}")
            return True
        else:
            logger.error(f"Notification failed: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.error("Notification timed out")
        return False
    except Exception as e:
        logger.error(f"Error sending notification: {e}")
        return False


def notify_booking_success(
    restaurant_name: str,
    date: str,
    time: str,
    party_size: int = 2,
) -> bool:
    """
    Send a notification for a successful booking.

    Args:
        restaurant_name: Name of the restaurant
        date: Reservation date
        time: Reservation time
        party_size: Number of guests

    Returns:
        True if notification was sent
    """
    title = f"Reservation Booked!"
    message = f"{restaurant_name}\n{date} at {time} for {party_size}"
    return send_notification(title, message)


def notify_booking_failure(
    restaurant_name: str,
    reason: str,
) -> bool:
    """
    Send a notification for a failed booking attempt.

    Args:
        restaurant_name: Name of the restaurant
        reason: Why the booking failed

    Returns:
        True if notification was sent
    """
    title = f"Booking Failed: {restaurant_name}"
    message = reason
    return send_notification(title, message, sound=False)


def notify_run_complete(
    total: int,
    booked: int,
    failed: int,
) -> bool:
    """
    Send a summary notification when the bot run completes.

    Args:
        total: Total restaurants attempted
        booked: Number of successful bookings
        failed: Number of failed attempts

    Returns:
        True if notification was sent
    """
    if booked > 0:
        title = f"Reservations Complete!"
        message = f"Booked {booked}/{total} restaurants"
    else:
        title = "No Reservations Available"
        message = f"Checked {total} restaurants, no slots found"

    return send_notification(title, message)


if __name__ == "__main__":
    # Test notifications
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("Testing macOS notifications...")
    print()

    # Test success notification
    print("Sending success notification...")
    notify_booking_success("Gramercy Tavern", "2026-04-17", "7:30 PM", 2)

    # Test run complete notification
    print("Sending run complete notification...")
    notify_run_complete(total=5, booked=2, failed=1)

    print()
    print("Check your notification center!")
