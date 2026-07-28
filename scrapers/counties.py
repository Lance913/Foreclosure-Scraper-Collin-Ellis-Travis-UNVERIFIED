"""
Thin per-county wrapper classes for counties that share a portal-type
scraper (system guide §3/§5). Currently one county: Travis, on the
publicsearch.us/GovOS platform (scrapers/publicsearch.py) -- the same
platform already proven for Bexar/Dallas/Tarrant/Denton/Johnson in the
sister repo (Lance913/Scraper_Python), confirmed live for Travis via
probe_travis.py (see that file's docstring history for the full
investigation log).

If Collin or Ellis ever migrate to this platform, or a future county lands
on it, add another thin wrapper here (or override `_reach_results_page()`
too, if that county's tenant also needs form-driven search instead of the
direct results-URL shape).
"""
import os
from datetime import date, timedelta

from .publicsearch import PublicSearchScraper, DEFAULT_WINDOW_DAYS

# How far forward to search for upcoming sale dates if the Sale Date control
# turns out to be an explicit date-range picker rather than a preset listbox
# (unconfirmed -- see TravisCountyScraper._reach_results_page). TX trustee
# sales are posted >=21 days ahead and happen the first Tuesday of the month,
# so a ~180 day forward window comfortably covers "upcoming" without being
# unbounded.
SALE_DATE_HORIZON_DAYS = int(os.environ.get('TRAVIS_SALE_DATE_HORIZON_DAYS', '180'))


class TravisCountyScraper(PublicSearchScraper):
    """Travis County -- publicsearch.us/GovOS, but NOT a drop-in identical
    tenant to the other 5 counties (system guide §3/§7: same platform does
    not guarantee identical behavior -- verify per county). Two confirmed
    differences from the base class, both handled by overriding
    `_reach_results_page()`:

    1. This tenant does NOT accept the shared direct results-URL query shape.
       Probed directly: `/results?department=FC&recordedDateRange=...` and
       the same with LR/NOS/FORECLOSURE all failed ("Error with search
       query" or a blank "Error" page/title). The Quick Search FORM works
       fine though (Department dropdown genuinely offers: Land Records,
       Assumed Names, Marriage, Foreclosures) -- so we drive that form
       directly instead of URL-guessing.

    2. Once "Foreclosures" is selected, the Date Range field's own label
       changes from "Recorded Date" to **"Sale Date"** -- confirmed via a
       real probe run (body-text dump showed "Date Range | Sale Date" after
       selecting the department). This is actually better for this
       business's purpose (upcoming auction date) than the recorded-date
       window the other 5 counties filter on.

    Interaction quirks discovered along the way (see probe_travis.py):
      - An onboarding "Not sure where to start? Take the Tour" popup
        overlays the form on first load and must be dismissed first (it
        blocks clicks on the fields underneath).
      - The Department combobox's real interactive element is an
        `<input role="combobox">` with a 0x0 bounding box -- not clickable
        by Playwright directly (fails the visibility check). Click the
        VISIBLE TEXT of the current value instead, then drive selection via
        keyboard (ArrowDown + Enter) rather than clicking option elements,
        which sidesteps a bunch of overlap/visibility issues with the
        rendered listbox. Confirmed order: Land Records, Assumed Names,
        Marriage, Foreclosures (index 3 -> 3x ArrowDown from Land Records).

    NOT YET CONFIRMED as of this writing (billing outage interrupted the
    probe run that was mid-verifying this -- see PR description): the exact
    preset options under "Sale Date" (may differ from the backward-looking
    "Last 24 Hours...Last 1 Year" list shown under "Recorded Date", since a
    sale date is inherently forward-looking). The ArrowDown-then-Enter
    approach below is written defensively (many presses, clamps at the last/
    broadest option rather than assuming a specific list length or wrapping
    behavior) and mirrors the exact pattern that worked for the Department
    field, but treat it as unconfirmed until a probe run shows real results.
    """

    def __init__(self):
        super().__init__('travis', 'Travis')

    def _window_days(self) -> int:
        # Unused for Travis -- the date range is chosen via the Sale Date UI
        # control in _reach_results_page(), not a recordedDateRange URL
        # param. Kept only for base-class interface compatibility.
        return DEFAULT_WINDOW_DAYS

    def _reach_results_page(self, page, target_date: date) -> None:
        self.logger.info(f"{self.county}: loading Quick Search...")
        page.goto(self.base_url)
        page.wait_for_load_state('networkidle')
        page.wait_for_timeout(1200)
        self._dismiss_popups(page)

        # 1. Department -> Foreclosures (confirmed working).
        page.get_by_text('Land Records', exact=True).first.click(timeout=8000)
        page.wait_for_timeout(600)
        dept_opts = page.evaluate(
            "() => Array.from(document.querySelectorAll('[role=\"option\"]'))"
            ".map(el => (el.textContent||'').trim()).filter(Boolean)")
        self.logger.info(f"{self.county}: department options: {dept_opts}")
        for _ in range(3):
            page.keyboard.press('ArrowDown')
            page.wait_for_timeout(150)
        page.keyboard.press('Enter')
        page.wait_for_timeout(1000)

        # 2. Date range -> Sale Date, broadest available window.
        #    UNCONFIRMED: see class docstring. Try the new "Sale Date" label
        #    first (expected once Foreclosures is selected), fall back to
        #    "Recorded Date" in case the label didn't change as expected.
        try:
            date_trigger = page.get_by_text('Sale Date', exact=True).first
            date_trigger.click(timeout=4000)
            self.logger.info(f"{self.county}: opened date range via 'Sale Date' label")
        except Exception:
            date_trigger = page.get_by_text('Recorded Date', exact=True).first
            date_trigger.click(timeout=4000)
            self.logger.warning(
                f"{self.county}: 'Sale Date' label not found, fell back to "
                f"'Recorded Date' -- department selection may not have taken.")
        page.wait_for_timeout(600)
        date_opts = page.evaluate(
            "() => Array.from(document.querySelectorAll('[role=\"option\"]'))"
            ".map(el => (el.textContent||'').trim()).filter(Boolean)")
        self.logger.info(f"{self.county}: date range options: {date_opts}")

        if date_opts:
            # Listbox behaves like the Department field -- press ArrowDown
            # generously to land on the LAST (broadest) option regardless of
            # exact list length -- most listbox widgets clamp at the last
            # item rather than wrap around.
            for _ in range(10):
                page.keyboard.press('ArrowDown')
                page.wait_for_timeout(100)
            page.keyboard.press('Enter')
            page.wait_for_timeout(1000)
        else:
            # No listbox options appeared -- this control likely isn't a
            # preset dropdown (e.g. a raw calendar date-picker instead).
            # Fall back to filling explicit date inputs directly with a
            # forward-looking window, since "Sale Date" for an upcoming
            # auction is inherently a future date, not a past one.
            self.logger.warning(
                f"{self.county}: no listbox options after opening date range -- "
                f"trying explicit date inputs instead (unconfirmed fallback).")
            try:
                page.keyboard.press('Escape')
            except Exception:
                pass
            page.wait_for_timeout(300)
            end_date = target_date + timedelta(days=SALE_DATE_HORIZON_DAYS)
            date_inputs = page.locator('input[type="date"], input[placeholder*="date" i]')
            n = date_inputs.count()
            self.logger.info(f"{self.county}: found {n} date-like input(s) for fallback fill")
            if n >= 2:
                date_inputs.nth(0).fill(target_date.strftime('%m/%d/%Y'))
                date_inputs.nth(1).fill(end_date.strftime('%m/%d/%Y'))
            elif n == 1:
                date_inputs.nth(0).fill(target_date.strftime('%m/%d/%Y'))
            else:
                self.logger.error(
                    f"{self.county}: no date range control matched either the "
                    f"listbox or explicit-input pattern -- Search will run "
                    f"with whatever default date range is pre-selected.")
            page.wait_for_timeout(500)

        # 3. Search.
        search_btn = page.locator('button:has-text("Search")').first
        search_btn.click(timeout=8000)
        try:
            page.wait_for_load_state('networkidle', timeout=15_000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        self.logger.info(f"{self.county}: after search -> url={page.url} title={page.title()!r}")

    @staticmethod
    def _dismiss_popups(page) -> None:
        """Close the onboarding tour popup that overlays the form on first
        load (confirmed present via screenshot; blocks clicks on the fields
        underneath if not dismissed first)."""
        try:
            page.keyboard.press('Escape')
            page.wait_for_timeout(300)
        except Exception:
            pass
        for sel in ('[aria-label="Close"]', 'button:has-text("×")', 'button:has-text("✕")',
                    'button[class*="close" i]', '[class*="modal" i] button',
                    '[class*="popup" i] button', '[class*="tour" i] button'):
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=1500)
                    page.wait_for_timeout(400)
            except Exception:
                pass
        try:
            page.mouse.click(5, 5)
        except Exception:
            pass
