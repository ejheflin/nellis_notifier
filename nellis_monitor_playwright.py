#!/usr/bin/env python3
"""
Nellis Auction monitor (Playwright) -> generates an RSS file with ONE RSS <item> PER LISTING (with images).

- Uses your saved session (nellis_storage.json) so location/filtering matches your browser.
- Ignores Nellis "suggested items" when the page explicitly says:
    "0 items found when searching for"
- Extracts listing links (/p/.../<id>) from each search results page
- Extracts an image URL for each listing (best-effort) from the search results page
- Writes nellis_feed.xml with one <item> per listing

Install:
  pip install playwright
  playwright install chromium

First run once to save your session (creates nellis_storage.json):
  python save_session.py
"""

import hashlib
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional
from xml.sax.saxutils import escape as xml_escape

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# -------------------------
# CONFIG: URLs to monitor
# -------------------------
URLS: List[str] = [
    "https://nellisauction.com/search?Taxonomy%20Level%201=Automotive&Location%20Name=Katy&query=h11%20led%20bulbs",
    "https://nellisauction.com/search?query=pontoon&Taxonomy%20Level%201=Outdoors%20%26%20Sports&Location%20Name=Katy",
    "https://nellisauction.com/search?Location%20Name=Katy&query=48v%20dc%20charger",
    "https://nellisauction.com/search?Taxonomy%20Level%201=Automotive&Taxonomy%20Level%202=Automotive%20Accessories&query=light%20bar%20led",
    "https://nellisauction.com/search?Taxonomy%20Level%201=Beauty%20%26%20Personal%20Care&query=wahl%20clipper&Location%20Name=Katy",
    "https://nellisauction.com/search?Location+Name=Katy&query=marine+wire+awg",
    "https://nellisauction.com/search?Location+Name=Katy&Taxonomy+Level+1=Smart+Home&query=zigbee",
    "https://nellisauction.com/search?Taxonomy+Level+1=Furniture+%26+Appliances&Taxonomy+Level+2=Kitchen+Appliances&query=refrigerator+freezer&Star+Rating=4.0&Star+Rating=5.0",
    "https://nellisauction.com/search?Taxonomy+Level+1=Home+Improvement&query=reverse+osmosis+water",
    "https://nellisauction.com/search?query=12v+bench+power+supply",
    "https://nellisauction.com/search?query=mens+13+Allen+Edmonds",
    "https://nellisauction.com/search?query=national+tree+company+flocked+9",
    
]

# Optional friendly names for RSS titles (otherwise URL is used)
NAMES: Dict[str, str] = {
    URLS[0]: "Katy Automotive: h11 led bulbs",
    URLS[1]: "Katy Outdoors & Sports: pontoon",
    URLS[2]: "Katy: 48v dc charger",
    URLS[3]: "Automotive Accessories: light bar led",
    URLS[4]: "Katy Beauty & Personal Care: wahl clipper",
}

STORAGE_STATE_FILE = "nellis_storage.json"
RSS_FILE = "docs/nellis_feed.xml"

# If True, RSS includes a "no results" placeholder item per search (usually leave False)
INCLUDE_ZERO_COUNT_IN_RSS = False

# Nellis "real zero results" phrase (ignore suggested items)
ZERO_RESULTS_RE = re.compile(r"0\s+items\s+found\s+when\s+searching\s+for", re.IGNORECASE)

# Timeouts
NAV_TIMEOUT_MS = 45000
WAIT_FOR_RESULTS_MS = 15000

# Listing link pattern: /p/.../<numeric_id>
ITEM_HREF_RE = re.compile(r"^/p/.*/(\d+)$")

def now_rfc2822() -> str:
    return datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")

def stable_id(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()

def normalize_img_url(src: str) -> str:
    src = (src or "").strip()
    if not src:
        return ""
    if src.startswith("//"):
        return "https:" + src
    if src.startswith("/"):
        return "https://nellisauction.com" + src
    return src

def build_rss(items: List[Dict[str, str]]) -> str:
    channel_title = "Nellis Auction Listings"
    channel_link = "https://nellisauction.com/"
    channel_desc = "One RSS item per matching Nellis Auction listing (from your saved searches)."

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

        # Description contains HTML inside CDATA
        parts.append(f"<description>{it['description']}</description>")

        # Some readers use enclosure for thumbnails
        if it.get("image_url"):
            parts.append(f'<enclosure url="{xml_escape(it["image_url"])}" type="image/jpeg" />')

        parts.append(f"<pubDate>{xml_escape(it['pubDate'])}</pubDate>")
        parts.append("</item>")

    parts.append("</channel>")
    parts.append("</rss>")
    return "\n".join(parts)

def wait_for_results_or_no_results(page) -> None:
    """Wait for either listings or a no-results hint; don't fail hard."""
    try:
        page.wait_for_selector("a[href^='/p/']", timeout=WAIT_FOR_RESULTS_MS)
        return
    except PlaywrightTimeoutError:
        pass

    no_result_selectors = [
        "text=/0\\s+items\\s+found/i",
        "text=/no\\s+results/i",
        "text=/no\\s+items/i",
    ]
    for sel in no_result_selectors:
        try:
            page.wait_for_selector(sel, timeout=2000)
            return
        except PlaywrightTimeoutError:
            continue

def extract_listing_links(page) -> List[str]:
    """Return absolute URLs for listing links on the page."""
    hrefs = page.eval_on_selector_all(
        "a[href^='/p/']",
        "els => els.map(e => e.getAttribute('href')).filter(Boolean)"
    )

    links: List[str] = []
    for href in hrefs:
        if ITEM_HREF_RE.match(href):
            links.append("https://nellisauction.com" + href)

    # De-dupe while preserving order
    seen = set()
    unique = []
    for l in links:
        if l not in seen:
            seen.add(l)
            unique.append(l)
    return unique

def extract_listing_images(page) -> Dict[str, str]:
    """
    Build mapping listing_url -> image_url from the search page.

    Best-effort:
    - find <a href="/p/.../<id>"> then look for an <img> within (or near) it
    - use src, data-src, or srcset
    """
    js = r"""
    () => {
      const out = {};
      const anchors = Array.from(document.querySelectorAll("a[href^='/p/']"));

      for (const a of anchors) {
        const href = a.getAttribute("href");
        if (!href) continue;
        if (!/^\/p\/.*\/\d+$/.test(href)) continue;

        const abs = "https://nellisauction.com" + href;

        const img = a.querySelector("img") || a.closest("div")?.querySelector("img");
        if (!img) continue;

        let src = img.getAttribute("src") || img.getAttribute("data-src") || "";
        if (!src) {
          const srcset = img.getAttribute("srcset") || "";
          if (srcset) src = srcset.split(",")[0].trim().split(" ")[0];
        }
        if (!src) continue;

        out[abs] = src;
      }
      return out;
    }
    """
    raw_map: Dict[str, str] = page.evaluate(js)
    out: Dict[str, str] = {}
    for link, src in raw_map.items():
        out[link] = normalize_img_url(src)
    return out

def extract_listing_titles(page) -> Dict[str, str]:
    """
    Best-effort mapping listing_url -> title text from the search page.
    This is intentionally loose because markup can change.

    We attempt:
    - anchor text
    - aria-label
    - closest heading
    """
    js = r"""
    () => {
      const out = {};
      const anchors = Array.from(document.querySelectorAll("a[href^='/p/']"));

      for (const a of anchors) {
        const href = a.getAttribute("href");
        if (!href) continue;
        if (!/^\/p\/.*\/\d+$/.test(href)) continue;
        const abs = "https://nellisauction.com" + href;

        let title = (a.getAttribute("aria-label") || "").trim();
        if (!title) title = (a.textContent || "").trim();

        // If anchor text is empty, try nearest heading text
        if (!title) {
          const h = a.closest("div")?.querySelector("h1,h2,h3,h4,h5,h6");
          if (h) title = (h.textContent || "").trim();
        }

        // Avoid extremely short junk
        if (title && title.length >= 3) out[abs] = title;
      }
      return out;
    }
    """
    raw_map: Dict[str, str] = page.evaluate(js)
    # Strip whitespace
    return {k: (v or "").strip() for k, v in raw_map.items() if (v or "").strip()}

def make_description_html(search_name: str, listing_url: str, image_url: str) -> str:
    img_html = ""
    if image_url:
        img_html = f'<p><img src="{xml_escape(image_url)}" alt="listing image" style="max-width:100%; height:auto;" /></p>'

    html = f"""
<![CDATA[
<p><em>Matched search:</em> {xml_escape(search_name)}</p>
<p><a href="{xml_escape(listing_url)}">{xml_escape(listing_url)}</a></p>
{img_html}
]]>
""".strip()
    return html

def main():
    rss_items: List[Dict[str, str]] = []
    seen_listing_urls: set[str] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(storage_state=STORAGE_STATE_FILE)
        context.set_default_navigation_timeout(NAV_TIMEOUT_MS)

        for search_url in URLS:
            search_name = NAMES.get(search_url, search_url)
            page = context.new_page()

            try:
                page.goto(search_url, wait_until="domcontentloaded")
            except PlaywrightTimeoutError:
                print(f"[WARN] Navigation timed out for: {search_name}")

            wait_for_results_or_no_results(page)

            final_url = page.url
            body_text = page.inner_text("body")

            if ZERO_RESULTS_RE.search(body_text):
                print(f"[CHECK] {search_name} -> 0 real results (explicit zero-results; ignoring suggestions)")
                page.close()

                if INCLUDE_ZERO_COUNT_IN_RSS:
                    rss_items.append({
                        "title": f"{search_name} — 0 results",
                        "link": final_url or search_url,
                        "guid": stable_id(f"{search_url}|0"),
                        "description": f"<![CDATA[<p>No results for this search.</p>]]>",
                        "pubDate": now_rfc2822(),
                        "image_url": "",
                    })
                continue

            listing_links = extract_listing_links(page)
            images_map = extract_listing_images(page)
            titles_map = extract_listing_titles(page)

            print(f"[CHECK] {search_name} -> {len(listing_links)} listing link(s) ({final_url})")
            if listing_links:
                print("    [DEBUG] first match:", listing_links[0])

            for listing_url in listing_links:
                # De-dupe across searches (same item might match multiple queries)
                if listing_url in seen_listing_urls:
                    continue
                seen_listing_urls.add(listing_url)

                title = titles_map.get(listing_url)
                if not title:
                    # Fallback: derive title from URL slug
                    slug = listing_url.split("/p/", 1)[-1]
                    title = slug.replace("-", " ").split("/", 1)[0].strip() or "Nellis listing"

                image_url = images_map.get(listing_url, "")

                rss_items.append({
                    "title": title,
                    "link": listing_url,
                    # Stable per listing URL so readers don't re-notify unless listing changes
                    "guid": stable_id(listing_url),
                    "description": make_description_html(search_name, listing_url, image_url),
                    "pubDate": now_rfc2822(),
                    "image_url": image_url,
                })

            page.close()

        browser.close()

    with open(RSS_FILE, "w", encoding="utf-8") as f:
        f.write(build_rss(rss_items))

    print(f"[OK] Wrote RSS: {RSS_FILE} (items in feed: {len(rss_items)})")

if __name__ == "__main__":
    main()
