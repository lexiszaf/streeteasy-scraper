#!/usr/bin/env python3
"""
StreetEasy UWS Apartment Scraper
Scrapes listings daily and emails new ones to lexiszaf@gmail.com
"""

import json
import os
import smtplib
import time
import random
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────
ALERT_EMAIL   = "lexiszaf@gmail.com"
FROM_EMAIL    = os.environ.get("GMAIL_USER", "")
APP_PASSWORD  = os.environ.get("GMAIL_APP_PASSWORD", "")

MAX_RENT      = 7000
MIN_BEDS      = 1
MAX_BEDS      = 3
MIN_BATHS     = 1

# 89th–103rd St UWS bounding box (approximate lat/lng)
NEIGHBORHOODS = ["upper-west-side"]

SEEN_FILE     = Path("data/seen_listings.json")
# ──────────────────────────────────────────────────────────────────────────────


def load_seen() -> set:
    if SEEN_FILE.exists():
        return set(json.loads(SEEN_FILE.read_text()))
    return set()


def save_seen(seen: set):
    SEEN_FILE.parent.mkdir(exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))


def random_delay(min_s=1.5, max_s=4.0):
    time.sleep(random.uniform(min_s, max_s))


def build_search_urls() -> list[str]:
    """Build StreetEasy search URLs for 1BR, 2BR, 3BR."""
    urls = []
    for beds in range(MIN_BEDS, MAX_BEDS + 1):
        # StreetEasy URL format for rentals
        url = (
            f"https://streeteasy.com/for-rent/upper-west-side/"
            f"beds:{beds}|price:-{MAX_RENT}|bathrooms:{MIN_BATHS}"
        )
        urls.append((beds, url))
    return urls


def scrape_listings(page, url: str, beds: int) -> list[dict]:
    listings = []

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        random_delay(2, 5)

        # Scroll to trigger lazy loading
        page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        random_delay(1, 2)
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        random_delay(1, 2)

        # Wait for listing cards
        page.wait_for_selector('[data-testid="listing-card"], .listingCard, article[data-id]', timeout=15000)

        cards = page.query_selector_all('[data-testid="listing-card"], .listingCard, article[data-id]')

        for card in cards:
            try:
                listing = parse_card(card, beds)
                if listing:
                    listings.append(listing)
            except Exception:
                continue

        # Handle pagination — grab page 2 if present
        next_btn = page.query_selector('a[aria-label="Next page"], .pagination-next a')
        if next_btn and len(listings) > 0:
            random_delay(2, 4)
            next_btn.click()
            page.wait_for_selector('[data-testid="listing-card"], .listingCard, article[data-id]', timeout=15000)
            cards2 = page.query_selector_all('[data-testid="listing-card"], .listingCard, article[data-id]')
            for card in cards2:
                try:
                    listing = parse_card(card, beds)
                    if listing:
                        listings.append(listing)
                except Exception:
                    continue

    except Exception as e:
        print(f"  Error scraping {url}: {e}")

    return listings


def parse_card(card, beds: int) -> dict | None:
    """Extract listing data from a card element."""
    # Try to get the listing ID / URL
    link = card.query_selector("a[href*='/rental/']")
    if not link:
        link = card.query_selector("a[href*='/for-rent/']")
    if not link:
        return None

    href = link.get_attribute("href") or ""
    if not href.startswith("http"):
        href = "https://streeteasy.com" + href

    # Listing ID from URL
    listing_id = href.rstrip("/").split("/")[-1]
    if not listing_id:
        return None

    # Filter by street range (89th–103rd)
    address_el = card.query_selector('[data-testid="listing-card-address"], .listingCard-addressLabel, .address')
    address = address_el.inner_text().strip() if address_el else ""

    if not is_in_range(address):
        return None

    # Price
    price_el = card.query_selector('[data-testid="listing-card-price"], .listingCard-priceLabel, .price')
    price_text = price_el.inner_text().strip() if price_el else ""
    price = parse_price(price_text)
    if price and price > MAX_RENT:
        return None

    # Baths
    details_el = card.query_selector('[data-testid="listing-card-details"], .listingCard-details, .details')
    details_text = details_el.inner_text().strip() if details_el else ""

    return {
        "id": listing_id,
        "url": href,
        "address": address,
        "price": price,
        "price_text": price_text,
        "beds": beds,
        "details": details_text,
        "found_at": datetime.now().isoformat(),
    }


def is_in_range(address: str) -> bool:
    """Check if address is between 89th and 103rd street."""
    if not address:
        return True  # include unknowns, better to over-notify

    import re
    # Look for street numbers like "90th", "West 95", "W 102", "102nd", etc.
    match = re.search(r'(?:West\s+|W\.?\s+|East\s+|E\.?\s+)?(\d{2,3})(?:st|nd|rd|th)?\s+St', address, re.IGNORECASE)
    if match:
        street_num = int(match.group(1))
        return 89 <= street_num <= 103

    # If we can't parse it, include it (don't miss listings)
    return True


def parse_price(price_text: str) -> int | None:
    import re
    match = re.search(r'[\$]?([\d,]+)', price_text)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def send_email(new_listings: list[dict]):
    if not FROM_EMAIL or not APP_PASSWORD:
        print("⚠️  No email credentials set — printing listings instead:")
        for l in new_listings:
            print(f"  {l['address']} | {l['price_text']} | {l['beds']}BR | {l['url']}")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🏠 {len(new_listings)} New UWS Listing{'s' if len(new_listings) != 1 else ''} — {datetime.now().strftime('%b %d')}"
    msg["From"]    = FROM_EMAIL
    msg["To"]      = ALERT_EMAIL

    html = build_email_html(new_listings)
    msg.attach(MIMEText(html, "html"))

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
        beds_label = f"{l['beds']} Bed{'s' if l['beds'] != 1 else ''}"
        price_label = l["price_text"] or "Price N/A"
        address = l["address"] or "Address N/A"
        details = l["details"] or ""

        tour_subject = f"Tour Request - {address}"
        tour_body = f"Hi, I'm interested in the apartment at {address} listed on StreetEasy. Could we schedule a tour? Also, is the layout flexible (would you allow a temporary wall to add a room)? Thank you!"
        mailto = f"mailto:?subject={tour_subject.replace(' ', '%20')}&body={tour_body.replace(' ', '%20').replace(',', '%2C')}"

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

    return f"""
    <!DOCTYPE html>
    <html>
    <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f5f5;margin:0;padding:20px;">
      <div style="max-width:600px;margin:0 auto;">
        <div style="background:#0066cc;border-radius:12px;padding:24px;margin-bottom:20px;text-align:center;">
          <h1 style="color:#fff;margin:0;font-size:24px;">🏠 New UWS Listings</h1>
          <p style="color:#cce0ff;margin:8px 0 0;">{datetime.now().strftime('%A, %B %d, %Y')} &nbsp;·&nbsp; 89th–103rd St &nbsp;·&nbsp; Up to $7,000/mo</p>
        </div>
        <p style="color:#444;margin-bottom:20px;">{len(listings)} new listing{'s' if len(listings) != 1 else ''} found since your last check:</p>
        {cards_html}
        <p style="color:#999;font-size:12px;text-align:center;margin-top:24px;">UWS Scraper · Runs daily · Filters: 1–3BR, 1+ bath, ≤$7,000</p>
      </div>
    </body>
    </html>
    """


def main():
    print(f"🔍 Starting StreetEasy scrape — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    seen = load_seen()
    new_listings = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        # Mask webdriver flag
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        page = context.new_page()

        for beds, url in build_search_urls():
            print(f"  Scraping {beds}BR listings...")
            listings = scrape_listings(page, url, beds)
            print(f"    Found {len(listings)} listings on page")

            for listing in listings:
                if listing["id"] not in seen:
                    new_listings.append(listing)
                    seen.add(listing["id"])

            random_delay(3, 7)  # polite delay between searches

        browser.close()

    print(f"\n📬 {len(new_listings)} new listings to report.")

    if new_listings:
        send_email(new_listings)
    else:
        print("No new listings — nothing to send.")

    save_seen(seen)
    print("✅ Done.")


if __name__ == "__main__":
    main()
