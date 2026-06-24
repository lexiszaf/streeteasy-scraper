#!/usr/bin/env python3
"""
StreetEasy UWS Apartment Scraper
Uses ScraperAPI to bypass bot detection.
Emails new listings to lexiszaf@gmail.com
"""

import json
import os
import smtplib
import time
import random
import re
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
ALERT_EMAIL    = "lexiszaf@gmail.com"
FROM_EMAIL     = os.environ.get("GMAIL_USER", "")
APP_PASSWORD   = os.environ.get("GMAIL_APP_PASSWORD", "")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "")

MAX_RENT       = 7000
MIN_BEDS       = 1
MAX_BEDS       = 3

SEEN_FILE      = Path("data/seen_listings.json")
# ──────────────────────────────────────────────────────────────────────────────


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    SEEN_FILE.parent.mkdir(exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))


def scraper_api_get(url: str) -> str | None:
    """Fetch a URL through ScraperAPI to bypass bot detection."""
    api_url = "http://api.scraperapi.com"
    params = {
        "api_key": SCRAPER_API_KEY,
        "url": url,
        "render": "true",   # renders JS like a real browser
    }
    try:
        resp = requests.get(api_url, params=params, timeout=120)
        if resp.status_code == 200:
            return resp.text
        else:
            print(f"    ScraperAPI returned {resp.status_code} for {url}")
            return None
    except Exception as e:
        print(f"    ScraperAPI error: {e}")
        return None


def build_search_urls() -> list[tuple]:
    urls = []
    for beds in range(MIN_BEDS, MAX_BEDS + 1):
        url = (
            f"https://streeteasy.com/for-rent/upper-west-side"
            f"?beds_min={beds}&beds_max={beds}&price_max={MAX_RENT}"
        )
        urls.append((beds, url))
    return urls


def scrape_listings(beds: int, url: str) -> list[dict]:
    print(f"    Fetching: {url}")
    html = scraper_api_get(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    # Check if blocked
    title = soup.title.string if soup.title else ""
    print(f"    Page title: '{title}'")
    if "denied" in title.lower() or "captcha" in title.lower():
        print("    ⚠️  Still blocked even with ScraperAPI")
        return []

    listings = []

    # StreetEasy listing cards
    cards = (
        soup.find_all("article", attrs={"data-id": True}) or
        soup.find_all("li", attrs={"data-id": True}) or
        soup.find_all(attrs={"data-testid": "listing-card"}) or
        soup.find_all("div", attrs={"data-listing-id": True}) or
        soup.select(".listingCard") or
        soup.select("article")
    )

    print(f"    Found {len(cards)} cards")

    for card in cards:
        try:
            listing = parse_card(card, beds)
            if listing:
                listings.append(listing)
        except Exception:
            continue

    return listings


def parse_card(card, beds: int) -> dict | None:
    # URL / ID
    link = card.find("a", href=re.compile(r"/rental/|/for-rent/"))
    if not link:
        link = card.find("a", href=True)
    if not link:
        return None

    href = link.get("href", "")
    if not href.startswith("http"):
        href = "https://streeteasy.com" + href

    listing_id = href.rstrip("/").split("/")[-1]
    if not listing_id or listing_id in ("for-rent", "upper-west-side"):
        return None

    # Address
    address = ""
    for sel in ["[data-testid='listing-card-address']", ".listingCard-addressLabel", "address"]:
        el = card.select_one(sel)
        if el:
            address = el.get_text(strip=True)
            break
    if not address:
        # fallback: grab first h2/h3
        for tag in ["h2", "h3", "h4"]:
            el = card.find(tag)
            if el:
                address = el.get_text(strip=True)
                break

    if not is_in_range(address):
        return None

    # Price
    price_text = ""
    for sel in ["[data-testid='listing-card-price']", ".listingCard-priceLabel", ".price"]:
        el = card.select_one(sel)
        if el:
            price_text = el.get_text(strip=True)
            break
    if not price_text:
        # look for any element containing a dollar sign
        for el in card.find_all(string=re.compile(r'\$[\d,]+')):
            price_text = el.strip()
            break

    price = parse_price(price_text)
    if price and price > MAX_RENT:
        return None

    # Details
    details = ""
    for sel in ["[data-testid='listing-card-details']", ".listingCard-details", ".details"]:
        el = card.select_one(sel)
        if el:
            details = el.get_text(strip=True)
            break

    return {
        "id": listing_id,
        "url": href,
        "address": address,
        "price": price,
        "price_text": price_text,
        "beds": beds,
        "details": details,
        "found_at": datetime.now().isoformat(),
    }


def is_in_range(address: str) -> bool:
    if not address:
        return True
    match = re.search(
        r'(?:West\s+|W\.?\s+|East\s+|E\.?\s+)?(\d{2,3})(?:st|nd|rd|th)?\s+St',
        address, re.IGNORECASE
    )
    if match:
        return 89 <= int(match.group(1)) <= 103
    return True


def parse_price(price_text: str) -> int | None:
    match = re.search(r'\$?([\d,]+)', price_text or "")
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def send_email(new_listings: list[dict]):
    if not FROM_EMAIL or not APP_PASSWORD:
        print("⚠️  No email credentials — printing listings:")
        for l in new_listings:
            print(f"  {l['address']} | {l['price_text']} | {l['beds']}BR | {l['url']}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🏠 {len(new_listings)} New UWS Listing{'s' if len(new_listings) != 1 else ''} — {datetime.now().strftime('%b %d')}"
    msg["From"]    = FROM_EMAIL
    msg["To"]      = ALERT_EMAIL
    msg.attach(MIMEText(build_email_html(new_listings), "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(FROM_EMAIL, APP_PASSWORD)
            server.sendmail(FROM_EMAIL, ALERT_EMAIL, msg.as_string())
        print(f"✅ Email sent with {len(new_listings)} listings.")
    except Exception as e:
        print(f"❌ Email failed: {e}")


def build_email_html(listings: list[dict]) -> str:
    cards_html = ""
    for l in listings:
        beds_label  = f"{l['beds']} Bed{'s' if l['beds'] != 1 else ''}"
        price_label = l["price_text"] or "Price N/A"
        address     = l["address"] or "Address N/A"
        details     = l["details"] or ""
        tour_subject = urllib.parse.quote(f"Tour Request - {address}")
        tour_body    = urllib.parse.quote(
            f"Hi, I'm interested in the apartment at {address} listed on StreetEasy. "
            f"Could we schedule a tour? Also, is the layout flexible "
            f"(would you allow a temporary wall to add a room)? Thank you!"
        )
        mailto = f"mailto:?subject={tour_subject}&body={tour_body}"

        cards_html += f"""
        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:12px;padding:20px;margin-bottom:16px;">
          <div style="font-size:18px;font-weight:700;color:#1a1a1a;margin-bottom:4px;">{address}</div>
          <div style="font-size:22px;font-weight:800;color:#0066cc;margin-bottom:8px;">{price_label}/mo</div>
          <div style="font-size:14px;color:#555;margin-bottom:12px;">{beds_label} &nbsp;·&nbsp; {details}</div>
          <div style="display:flex;gap:10px;flex-wrap:wrap;">
            <a href="{l['url']}" style="background:#0066cc;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;">View on StreetEasy →</a>
            <a href="{mailto}" style="background:#f0f7ff;color:#0066cc;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;font-size:14px;border:1px solid #0066cc;">✉️ Request Tour</a>
          </div>
        </div>
        """

    return f"""<!DOCTYPE html>
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;margin:0;padding:20px;">
      <div style="max-width:600px;margin:0 auto;">
        <div style="background:#0066cc;border-radius:12px;padding:24px;margin-bottom:20px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:24px;">🏠 New UWS Listings</h1>
          <p style="color:#cce0ff;margin:8px 0 0;">{datetime.now().strftime('%A, %B %d, %Y')} &nbsp;·&nbsp; 89th–103rd St &nbsp;·&nbsp; Up to $7,000/mo</p>
        </div>
        <p style="color:#444;margin-bottom:20px;">{len(listings)} new listing{'s' if len(listings) != 1 else ''} since your last check:</p>
        {cards_html}
        <p style="color:#999;font-size:12px;text-align:center;margin-top:24px;">UWS Scraper · Runs daily at 8am ET · 1–3BR · ≤$7,000</p>
      </div>
    </body>
    </html>"""


def main():
    print(f"🔍 Starting StreetEasy scrape — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    if not SCRAPER_API_KEY:
        print("❌ SCRAPER_API_KEY not set!")
        return

    seen = load_seen()
    new_listings = []

    for beds, url in build_search_urls():
        print(f"  Scraping {beds}BR listings...")
        listings = scrape_listings(beds, url)
        print(f"    → {len(listings)} listings found")

        for listing in listings:
            if listing["id"] not in seen:
                new_listings.append(listing)
                seen.add(listing["id"])

        time.sleep(random.uniform(2, 4))

    print(f"\n📬 {len(new_listings)} new listings to report.")

    if new_listings:
        send_email(new_listings)
    else:
        print("No new listings — nothing to send.")

    save_seen(seen)
    print("✅ Done.")


if __name__ == "__main__":
    main()