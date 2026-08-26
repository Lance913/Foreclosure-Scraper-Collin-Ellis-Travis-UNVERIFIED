"""
Google Sheets Writer — Foreclosure Leads (Collin, Ellis, Travis)

Writes into the SAME spreadsheet used by the Probate-Scraper-TX repo, but on
its own tabs, so the two lead types never mix. Leads get their own tab PER
COUNTY (e.g. 'Collin-Foreclosure'), auto-created on first write — easier to
scan/filter per county than one giant combined tab. There is one shared
tracker tab across all counties:
  - '{County}-Foreclosure'      (one per county, this repo's records)
  - 'Foreclosure Daily Counts'  (this repo's per-day/per-county tracker)

Dedup key priority:
  1. If doc_id present → county + doc_id (unique per filing regardless of name)
  2. Else if address present → first + last + county + file_date + address
  3. Else → first + last + county + file_date

Column order: First Name, Last Name, Address, City, State, Zip, County,
              Foreclosure File Date, Sale Date, Doc ID, Date Pulled

All Sheets API calls are wrapped with retry/backoff — a single transient
503/429 must not discard a whole day's already-scraped work (system guide §9,
bug 5). Retrying the whole write is safe here because the write path is
dedup-check-then-append, never blind-append, so a retried call can't create
duplicates even if the first attempt partially succeeded.
"""

import os
import json
import logging
import time
from typing import Callable, Dict, List, TypeVar

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger('sheets_writer')

SPREADSHEET_ID = '1i3u7U-GGvJ95tH4zk0B1TjaGEplNpUd7fyPj-EaoBWE'

SCOPES = [
    'https://spreadsheets.google.com/feeds',
    'https://www.googleapis.com/auth/drive',
]

COLUMN_HEADERS = [
    'First Name',
    'Last Name',
    'Address',
    'City',
    'State',
    'Zip Code',
    'County',
    'Foreclosure File Date',
    'Sale Date',
    'Doc ID',
    'Date Pulled',
]

# Column index (1-based) of "Foreclosure File Date" — the sheet is sorted on this.
FILE_DATE_COL = COLUMN_HEADERS.index('Foreclosure File Date') + 1
LAST_COL_LETTER = chr(ord('A') + len(COLUMN_HEADERS) - 1)

LEADS_SUFFIX = 'Foreclosure'  # per-county tab name: '{County}-Foreclosure'


def _leads_tab_name(county: str) -> str:
    return f"{str(county).strip().title()}-{LEADS_SUFFIX}"


# ── Daily tracker tab ───────────────────────────────────────────────────────
TRACKER_TAB = 'Foreclosure Daily Counts'
# Add each county's display name here as its scraper lands.
TRACKER_COUNTIES = ['Collin', 'Ellis', 'Travis']
TRACKER_HEADERS = ['Date'] + TRACKER_COUNTIES + ['Total']

T = TypeVar('T')

# Transient HTTP statuses worth retrying. None (e.g. a raw ConnectionError with
# no response object) is also treated as transient — safer to retry than not.
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 5


def _with_retry(fn: Callable[[], T], label: str) -> T:
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fn()
        except (gspread.exceptions.APIError, ConnectionError, TimeoutError) as exc:
            status = None
            resp = getattr(exc, 'response', None)
            if resp is not None:
                status = getattr(resp, 'status_code', None)
            transient = status is None or status in _TRANSIENT_STATUSES
            if not transient or attempt == _MAX_ATTEMPTS:
                raise
            delay = min(2 ** attempt, 30)
            logger.warning(
                f"{label}: transient error (attempt {attempt}/{_MAX_ATTEMPTS}, "
                f"status={status}): {exc} — retrying in {delay}s")
            time.sleep(delay)
    raise RuntimeError(f"{label}: exhausted retries")  # unreachable


def _get_client() -> gspread.Client:
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    if not creds_json:
        raise EnvironmentError("GOOGLE_CREDENTIALS environment variable is not set.")
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_worksheet(ss: gspread.Spreadsheet, title: str,
                              headers: List[str]) -> gspread.Worksheet:
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=1000, cols=max(len(headers), 10))
        ws.update('A1', [headers], value_input_option='USER_ENTERED')
        logger.info(f"Created '{title}' tab.")
        return ws
    first_row = ws.row_values(1)
    if first_row != headers:
        logger.info(f"'{title}' headers missing or outdated — resetting row 1.")
        ws.update('A1', [headers], value_input_option='USER_ENTERED')
    return ws


def _record_key(rec: Dict) -> str:
    county  = str(rec.get('county',   '')).strip().lower()
    doc_id  = str(rec.get('doc_id',   '')).strip()
    first   = str(rec.get('first_name','')).strip().lower()
    last    = str(rec.get('last_name', '')).strip().lower()
    file_dt = str(rec.get('file_date', '')).strip()
    address = str(rec.get('address',  '')).strip().lower()

    if doc_id:
        return f"docid|{county}|{doc_id}"
    if address and address not in ('n/a', ''):
        return f"{first}|{last}|{county}|{file_dt}|{address}"
    return f"{first}|{last}|{county}|{file_dt}"


def _existing_keys(worksheet: gspread.Worksheet) -> set:
    records = worksheet.get_all_records()
    return {
        _record_key({
            'doc_id':     r.get('Doc ID',                ''),
            'first_name': r.get('First Name',            ''),
            'last_name':  r.get('Last Name',             ''),
            'county':     r.get('County',                ''),
            'file_date':  r.get('Foreclosure File Date', ''),
            'address':    r.get('Address',               ''),
        })
        for r in records
    }


def _to_row(rec: Dict, pull_date: str = '') -> List[str]:
    return [
        rec.get('first_name', ''),
        rec.get('last_name',  ''),
        rec.get('address',    ''),
        rec.get('city',       ''),
        rec.get('state',      'TX'),
        rec.get('zip_code',   ''),
        rec.get('county',     ''),
        rec.get('file_date',  ''),
        rec.get('sale_date',  ''),
        rec.get('doc_id',     ''),
        pull_date,
    ]


def _sort_by_file_date(worksheet: gspread.Worksheet):
    """Sort the whole tab (excluding the header row) by Foreclosure File Date.
    Non-fatal if it fails — the data is already written."""
    try:
        # Must not use col_values(1) — column A (First Name) is blank on many
        # rows, so its trailing blanks get trimmed and the row count comes back
        # short, leaving the bottom of the sheet unsorted.
        n_rows = len(worksheet.get_all_values())  # any row with content, incl. header
        if n_rows > 2:
            worksheet.sort((FILE_DATE_COL, 'asc'),
                           range=f'A2:{LAST_COL_LETTER}{n_rows}')
            logger.info(f"Sorted sheet by file date (rows 2–{n_rows}).")
    except Exception as e:
        logger.warning(f"Sort by file date failed (data still written): {e}")


def write_records(records: List[Dict], pull_date: str = '') -> List[Dict]:
    """Write new (non-duplicate) records, one tab per county. Returns the
    records actually written, so callers can report a true per-county 'new
    today' count."""
    if not records:
        logger.info("No records to write.")
        return []

    def _do():
        ss = _get_client().open_by_key(SPREADSHEET_ID)

        by_county: Dict[str, List[Dict]] = {}
        for rec in records:
            by_county.setdefault(str(rec.get('county', '')).strip(), []).append(rec)

        all_new_records: List[Dict] = []
        for county, county_records in by_county.items():
            tab_name  = _leads_tab_name(county)
            worksheet = _get_or_create_worksheet(ss, tab_name, COLUMN_HEADERS)
            existing  = _existing_keys(worksheet)

            new_rows, new_records, seen_today = [], [], set()
            for rec in county_records:
                key = _record_key(rec)
                if key in existing or key in seen_today:
                    continue
                seen_today.add(key)
                new_rows.append(_to_row(rec, pull_date))
                new_records.append(rec)

            if new_rows:
                worksheet.append_rows(new_rows, value_input_option='USER_ENTERED')
                logger.info(f"{tab_name}: wrote {len(new_rows)} new rows.")
                _sort_by_file_date(worksheet)
            else:
                logger.info(f"{tab_name}: all records were duplicates — nothing written.")
            all_new_records.extend(new_records)

        return all_new_records

    return _with_retry(_do, 'write_records')


def reset_all() -> None:
    """Wipe every per-county leads tab and the tracker tab, leaving only
    fresh headers. Discovers leads tabs dynamically (anything ending in
    '-{LEADS_SUFFIX}') so it stays correct as counties are added, without
    needing a hardcoded county list."""
    def _do():
        ss = _get_client().open_by_key(SPREADSHEET_ID)

        for ws in ss.worksheets():
            if ws.title.endswith(f'-{LEADS_SUFFIX}'):
                ws.clear()
                ws.update('A1', [COLUMN_HEADERS], value_input_option='USER_ENTERED')
                logger.info(f"Reset '{ws.title}' tab.")

        tracker = _get_or_create_worksheet(ss, TRACKER_TAB, TRACKER_HEADERS)
        tracker.clear()
        tracker.update('A1', [TRACKER_HEADERS], value_input_option='USER_ENTERED')
        logger.info(f"Reset '{TRACKER_TAB}' tab.")

    _with_retry(_do, 'reset_all')


def update_daily_tracker(pull_date: str, new_by_county: Dict[str, int]):
    """One row per day on the tracker tab: how many NEW records were added per
    county that day (duplicates excluded — this is genuinely new leads).

    If the day already has a row (e.g. the job ran twice), counts are ADDED to
    it so the row stays the true total of what came in that day."""
    def _do():
        ss = _get_client().open_by_key(SPREADSHEET_ID)
        ws = _get_or_create_worksheet(ss, TRACKER_TAB, TRACKER_HEADERS)

        counts = [int(new_by_county.get(c, 0)) for c in TRACKER_COUNTIES]

        dates = ws.col_values(1)  # includes header
        if pull_date in dates:
            idx = dates.index(pull_date) + 1
            prev = ws.row_values(idx)
            merged = []
            for i, _c in enumerate(TRACKER_COUNTIES):
                cell = prev[i + 1] if len(prev) > i + 1 else ''
                before = int(cell) if str(cell).strip().lstrip('-').isdigit() else 0
                merged.append(before + counts[i])
            row = [pull_date] + merged + [sum(merged)]
            ws.update(f'A{idx}', [row], value_input_option='USER_ENTERED')
            logger.info(f"Tracker {pull_date} (accumulated): {dict(zip(TRACKER_COUNTIES, merged))}")
        else:
            row = [pull_date] + counts + [sum(counts)]
            ws.append_row(row, value_input_option='USER_ENTERED')
            logger.info(f"Tracker {pull_date} (new): {dict(zip(TRACKER_COUNTIES, counts))}")

    _with_retry(_do, 'update_daily_tracker')
