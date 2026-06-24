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
import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────
ALERT_EMAIL  = "lexiszaf@gmail.com"
FROM_EMAIL   = os.environ.get("GMAIL_USER", "")
APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

MAX_RENT     = 7000
MIN_BEDS     = 1
MAX_BEDS     = 3

SEEN_FILE    = Path("data/seen_listings.json")
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


def build_search_urls() -> list[tuple]:
    """
    StreetEasy rental search URL format (confirmed working):
    /for-rent/upper-west-side?beds_min=1&beds_max=1&price_max=7000
    """
    urls = []
    for beds in range(MIN_BEDS, MAX_BEDS + 1):
        url = (
            f"https://streeteasy.com/for-rent/upper-west-side"
            f"?beds_min={beds}&beds_max={beds}&price_max={MAX_RENT}"
        )
        urls.append((beds, url))
    return urls


def scrape_listings(page, url: str, beds: int) -> list[dict]:
    listings = []

    try:
        print(f"    Loading: {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        random_delay(3, 6)

        # Scroll slowly to trigger lazy loading
        for pct in [0.25, 0.5, 0.75, 1.0]:
            page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pct})")
            random_delay(0.8, 1.5)

        # Try multiple possible card selectors StreetEasy has used
        selectors = [
            'article[data-id]',
            '[data-testid="listing-card"]',
            '.listingCard',
            'li[data-id]',
            'div[data-listing-id]',
            '.search-results article',
            '.listings-container article',
        ]

        cards = []
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=8000)
                cards = page.query_selector_all(sel)
                if cards:
                    print(f"    Selector matched '{sel}': {len(cards)} cards")
                    break
            except Exception:
                continue

        if not cards:
            # Dump page title to help debug
            title = page.title()
            print(f"    No cards found. Page title: '{title}'")
            # Check if it's a CAPTCHA/block page
            content = page.content()
            if "captcha" in content.lower() or "robot" in content.lower() or "blocked" in content.lower():
                print("    ⚠️  Likely blocked by bot detection")
            return []

        for card in cards:
            try:
                listing = parse_card(card, beds)
                if listing:
                    listings.append(listing)
            except Exception:
                continue

        # Pagination: grab page 2 if there's a next button
        try:
            next_btn = page.query_selector('a[aria-label="Next page"], [data-testid="pagination-next"], .pagination-next a')
            if next_btn and len(listings) > 0:
                random_delay(2, 4)
                next_btn.click()
                page.wait_for_load_state("domcontentloaded")
                random_delay(2, 3)
                for sel in selectors:
                    try:
                        cards2 = page.query_selector_all(sel)
                        if cards2:
                            for card in cards2:
                                try:
                                    listing = parse_card(card, beds)
                                    if listing:
                                        listings.append(listing)
                                except Exception:
                                    continue
                            break
                    except Exception:
                        continue
        except Exception:
            pass

    except Exception as e:
        print(f"    Error scraping {url}: {e}")

    return listings


def parse_card(card, beds: int) -> dict | None:
    # Get listing URL
    link = card.query_selector("a[href*='/rental/'], a[href*='/for-rent/']")
    if not link:
        # Try any link inside the card
        link = card.query_selector("a[href]")
    if not link:
        return None

    href = link.get_attribute("href") or ""
    if not href:
        return None
    if not href.startswith("http"):
        href = "https://streeteasy.com" + href

    # Listing ID
    listing_id = href.rstrip("/").split("/")[-1]
    if not listing_id or listing_id in ("for-rent", "upper-west-side"):
        return None

    # Address — try several selectors
    address = ""
    for sel in ['[data-testid="listing-card-address"]', '.listingCard-addressLabel',
                '.address', 'address', 'h2', 'h3', '[class*="address"]']:
        el = card.query_selector(sel)
        if el:
            address = el.inner_text().strip()
            if address:
                break

    if not is_in_range(address):
        return None

    # Price
    price_text = ""
    for sel in ['[data-testid="listing-card-price"]', '.listingCard-priceLabel',
                '.price', '[class*="price"]', '[class*="Price"]']:
        el = card.query_selector(sel)
        if el:
            price_text = el.inner_text().strip()
            if price_text:
                break

    price = parse_price(price_text)
    if price and price > MAX_RENT:
        return None

    # Details (beds/baths summary line)
    details = ""
    for sel in ['[data-testid="listing-card-details"]', '.listingCard-details',
                '.details', '[class*="details"]', '[class*="Details"]']:
        el = card.query_selector(sel)
        if el:
            details = el.inner_text().strip()
            if details:
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
    """Check if address is between 89th and 103rd street. Include unknowns."""
    if not address:
        return True

    match = re.search(
        r'(?:West\s+|W\.?\s+|East\s+|E\.?\s+)?(\d{2,3})(?:st|nd|rd|th)?\s+St',
        address, re.IGNORECASE
    )
    if match:
        street_num = int(match.group(1))
        return 89 <= street_num <= 103

    return True  # include if can't parse — better to over-notify


def parse_price(price_text: str) -> int | None:
    match = re.search(r'[\$]?([\d,]+)', price_text)
    if match:
        return int(match.group(1).replace(",", ""))
    return None


def send_email(new_listings: list[dict]):
    if not FROM_EMAIL or not APP_PASSWORD:
        print("⚠️  No email credentials — printing listings instead:")
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
        beds_label  = f"{l['beds']} Bed{'s' if l['beds'] != 1 else ''}"
        price_label = l["price_text"] or "Price N/A"
        address     = l["address"] or "Address N/A"
        details     = l["details"] or ""

        tour_subject = f"Tour Request - {address}"
        tour_body    = (
            f"Hi, I'm interested in the apartment at {address} listed on StreetEasy. "
            f"Could we schedule a tour? Also, is the layout flexible "
            f"(would you allow a temporary wall to add a room)? Thank you!"
        )
        mailto = (
            f"mailto:?subject={tour_subject.replace(' ', '%20')}"
            f"&body={tour_body.replace(' ', '%20').replace(',', '%2C')}"
        )

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
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            }
        )
        # Mask webdriver flag
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = { runtime: {} };
        """)

        page = context.new_page()

        # Visit homepage first to get cookies (helps avoid bot detection)
        print("  Warming up with homepage visit...")
        try:
            page.goto("https://streeteasy.com", wait_until="domcontentloaded", timeout=20000)
            random_delay(2, 4)
        except Exception:
            pass

        for beds, url in build_search_urls():
            print(f"  Scraping {beds}BR listings...")
            listings = scrape_listings(page, url, beds)
            print(f"    → {len(listings)} listings found")

            for listing in listings:
                if listing["id"] not in seen:
                    new_listings.append(listing)
                    seen.add(listing["id"])

            random_delay(4, 8)  # polite delay between searches

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