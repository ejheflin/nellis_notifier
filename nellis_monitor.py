#!/usr/bin/env python3
"""
Nellis Auction search monitor -> generates an RSS feed and optionally prints alerts.

How it works:
- Fetch each URL
- Parse "<N> items found" from HTML
- Update an RSS XML file with one entry per URL that currently has results
- Optional: alert only when transitioning from 0 -> >0, using a small state.json file

Install:
  pip install requests

Run:
  python nellis_monitor.py

Schedule (cron example, every 5 min):
  */5 * * * * /usr/bin/python3 /path/to/nellis_monitor.py >> /path/to/monitor.log 2>&1
"""

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
import requests
from xml.sax.saxutils import escape as xml_escape

# -------------------------
# CONFIG: Put your URLs here
# -------------------------
URLS: List[str] = [
    # Your example:
    "https://nellisauction.com/search?Taxonomy%20Level%201=Automotive&Location%20Name=Katy&query=h11%20led%20bulbs",

    # Add more URLs below:
    # "https://nellisauction.com/search?Location%20Name=Katy&query=dewalt",
    # "https://nellisauction.com/search?Taxonomy%20Level%201=Electronics&Location%20Name=Katy&query=iphone",
]

# Friendly names for feed titles (optional). If missing, the URL is used.
NAMES: Dict[str, str] = {
    URLS[0]: "Katy Automotive: h11 led bulbs",
}

# Output files
STATE_FILE = "nellis_state.json"      # tiny state to avoid repeat alerts
RSS_FILE = "nellis_feed.xml"

# Behavior
CHECK_INTERVAL_SECONDS = 0            # 0 = run once and exit; >0 = loop forever
ALERT_ON_TRANSITION_ONLY = True       # True: only alert on 0 -> >0; False: alert anytime >0
REQUEST_TIMEOUT = 25

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NelllisMonitor/1.0; +https://example.invalid)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "close",
}

# Regex to find result count in page HTML
COUNT_RE = re.compile(r"(\d+)\s+items\s+found", re.IGNORECASE)

# -------------------------
# Helpers
# -------------------------

def now_rfc2822() -> str:
    # RFC 2822 date for RSS
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

def stable_id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def load_state() -> Dict[str, int]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # stored as {url: last_count}
        return {k: int(v) for k, v in data.items()}
    except Exception:
        return {}

def save_state(state: Dict[str, int]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)

def fetch_count(session: requests.Session, url: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Returns (count, final_url). count is None on failure.
    """
    try:
        resp = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        resp.raise_for_status()
        html = resp.text
        m = COUNT_RE.search(html)
        if not m:
            # If the HTML doesn't contain the count, treat as 0 rather than error.
            return 0, resp.url
        return int(m.group(1)), resp.url
    except Exception as e:
        print(f"[ERROR] Fetch failed for {url}: {e}")
        return None, None

def build_rss(items: List[Dict[str, str]]) -> str:
    """
    items: list of dicts with keys: title, link, guid, description, pubDate
    """
    channel_title = "Nellis Auction Search Monitor"
    channel_link = "https://nellisauction.com/"
    channel_desc = "Alerts when your Nellis Auction search URLs have results."

    parts = []
    parts.append('<?xml version="1.0" encoding="UTF-8"?>')
    parts.append('<rss version="2.0">')
    parts.append("<channel>")
    parts.append(f"<title>{xml_escape(channel_title)}</title>")
    parts.append(f"<link>{xml_escape(channel_link)}</link>")
    parts.append(f"<description>{xml_escape(channel_desc)}</description>")
    parts.append(f"<lastBuildDate>{xml_escape(now_rfc2822())}</lastBuildDate>")

    for it in items:
        parts.append("<item>")
        parts.append(f"<title>{xml_escape(it['title'])}</title>")
        parts.append(f"<link>{xml_escape(it['link'])}</link>")
        parts.append(f"<guid isPermaLink=\"false\">{xml_escape(it['guid'])}</guid>")
        parts.append(f"<description>{xml_escape(it['description'])}</description>")
        parts.append(f"<pubDate>{xml_escape(it['pubDate'])}</pubDate>")
        parts.append("</item>")

    parts.append("</channel>")
    parts.append("</rss>")
    return "\n".join(parts)

def notify_console(url: str, name: str, count: int) -> None:
    # Replace/extend this with Discord/Slack webhook, email, SMS, etc.
    print(f"[ALERT] {name}: {count} results -> {url}")

# -------------------------
# Main check
# -------------------------

def check_once() -> None:
    state = load_state()
    rss_items: List[Dict[str, str]] = []

    with requests.Session() as session:
        for url in URLS:
            name = NAMES.get(url, url)
            count, final_url = fetch_count(session, url)
            if count is None:
                # Keep previous state on failure
                continue

            last = state.get(url, 0)

            # Decide whether to alert
            should_alert = False
            if count > 0:
                if ALERT_ON_TRANSITION_ONLY:
                    should_alert = (last == 0)
                else:
                    should_alert = True

            if should_alert:
                notify_console(url, name, count)

            # Add to RSS if currently has results
            if count > 0:
                rss_items.append({
                    "title": f"{name} — {count} results available",
                    "link": final_url or url,
                    "guid": stable_id(f"{url}|{count}|{datetime.now(timezone.utc).date()}"),
                    "description": f"{count} items found for this search. Open link to view results.",
                    "pubDate": now_rfc2822(),
                })

            # Update state
            state[url] = count

    # Write RSS file
    rss_xml = build_rss(rss_items)
    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(rss_xml)

    save_state(state)
    print(f"[OK] Wrote RSS: {RSS_FILE} (items in feed: {len(rss_items)})")

def main():
    if CHECK_INTERVAL_SECONDS and CHECK_INTERVAL_SECONDS > 0:
        while True:
            check_once()
            time.sleep(CHECK_INTERVAL_SECONDS)
    else:
        check_once()

if __name__ == "__main__":
    main()
