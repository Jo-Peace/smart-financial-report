"""Taiwan ticker ids shared by every module that keys data by stock code."""

from __future__ import annotations

import re

# Suffix only, ".TWO" before ".TW": replace(".TW").replace(".TWO") turned
# "6488.TWO" (上櫃, used since FinMind TPEx rows reach the universe) into "6488O".
_TW_SUFFIX_RE = re.compile(r"\.TWO?$", re.IGNORECASE)


def bare_ticker(value) -> str:
    """'2330.TW' / '6488.TWO' / ' 2330.tw ' → '2330' / '6488'; '^TWII' unchanged; None → ''."""
    if value is None:
        return ""
    return _TW_SUFFIX_RE.sub("", str(value).strip()).strip().upper()
