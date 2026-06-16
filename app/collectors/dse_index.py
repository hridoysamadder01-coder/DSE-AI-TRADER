"""DSE index snapshot (DSEX, DS30, DSES).

The homepage marquee loads index values via AJAX, so the static HTML has none.
The reliable server-rendered source is `recent_market_information.php`, whose
first table holds one row per trading day with the closing index values plus
market-wide totals. We read the latest row (value) and the prior row to derive
change / change %.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup
from loguru import logger

from ..config import get_settings
from ..schemas import IndexSnapshotIn, TickIn
from .base import BaseCollector
from .dse import _to_float
from .http import fetch_html


# Index columns we care about, matched by collapsed header text (lowercased,
# whitespace removed). DSE header cells read e.g. "DSEX <br>Index" -> "dsexindex".
_INDEX_HEADER_KEYS = {
    "DSEX": "dsexindex",
    "DSES": "dsesindex",
    "DS30": "ds30index",
}


class DSEIndexSnapshotCollector(BaseCollector):
    name = "dse_index"
    URL_PATH = "/recent_market_information.php"

    def fetch(self) -> tuple[list[TickIn], list[IndexSnapshotIn]]:
        settings = get_settings()
        html = fetch_html(settings.dse_base_url + self.URL_PATH)
        return [], self._parse(html)

    def _parse(self, html: str) -> list[IndexSnapshotIn]:
        soup = BeautifulSoup(html, "lxml")
        captured_at = datetime.now(timezone.utc)

        # Find the table whose header row carries the DSEX index column.
        target_table = None
        header_cells: list[str] = []
        for table in soup.find_all("table"):
            collapsed = [
                "".join(th.get_text(strip=True).lower().split())
                for th in table.find_all("th")
            ]
            if any("dsexindex" in c for c in collapsed):
                target_table = table
                header_cells = collapsed
                break

        if target_table is None:
            logger.info("DSE index: index table not found (layout may have changed)")
            return []

        def col_for(key: str) -> int | None:
            for i, c in enumerate(header_cells):
                if key in c:
                    return i
            return None

        col_idx = {name: col_for(key) for name, key in _INDEX_HEADER_KEYS.items()}
        col_idx = {n: i for n, i in col_idx.items() if i is not None}
        if not col_idx:
            logger.info("DSE index: no index columns resolved")
            return []

        # Collect data rows (one per trading day, newest first).
        max_col = max(col_idx.values())
        data_rows: list[list[str]] = []
        for tr in target_table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) > max_col:
                data_rows.append([td.get_text(strip=True) for td in tds])
        if not data_rows:
            logger.info("DSE index: no data rows in index table")
            return []

        latest = data_rows[0]
        prev = data_rows[1] if len(data_rows) > 1 else None

        snaps: list[IndexSnapshotIn] = []
        for name, i in col_idx.items():
            value = _to_float(latest[i]) if i < len(latest) else None
            if value is None:
                continue
            change = change_pct = None
            if prev is not None and i < len(prev):
                pv = _to_float(prev[i])
                if pv is not None and pv > 0:
                    change = round(value - pv, 5)
                    change_pct = round((change / pv) * 100, 4)
            snaps.append(
                IndexSnapshotIn(
                    captured_at=captured_at,
                    exchange="DSE",
                    index_name=name,
                    value=value,
                    change=change,
                    change_pct=change_pct,
                    source="dsebd_recent_market_info",
                )
            )

        if not snaps:
            logger.info("DSE index: no indices parsed")
        return snaps
