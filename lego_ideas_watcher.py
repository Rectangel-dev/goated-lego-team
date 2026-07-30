#!/usr/bin/env python3
"""
LEGO Ideas profile watcher -> Discord webhook notifier.

Watches one or more LEGO Ideas profiles and posts a Discord notification
whenever one of them publishes a new creation (Product Idea, Challenge
Entry, or Activity Entry).

Setup:
    1. pip install requests beautifulsoup4
    2. Fill in DISCORD_WEBHOOK_URL below.
    3. Fill in PROFILES below with the profile URLs you want to track.
    4. Run: python3 lego_ideas_watcher.py

The script checks every CHECK_INTERVAL_SECONDS (default 60s) and stores
what it has already seen in seen_creations.json (next to this script), so
you won't get re-notified for old creations if you restart it. On the very
first run for a profile, it just records the current creations as a
baseline silently (no notification spam for stuff that already existed).
"""

import json
import time
import os
import logging
from pathlib import Path
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------
# CONFIG - edit these
# ----------------------------------------------------------------------

# Paste your Discord webhook URL here (Server Settings -> Integrations ->
# Webhooks -> New Webhook -> Copy Webhook URL)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# List the LEGO Ideas profiles you want to watch.
# You can use either the profile URL or just the UUID/alias.
PROFILES = [
    "https://ideas.lego.com/profile/630ace2f-6101-4a1d-b9ac-e8db9bdd788c",  # starteratr
    "https://ideas.lego.com/profile/27715096-a403-46f3-8095-efff3da0a857",  # Sunmere
]

CHECK_INTERVAL_SECONDS = 300

STATE_FILE = Path(__file__).parent / "seen_creations.json"

# ----------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("lego-watcher")

HEADERS = {
    # A normal browser UA so the site serves us the full server-rendered page.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            log.warning("seen_creations.json was corrupt, starting fresh.")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2))


def fetch_profile_creations(profile_url: str) -> tuple[str, list[dict]]:
    """
    Returns (display_name, list_of_creations) for a profile URL.
    Each creation dict has: id (the URL), title, entity_type, image (or None).
    """
    resp = requests.get(profile_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Display name shown in the profile header
    name_tag = soup.select_one("h3[title]")
    display_name = name_tag["title"].strip() if name_tag else profile_url

    grid = soup.find(attrs={"data-testid": "profile-builds-tab-grid"})
    creations = []
    if grid is None:
        return display_name, creations

    for link_tag in grid.select("a.Card_cardLink__GW5kw[href]"):
        href = link_tag.get("href", "").strip()
        aria_label = link_tag.get("aria-label", "").strip()
        entity_type, _, title = aria_label.partition(":")
        entity_type = entity_type.strip() or "Creation"
        title = title.strip() or "(untitled)"

        # Try to grab a thumbnail image from the sibling card content
        img_url = None
        card = link_tag.find_parent(attrs={"data-testid": "card-image-container"})
        if card:
            img_tag = card.find("img")
            if img_tag and img_tag.get("data-src"):
                img_url = img_tag["data-src"].split(" ")[0]

        if href:
            creations.append(
                {
                    "id": href,
                    "title": title,
                    "entity_type": entity_type,
                    "image": img_url,
                }
            )

    return display_name, creations


def send_discord_notification(profile_name: str, creation: dict) -> None:
    if not DISCORD_WEBHOOK_URL or "PASTE_YOUR" in DISCORD_WEBHOOK_URL:
        log.error("DISCORD_WEBHOOK_URL is not set! Edit the script and add your webhook URL.")
        return

    embed = {
        "title": creation["title"],
        "url": creation["id"],
        "description": f"**{creation['entity_type']}** by **{profile_name}**",
        "color": 0xFFC400,  # LEGO yellow
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "LEGO Ideas watcher"},
    }
    if creation.get("image"):
        embed["image"] = {"url": creation["image"]}

    payload = {
        "username": "LEGO Ideas Watcher",
        "embeds": [embed],
        "content": f"🧱 New creation from **{profile_name}**: [{creation['title']}]({creation['id']})",
    }

    try:
        r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=15)
        r.raise_for_status()
        log.info(f"Notified Discord about: {creation['title']} ({creation['id']})")
    except requests.RequestException as e:
        log.error(f"Failed to send Discord notification: {e}")


def check_profiles(state: dict) -> None:
    for profile_url in PROFILES:
        try:
            display_name, creations = fetch_profile_creations(profile_url)
        except requests.RequestException as e:
            log.error(f"Failed to fetch {profile_url}: {e}")
            continue

        seen_ids = set(state.get(profile_url, {}).get("seen_ids", []))
        is_first_run = profile_url not in state

        new_creations = [c for c in creations if c["id"] not in seen_ids]

        if is_first_run:
            log.info(
                f"[{display_name}] First run - baselining {len(creations)} existing creation(s), no notifications sent."
            )
        else:
            for creation in new_creations:
                log.info(f"[{display_name}] New creation found: {creation['title']}")
                send_discord_notification(display_name, creation)

        # Update state with everything currently seen
        all_ids = list({c["id"] for c in creations} | seen_ids)
        state[profile_url] = {
            "display_name": display_name,
            "seen_ids": all_ids,
            "last_checked": datetime.now(timezone.utc).isoformat(),
        }

    save_state(state)


def main() -> None:
    log.info("Starting LEGO Ideas watcher.")
    log.info(f"Watching {len(PROFILES)} profile(s), checking every {CHECK_INTERVAL_SECONDS}s.")
    state = load_state()

    try:
    check_profiles(state)
except Exception as e:
    log.exception(f"Unexpected error during check cycle: {e}")


if __name__ == "__main__":
    main()
