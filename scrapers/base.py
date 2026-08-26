import requests
import logging
import re
import time
from datetime import date
from typing import List, Dict, Optional

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s'
)

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}

# Builders / entities we never want as a lead. The business only wants distressed
# INDIVIDUAL homeowners, not builders/developers/HOAs/investment funds. Shared
# across every county scraper so the exclusion list only needs updating in one
# place. Checked against whatever name text a scraper extracts (table cell or OCR).
ENTITY_EXCLUDE_KEYWORDS = [
    # Homebuilders
    'D R HORTON', 'DR HORTON', 'LENNAR', 'KB HOME', 'MERITAGE', 'PULTE', 'CENTEX',
    'TAYLOR MORRISON', 'STARLIGHT', 'CONTINENTAL HOMES', 'BEAZER', 'CHESMAR',
    'M/I HOMES', 'COVENTRY', 'COUTO HOMES', 'LGI HOMES',
    # Generic entity suffixes / structures
    'LLC', 'L.L.C', 'LLP', 'L.P', ' LP', 'INC', 'CORPORATION', 'CORP', 'COMPANY',
    'PARTNERSHIP', 'LTD', 'TRUST', 'TRUSTEE',
    # Funds / investors / HOAs / institutions
    'PURCHASING FUND', 'ASSOCIATION', 'HOMEOWNERS', 'HOME OWNERS', 'HOA',
    'PROPERTIES', 'INVESTMENTS', 'HOLDINGS', 'CAPITAL', 'FINANCIAL', 'FUND',
    'PARTNERS', 'VENTURES', 'ENTERPRISES', 'REALTY', 'GROUP',
    'CITY OF', 'COUNTY OF', 'DEPARTMENT', 'REVENUE', 'AUTHORITY', 'DISTRICT',
]

# Word-boundary-wrapped version of each keyword, compiled once at import time
# rather than per call. Plain substring containment (`ex in name`) is
# unguarded and matches keywords embedded inside real surnames -- e.g. 'INC'
# inside "Vincent", 'HOA' inside "Hoag", 'LP' inside "Alpert" -- silently
# rejecting genuine homeowner leads as if they were entities. \b anchors each
# keyword to whole-token boundaries (a keyword still matches across internal
# punctuation like 'L.L.C' or multi-word phrases like 'CITY OF', since \b is
# only asserted at the two ends of the escaped keyword, not between its own
# characters) so it only fires when the keyword appears as its own token,
# not as a fragment of a longer word.
_ENTITY_EXCLUDE_PATTERNS = [
    re.compile(r'\b' + re.escape(kw) + r'\b') for kw in ENTITY_EXCLUDE_KEYWORDS
]


def is_residential_lead(name: str) -> bool:
    """True if an extracted owner name looks like an individual, not an entity.
    Unknown/blank name -> keep it (an address-only lead is still useful)."""
    if not name:
        return True
    g = name.upper()
    return not any(p.search(g) for p in _ENTITY_EXCLUDE_PATTERNS)


def launch_chromium(pw, **kwargs):
    """Launch headless Chromium, self-healing if the browser binary is missing.

    On GitHub Actions a stale Playwright cache can leave the Chromium build absent
    ("Executable doesn't exist"), which would silently lose an entire county. If
    that happens we install the browser at runtime and retry once."""
    import subprocess
    import sys
    args = kwargs.pop('args', ['--disable-blink-features=AutomationControlled'])
    try:
        return pw.chromium.launch(headless=True, args=args, **kwargs)
    except Exception as exc:
        msg = str(exc)
        if "Executable doesn't exist" in msg or 'playwright install' in msg:
            logging.getLogger('base').warning("Chromium missing — installing at runtime…")
            subprocess.run([sys.executable, '-m', 'playwright', 'install', 'chromium'],
                           check=False)
            return pw.chromium.launch(headless=True, args=args, **kwargs)
        raise


class BaseScraper:
    """Shared utilities for all county scrapers."""

    def __init__(self, county_name: str):
        self.county = county_name
        self.logger = logging.getLogger(county_name)
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def scrape(self, target_date: date) -> List[Dict]:
        raise NotImplementedError("Each county scraper must implement scrape()")

    # ── Name helpers ─────────────────────────────────────────────────────────

    def parse_name(self, full_name: str):
        """
        Split 'JOHN A DOE' → ('John', 'A Doe').
        Strips 'ET AL', 'ET UX', '& JANE', etc.
        Returns (first_name, last_name).
        """
        full_name = full_name.strip().upper()
        full_name = re.sub(
            r'\s+(ET\s+AL|ET\s+UX|AND\s+|&\s+).*$', '', full_name, flags=re.I
        ).strip()
        full_name = full_name.strip(',.;')
        parts = full_name.split()
        if len(parts) >= 2:
            return parts[0].title(), ' '.join(parts[1:]).title()
        elif len(parts) == 1:
            return '', parts[0].title()
        return '', full_name.title()

    # ── Address helpers ───────────────────────────────────────────────────────

    def parse_address(self, raw: str):
        """
        Try to pull street / city / zip from a raw address string.
        Returns (address, city, zip_code).
        """
        raw = raw.strip()
        m = re.search(
            r'^([\d]+[^,]+),\s*([A-Za-z\s]+),\s*TX\s*(\d{5})',
            raw, re.I
        )
        if m:
            return m.group(1).strip(), m.group(2).strip().title(), m.group(3)
        return raw, '', ''

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    def get(self, url: str, **kwargs) -> Optional[requests.Response]:
        for attempt in range(3):
            try:
                r = self.session.get(url, timeout=30, **kwargs)
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                self.logger.warning(f"GET {url} attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        return None

    def post(self, url: str, **kwargs) -> Optional[requests.Response]:
        for attempt in range(3):
            try:
                r = self.session.post(url, timeout=30, **kwargs)
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                self.logger.warning(f"POST {url} attempt {attempt+1} failed: {e}")
                time.sleep(2 ** attempt)
        return None

    def build_record(self, **kwargs) -> Dict:
        """Normalise field names into the canonical output dict."""
        return {
            'first_name':  kwargs.get('first_name', ''),
            'last_name':   kwargs.get('last_name', ''),
            'address':     kwargs.get('address', ''),
            'city':        kwargs.get('city', ''),
            'state':       kwargs.get('state', 'TX'),
            'zip_code':    kwargs.get('zip_code', ''),
            'county':      self.county,
            'file_date':   kwargs.get('file_date', ''),
            'sale_date':   kwargs.get('sale_date', ''),
            'doc_id':      kwargs.get('doc_id', ''),
        }
