"""
Scraper class registry. Each county gets its own module here
(one per DISTINCT portal platform — if two counties turn out to share a
platform, the second can be a thin wrapper class in the first's module or
a shared counties.py, per the system guide §3).

Add each new county's import + name below as its scraper is completed:

    from .collin import CollinCountyScraper
    from .ellis import EllisCountyScraper
    from .travis import TravisCountyScraper

    __all__ = ['CollinCountyScraper', 'EllisCountyScraper', 'TravisCountyScraper']
"""

__all__ = []
