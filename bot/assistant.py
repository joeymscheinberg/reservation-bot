"""
Natural language assistant for managing reservations.
Uses Claude to interpret commands and manage bookings conversationally.
"""

import os
import json
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
import anthropic

from .main import (
    get_pending_bookings,
    get_optional_bookings,
    cancel_booking,
    confirm_optional_booking,
    complete_bookings,
    get_weekend_id,
    clear_old_files,
)

load_dotenv()

# Tool definitions for Claude
TOOLS = [
    {
        "name": "show_status",
        "description": "Show all current pending reservations (auto-booking) and optional extras available. Call this when the user wants to see what's scheduled or available.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "cancel_reservation",
        "description": "Cancel a pending reservation. Use when the user wants to cancel, remove, or not book a specific restaurant. Pass the restaurant name or 'all' to cancel everything.",
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_name": {
                    "type": "string",
                    "description": "Restaurant name to cancel (partial match works), or 'all' to cancel all pending",
                },
            },
            "required": ["restaurant_name"],
        },
    },
    {
        "name": "confirm_reservation",
        "description": "Confirm an optional reservation and book it immediately. Use when the user wants to add, book, or confirm an optional extra.",
        "input_schema": {
            "type": "object",
            "properties": {
                "restaurant_name": {
                    "type": "string",
                    "description": "Restaurant name to confirm (partial match works)",
                },
            },
            "required": ["restaurant_name"],
        },
    },
    {
        "name": "swap_reservation",
        "description": "Swap the auto-booked restaurant for a weekend with an optional one. Cancels the current auto-book and confirms the optional.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cancel_restaurant": {
                    "type": "string",
                    "description": "Restaurant to cancel",
                },
                "confirm_restaurant": {
                    "type": "string",
                    "description": "Restaurant to book instead",
                },
            },
            "required": ["cancel_restaurant", "confirm_restaurant"],
        },
    },
]


def format_booking(booking: dict) -> str:
    """Format a booking for display."""
    return f"{booking['venue']} - {booking['date']} at {booking['time']}"


def format_booking_with_weekend(booking: dict) -> str:
    """Format a booking with weekend info."""
    weekend = get_weekend_id(booking["date"])
    day = datetime.strptime(booking["date"], "%Y-%m-%d").strftime("%A")
    return f"[Weekend {weekend}] {booking['venue']} - {day} {booking['date']} at {booking['time']}"


def tool_show_status() -> str:
    """Show current reservation status."""
    pending = get_pending_bookings()
    optional = get_optional_bookings()

    lines = []

    if pending:
        lines.append(f"AUTO-BOOKING ({len(pending)}):")
        lines.append("These will book automatically after the wait period:\n")
        for b in sorted(pending, key=lambda x: x["date"]):
            lines.append(f"  - {format_booking_with_weekend(b)}")
    else:
        lines.append("No pending auto-bookings.")

    lines.append("")

    if optional:
        lines.append(f"OPTIONAL EXTRAS ({len(optional)}):")
        lines.append("Say 'confirm [name]' to add any of these:\n")
        for b in sorted(optional, key=lambda x: x["date"]):
            lines.append(f"  - {format_booking_with_weekend(b)}")
    else:
        lines.append("No optional extras available.")

    return "\n".join(lines)


def tool_cancel_reservation(restaurant_name: str) -> str:
    """Cancel a reservation."""
    if restaurant_name.lower() == "all":
        count = cancel_booking(None)
        if count:
            return f"Cancelled all {count} pending reservation(s)."
        return "No pending reservations to cancel."

    # Check if it exists first
    pending = get_pending_bookings()
    matching = [b for b in pending if restaurant_name.lower() in b["venue"].lower()]

    if not matching:
        available = ", ".join(b["venue"] for b in pending) if pending else "none"
        return f"No pending reservation found matching '{restaurant_name}'. Pending: {available}"

    count = cancel_booking(restaurant_name)
    cancelled_names = ", ".join(b["venue"] for b in matching)
    return f"Cancelled: {cancelled_names}"


def tool_confirm_reservation(restaurant_name: str) -> str:
    """Confirm an optional reservation."""
    optional = get_optional_bookings()
    matching = [b for b in optional if restaurant_name.lower() in b["venue"].lower()]

    if not matching:
        available = ", ".join(b["venue"] for b in optional) if optional else "none"
        return f"No optional booking found matching '{restaurant_name}'. Available: {available}"

    count = confirm_optional_booking(restaurant_name)
    if count:
        # Book immediately
        pending = get_pending_bookings()
        matching_pending = [b for b in pending if restaurant_name.lower() in b["venue"].lower()]

        if matching_pending:
            results = complete_bookings(matching_pending)
            if results["booked"]:
                booked = matching_pending[0]
                return f"Booked {booked['venue']} for {booked['date']} at {booked['time']}!"
            else:
                return f"Confirmed but booking failed - the slot may no longer be available."

    return f"Could not confirm '{restaurant_name}'."


def tool_swap_reservation(cancel_restaurant: str, confirm_restaurant: str) -> str:
    """Swap one reservation for another."""
    # Cancel first
    cancel_result = tool_cancel_reservation(cancel_restaurant)
    if "No pending" in cancel_result:
        return cancel_result

    # Then confirm
    confirm_result = tool_confirm_reservation(confirm_restaurant)

    return f"{cancel_result}\n{confirm_result}"


def execute_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool and return the result."""
    if tool_name == "show_status":
        return tool_show_status()
    elif tool_name == "cancel_reservation":
        return tool_cancel_reservation(tool_input["restaurant_name"])
    elif tool_name == "confirm_reservation":
        return tool_confirm_reservation(tool_input["restaurant_name"])
    elif tool_name == "swap_reservation":
        return tool_swap_reservation(
            tool_input["cancel_restaurant"],
            tool_input["confirm_restaurant"],
        )
    else:
        return f"Unknown tool: {tool_name}"


def get_system_prompt() -> str:
    """Get the system prompt for the assistant."""
    return """You are a friendly reservation assistant helping manage restaurant bookings.

Your job is to help the user:
- See their current reservations (pending auto-books and optional extras)
- Cancel reservations they don't want
- Confirm optional extras they want to add
- Swap one restaurant for another

Be conversational and helpful. When showing status, summarize it naturally.
When the user makes a request, use the appropriate tool and confirm what you did.

Keep responses concise - this is a quick chat interface, not a lengthy conversation.

If the user says something like "what do I have" or "show me", call show_status.
If they say "cancel X" or "don't book X" or "remove X", call cancel_reservation.
If they say "add X" or "book X" or "confirm X" or "yes to X", call confirm_reservation.
If they say "swap X for Y" or "instead of X, book Y", call swap_reservation."""


class ReservationAssistant:
    """Chat-based reservation assistant using Claude."""

    def __init__(self):
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key or api_key == "your-api-key-here":
            raise ValueError(
                "ANTHROPIC_API_KEY not set in .env file.\n"
                "Get your key at https://console.anthropic.com/\n"
                "Then add it to .env: ANTHROPIC_API_KEY=sk-ant-..."
            )

        self.client = anthropic.Anthropic(api_key=api_key)
        self.messages = []

    def chat(self, user_message: str) -> str:
        """Send a message and get a response."""
        self.messages.append({"role": "user", "content": user_message})

        response = self.client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=get_system_prompt(),
            tools=TOOLS,
            messages=self.messages,
        )

        # Process tool calls if any
        while response.stop_reason == "tool_use":
            # Extract tool calls
            tool_results = []
            assistant_content = response.content

            for block in response.content:
                if block.type == "tool_use":
                    tool_result = execute_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": tool_result,
                    })

            # Add assistant message and tool results
            self.messages.append({"role": "assistant", "content": assistant_content})
            self.messages.append({"role": "user", "content": tool_results})

            # Get next response
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=get_system_prompt(),
                tools=TOOLS,
                messages=self.messages,
            )

        # Extract text response
        text_response = ""
        for block in response.content:
            if hasattr(block, "text"):
                text_response += block.text

        self.messages.append({"role": "assistant", "content": response.content})

        return text_response


def run_chat():
    """Run the interactive chat interface."""
    print("\n" + "=" * 50)
    print("RESERVATION ASSISTANT")
    print("=" * 50)
    print("\nHey! I'm your reservation assistant.")
    print("Tell me what you'd like to do with your bookings.")
    print("Type 'quit' to exit.\n")

    try:
        assistant = ReservationAssistant()
    except ValueError as e:
        print(f"Error: {e}")
        return

    # Show initial status
    status = tool_show_status()
    if "No pending" in status and "No optional" in status:
        print("You don't have any pending reservations right now.")
        print("Run the bot first: python3 -m bot.main\n")
    else:
        print("Here's what you have:\n")
        print(status)
        print()

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q", "bye"):
            print("\nGoodbye!")
            break

        response = assistant.chat(user_input)
        print(f"\nAssistant: {response}\n")


if __name__ == "__main__":
    run_chat()
