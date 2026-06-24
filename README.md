# 🏠 UWS Apartment Scraper

Scrapes StreetEasy daily for 1–3BR apartments between 89th–103rd St on the Upper West Side, under $7,000/mo. Emails new listings to you automatically.

---

## Setup (20 minutes, one time)

### 1. Fork / clone this repo

```bash
git clone https://github.com/YOUR_USERNAME/streeteasy-scraper.git
cd streeteasy-scraper
```

### 2. Create a Gmail App Password

You need an **App Password** (not your regular Gmail password) so the script can send email:

1. Go to your Google Account → **Security**
2. Make sure **2-Step Verification** is ON
3. Go to **App Passwords** (search for it in the search bar)
4. Create one: App = "Mail", Device = "Other" → name it "StreetEasy Scraper"
5. Copy the 16-character password Google gives you

### 3. Add GitHub Secrets

In your GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**:

| Secret name | Value |
|---|---|
| `GMAIL_USER` | your Gmail address (e.g. `lexiszaf@gmail.com`) |
| `GMAIL_APP_PASSWORD` | the 16-char app password from step 2 |

### 4. Enable GitHub Actions

Go to your repo → **Actions** tab → click **"I understand my workflows, go ahead and enable them"**

### 5. Test it manually

Go to **Actions** → **Daily UWS Apartment Scraper** → **Run workflow** → **Run workflow**

Watch it run. You should get an email within ~3 minutes if there are listings.

---

## How it works

- Runs every day at **8:00 AM Eastern**
- Scrapes StreetEasy for 1BR, 2BR, 3BR in UWS under $7k
- Filters to **89th–103rd Street**
- Tracks listings it's already seen in `data/seen_listings.json`
- Emails you **only new listings** — no spam, no repeats
- Each email has a **"View on StreetEasy"** link and a **"Request Tour"** mailto button

---

## Adjusting filters

Edit `scraper.py`:

```python
MAX_RENT  = 7000   # raise or lower budget
MIN_BEDS  = 1      # minimum bedrooms
MAX_BEDS  = 3      # maximum bedrooms
MIN_BATHS = 1      # minimum bathrooms
```

To change the street range, edit `is_in_range()`:
```python
return 89 <= street_num <= 103  # change these numbers
```

To change the run time, edit `.github/workflows/scrape.yml`:
```yaml
- cron: "0 12 * * *"  # 12:00 UTC = 8:00 AM ET
```
Use [crontab.guru](https://crontab.guru) to figure out cron syntax.

---

## If StreetEasy blocks it

The scraper uses stealth settings but StreetEasy may occasionally block it. If you're getting empty emails or errors:

1. Check the **Actions** logs for error messages
2. Try running locally first: `pip install playwright && playwright install chromium && python scraper.py`
3. If consistently blocked, the next step is adding a proxy (ScraperAPI has a free tier of 1000 calls/mo)

---

## Running locally

```bash
pip install -r requirements.txt
playwright install chromium

# Set env vars
export GMAIL_USER="lexiszaf@gmail.com"
export GMAIL_APP_PASSWORD="your-app-password"

python scraper.py
```
