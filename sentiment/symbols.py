from .exposure import MAJORS, split_pair

METAL_PREFIXES = ('XAU', 'XAG', 'XPT', 'XPD')
CRYPTO_PREFIXES = (
    'BTC', 'LTC', 'ETH', 'DSH', 'XRP', 'BCH', 'ZEC', 'NEO', 'QTM', 'XLM', 'TRX',
    'ADA', 'DOT', 'UNI', 'YFI', 'ICP', 'SOL', 'FIL', 'AXS', 'GRT', 'CHZ',
)

GROUPS = ('majors', 'crosses', 'exotics', 'metals', 'crypto')


def classify(symbol: str) -> str:
    """majors = pairs against USD, crosses = other major-currency pairs, then the rest."""
    if symbol[:3] in METAL_PREFIXES:
        return 'metals'
    if symbol[:3] in CRYPTO_PREFIXES:
        return 'crypto'
    if split_pair(symbol):
        return 'majors' if 'USD' in (symbol[:3], symbol[3:]) else 'crosses'
    return 'exotics'
