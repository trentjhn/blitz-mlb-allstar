"""Paths, seasons, and scraping limits used across the pipeline."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
QUARANTINE_DIR = ROOT / "data" / "quarantine"
LOCK_PATH = ROOT / "data" / ".scrape.lock"
# Written when the site refuses a request (403 or 429); every later scrape refuses to start
# until someone deletes it, because Baseball Reference blocks can last a day.
BLOCK_PATH = ROOT / "data" / ".scrape.blocked"

SEASONS = (2024, 2025, 2026)

BR_BASE = "https://www.baseball-reference.com"
SHOW_URL = "https://www.theshowratings.com/lists/top-100-players"

USER_AGENT = "blitz-mlb-allstar/1.0 (MLB All-Star take-home; cached scraper, one request every 4 s)"

# Baseball Reference jails a session for up to a day above 20 requests a minute, and its
# robots.txt asks for 3 s between requests. 4 s is 15 a minute: a 25% margin that retries share.
REQUEST_SPACING_S = 4.0
TIMEOUT_S = 30
# Retries cover server errors, timeouts, and connections that drop. A 429 or 403 stops the
# run instead, because retrying against a rate limit extends the jail.
RETRY_WAITS_S = (8.0, 16.0)
