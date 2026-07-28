# Foreclosure Scraper — Collin, Ellis, Travis

Pulls Notice-of-Trustee's-Sale (pre-foreclosure) filings from three Texas
county record portals daily and appends them to a Google Sheet. Sister system
to [`Lance913/Scraper_Python`](https://github.com/Lance913/Scraper_Python)
(Harris/Bexar/Dallas/Tarrant/Denton/Johnson) — same architecture, different
counties, separate Google Sheet tabs so the two lead sets never mix. See
`SYSTEM_GUIDE.md` in this repo for the full design rationale and hard-won bugs
this codebase defends against.

| County | Portal |
|--------|--------|
| Collin | apps.collincountytx.gov/ForeclosureNotices |
| Ellis  | co.ellis.tx.us (Archive/Laserfiche) |
| Travis | tccsearch.org |

**Fields captured:** First Name · Last Name · Address · City · State · Zip ·
County · Foreclosure File Date · Sale Date · Doc ID · Date Pulled

---

## One-Time Setup

### Step 1 — Enable Google Sheets API
1. [console.cloud.google.com](https://console.cloud.google.com) → new or existing project
2. Enable **Google Sheets API** and **Google Drive API**

### Step 2 — Create a Service Account
1. IAM & Admin → Service Accounts → Create Service Account (any name, no roles needed)
2. Keys tab → Add Key → JSON → download it

> **Reusing the Scraper_Python service account is fine and saves a step** — if
> you already have a `GOOGLE_CREDENTIALS` JSON key from that project, you can
> reuse the same one here instead of creating a new one. Just make sure its
> `client_email` is shared on the sheet (Step 3) and its JSON is added as this
> repo's secret too (Step 4) — GitHub secrets don't carry over between repos
> automatically.

### Step 3 — Share the target Google Sheet
Open the JSON key → copy `client_email` → open the target Google Sheet
(the one this system writes to) → **Share** → paste that email → **Editor**.

### Step 4 — Add the GitHub secret
Repo → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `GOOGLE_CREDENTIALS`
- Value: the entire JSON key file contents

### Step 5 — Test it
**Actions tab → "Daily Foreclosure Scraper (Collin/Ellis/Travis)" → Run workflow**
- `dry_run = true` first, to verify scraping without writing
- Check the run logs for output

---

## Schedule
Runs daily at ~7:15 AM CST (`15 13 * * *` UTC — offset from the exact hour
since GitHub's `schedule` trigger is best-effort and commonly fires 1-3h late
at :00). Edit `.github/workflows/daily_scrape.yml` to change it.

## Manual / Backfill Run
Actions UI → Run workflow, with `date` / `counties` / `dry_run` inputs. Or
locally (editing only — see `SYSTEM_GUIDE.md` on why real runs must happen on
GitHub Actions, not locally):
```bash
pip install -r requirements.txt
export GOOGLE_CREDENTIALS='{ ... paste JSON ... }'
python main.py --dry-run
python main.py --counties collin
```

## Notes on Data
- **Names** come from the deed-of-trust grantor / owner field. Entities
  (builders, HOAs, funds) are filtered out — see `scrapers/base.py`'s
  `is_residential_lead()` / `ENTITY_EXCLUDE_KEYWORDS`.
- **Duplicates** are filtered by doc ID (preferred) or name+county+file
  date+address before writing — see `sheets_writer.py`.
- If a run finds zero records for a county, that's often normal — not every
  day has new filings.

## File Structure
```
Foreclosure-Scraper-Collin-Ellis-Travis/
├── .github/workflows/
│   ├── daily_scrape.yml   # production cron — matrix (1 job/county) + collate
│   └── reset_sheet.yml    # one-click wipe (type RESET), for a clean re-populate
├── scrapers/
│   ├── __init__.py        # scraper class registry
│   ├── base.py             # shared utilities: name/address parsing, HTTP
│   │                        # retry, entity exclusion, build_record()
│   └── <county>.py         # one module per county/portal (+ <county>_extract.py
│                            # if OCR is needed)
├── main.py                 # CLI orchestrator
├── sheets_writer.py         # Google Sheets I/O (dedup, sort, tracker, retry)
├── requirements.txt
├── SYSTEM_GUIDE.md          # full design guide — read this before changing anything
└── README.md
```
