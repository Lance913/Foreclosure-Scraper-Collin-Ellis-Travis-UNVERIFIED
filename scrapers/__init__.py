"""
Scraper class registry. Each county gets its own module here
(one per DISTINCT portal platform — if two counties turn out to share a
platform, the second can be a thin wrapper class in the first's module or
a shared counties.py, per the system guide §3).

Add each new county's import + name below as its scraper is completed:

    from .collin import CollinCountyScraper
    from .ellis import EllisCountyScraper
    from .counties import TravisCountyScraper

    __all__ = ['CollinCountyScraper', 'EllisCountyScraper', 'TravisCountyScraper']

Travis: publicsearch.us/GovOS (scrapers/publicsearch.py, thin wrapper in
scrapers/counties.py) -- same platform as Bexar/Dallas/Tarrant/Denton/Johnson
in the sister repo. NOT YET wired into main.py's ALL_COUNTIES/SCRAPER_MAP --
see probe_travis.py and the PR description for what's confirmed vs. still
being verified before it's flipped on for real dry-run testing.
"""
from .counties import TravisCountyScraper

__all__ = ['TravisCountyScraper']
