"""DSE (Dhaka Stock Exchange) collector.

Source: https://www.dsebd.org/latest_share_price_scroll_l.php
Layout note: the page renders a single HTML table where each row holds a
symbol, LTP, high, low, close (yesterday close), trade, volume, value (mn).
Column order has been stable for years, but we still match on header text
where possible so a column add doesn't silently misalign data.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from loguru import logger

from ..config import get_settings
from ..schemas import IndexSnapshotIn, TickIn
from .base import BaseCollector
from .http import fetch_html

_NUM_RE = re.compile(r"-?\d+(?:,\d{3})*(?:\.\d+)?")


def _to_float(text: str | None) -> float | None:
    if text is None:
        return None
    text = text.strip().replace(",", "")
    if not text or text in {"-", "--", "N/A"}:
        return None
    m = _NUM_RE.search(text)
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _to_int(text: str | None) -> int | None:
    f = _to_float(text)
    return int(f) if f is not None else None


class DSELatestPriceCollector(BaseCollector):
    name = "dse_latest"

    LATEST_URL_PATH = "/latest_share_price_scroll_l.php"

    def fetch(self) -> tuple[list[TickIn], list[IndexSnapshotIn]]:
        settings = get_settings()
        url = settings.dse_base_url + self.LATEST_URL_PATH
        html = fetch_html(url)
        return self._parse(html), []

    def _parse(self, html: str) -> list[TickIn]:
        soup = BeautifulSoup(html, "lxml")
        table = soup.find("table", class_="table")
        if table is None:
            table = soup.find("table")  # last-resort fallback
        if table is None:
            logger.warning("DSE latest: no table found in markup")
            return []

        headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
        col_idx = {h: i for i, h in enumerate(headers)}

        def col(name: str) -> int | None:
            for key in (name, name.upper(), name.lower()):
                if key in col_idx:
                    return col_idx[key]
            for h, i in col_idx.items():
                if name in h:
                    return i
            return None

        idx_symbol = col("trading code") or col("symbol") or 1
        idx_ltp = col("ltp")
        idx_high = col("high")
        idx_low = col("low")
        # ycp = yesterday's closing price (DSE column "YCP*"), the basis for the
        # CHANGE column. Must NOT be confused with "CLOSEP*" (today's close),
        # which is 0 intraday and would zero out every change calculation.
        idx_ycp = col("ycp") or col("close")
        idx_trades = col("trade")
        idx_volume = col("volume")
        idx_value = col("value")  # value in million Tk

        captured_at = datetime.now(timezone.utc)
        ticks: list[TickIn] = []

        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            symbol_cell = tds[idx_symbol] if idx_symbol < len(tds) else None
            if symbol_cell is None:
                continue
            symbol = symbol_cell.get_text(strip=True)
            if not symbol or symbol.lower() in {"trading code", "symbol"}:
                continue

            def cell(i: int | None) -> str | None:
                if i is None or i >= len(tds):
                    return None
                return tds[i].get_text(strip=True)

            ltp = _to_float(cell(idx_ltp))
            high = _to_float(cell(idx_high))
            low = _to_float(cell(idx_low))
            ycp = _to_float(cell(idx_ycp))
            trades = _to_int(cell(idx_trades))
            volume = _to_int(cell(idx_volume))
            value_mn = _to_float(cell(idx_value))
            value_bdt = value_mn * 1_000_000 if value_mn is not None else None

            change = None
            change_pct = None
            if ltp is not None and ycp is not None and ycp > 0:
                change = round(ltp - ycp, 4)
                change_pct = round((change / ycp) * 100, 4)

            ticks.append(
                TickIn(
                    symbol=symbol.upper(),
                    name=None,
                    sector=None,
                    exchange="DSE",
                    captured_at=captured_at,
                    ltp=ltp,
                    open=None,
                    high=high,
                    low=low,
                    ycp=ycp,
                    change=change,
                    change_pct=change_pct,
                    trades=trades,
                    volume=volume,
                    value_bdt=value_bdt,
                    source="dsebd_latest",
                )
            )

        return ticks
