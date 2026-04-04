"""
Health scoring engine for Etsy listings (0-10 scale).

Score starts at 10.0 and is reduced for issues found.
Etsy-specific calibration: 13 tags allowed, 10 images allowed,
views/favourites/conversion are the primary performance signals.
"""


# Each check returns True if the issue is present
ETSY_CHECKS = {
    'NO_TAGS':         lambda l: len(l.get('tags') or []) == 0,
    'FEW_TAGS':        lambda l: 0 < len(l.get('tags') or []) < 10,
    'SHORT_TITLE':     lambda l: len(l.get('title') or '') < 40,
    'LONG_TITLE':      lambda l: len(l.get('title') or '') > 140,
    'NO_DESCRIPTION':  lambda l: not l.get('description'),
    'SHORT_DESC':      lambda l: l.get('description') and len(l['description']) < 200,
    'NO_IMAGES':       lambda l: l.get('num_images', 0) == 0,
    'FEW_IMAGES':      lambda l: 0 < l.get('num_images', 0) < 5,
    'ZERO_VIEWS':      lambda l: l.get('views_30d') is not None and l['views_30d'] == 0,
    'LOW_VIEWS':       lambda l: l.get('views_30d') is not None and 0 < l['views_30d'] < 20,
    'LOW_CONVERSION':  lambda l: (l.get('conversion_rate') or 0) > 0 and l['conversion_rate'] < 0.02,
    'NO_SALES':        lambda l: l.get('sales_30d') is not None and l['sales_30d'] == 0,
    'HIGH_PRICE':      lambda l: l.get('price') is not None and l['price'] > 50,
}

# Deductions per issue code
DEDUCTIONS = {
    'NO_TAGS':         2.0,
    'FEW_TAGS':        1.0,
    'SHORT_TITLE':     0.5,
    'LONG_TITLE':      0.5,
    'NO_DESCRIPTION':  1.5,
    'SHORT_DESC':      0.5,
    'NO_IMAGES':       2.0,
    'FEW_IMAGES':      0.5,
    'ZERO_VIEWS':      2.0,
    'LOW_VIEWS':       1.0,
    'LOW_CONVERSION':  1.5,
    'NO_SALES':        1.0,
    'HIGH_PRICE':      0.25,   # flag only, minor deduction
}

# Human-readable recommendations per issue
RECOMMENDATIONS = {
    'NO_TAGS':         'Add at least 10 of the 13 allowed tags with relevant search terms',
    'FEW_TAGS':        'Add more tags — Etsy allows 13, aim for all 13',
    'SHORT_TITLE':     'Expand title with keywords buyers search for (aim for 60-100 chars)',
    'LONG_TITLE':      'Shorten title — keep under 140 characters for readability',
    'NO_DESCRIPTION':  'Add a detailed description with keywords and product details',
    'SHORT_DESC':      'Expand description — aim for 300+ words with use cases and materials',
    'NO_IMAGES':       'Add product images — Etsy allows 10, use at least 5',
    'FEW_IMAGES':      'Add more images — show different angles, scale, and context shots',
    'ZERO_VIEWS':      'Review SEO: tags, title, and category may not match buyer searches',
    'LOW_VIEWS':       'Improve visibility: refresh tags, consider Etsy Ads for testing',
    'LOW_CONVERSION':  'Review pricing, images, and description quality vs competitors',
    'NO_SALES':        'Consider promotional pricing or refreshing listing to boost visibility',
    'HIGH_PRICE':      'Price above £50 — verify competitive positioning',
}


def calculate_health_score(listing: dict) -> tuple[float, list[str], list[str]]:
    """
    Calculate a 0-10 health score for a listing.

    Returns (score, issues_list, recommendations_list).
    """
    score = 10.0
    issues = []
    recs = []

    for code, check_fn in ETSY_CHECKS.items():
        try:
            if check_fn(listing):
                issues.append(code)
                score -= DEDUCTIONS.get(code, 0)
                if code in RECOMMENDATIONS:
                    recs.append(RECOMMENDATIONS[code])
        except (TypeError, KeyError):
            pass

    score = max(0.0, min(10.0, round(score, 1)))
    return score, issues, recs
