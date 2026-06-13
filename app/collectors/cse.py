"""CSE (Chittagong Stock Exchange) collector.

Source: https://www.cse.com.bd/market/current_price
Parses the live price table. CSE markup is less stable than DSE, so we
do best-effort column inference by header text.
"""
from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup
from loguru import logger

from ..config import get_settings
from ..schemas import IndexSnapshotIn, TickIn
from .base import BaseCollector
from .dse import _to_float, _to_int
from .http import fetch_html


class CSELatestPriceCollector(BaseCollector):
    name = "cse_latest"

    LATEST_URL_PATH = "/market/current_price"

    def fetch(self) -> tuple[list[TickIn], list[IndexSnapshotIn]]:
        settings = get_settings()
        url = settings.cse_base_url + self.LATEST_URL_PATH
        html = fetch_html(url)
        return self._parse(html), []

    def _parse(self, html: str) -> list[TickIn]:
        soup = BeautifulSoup(html, "lxml")
        candidates = soup.find_all("table")
        if not candidates:
            logger.warning("CSE latest: no tables found")
            return []

        # Pick the table whose header contains the most price-like columns.
        best = None
        best_score = -1
        for tbl in candidates:
            headers = [th.get_text(strip=True).lower() for th in tbl.find_all("th")]
            score = sum(
                1 for h in headers if any(k in h for k in ("ltp", "open", "high", "low", "close", "ycp"))
            )
            if score > best_score:
                best = tbl
                best_score = score
        if best is None or best_score == 0:
            logger.warning("CSE latest: no price-like table identified")
            return []

        headers = [th.get_text(strip=True).lower() for th in best.find_all("th")]
        col_idx = {h: i for i, h in enumerate(headers)}

        def col(*needles: str) -> int | None:
            for n in needles:
                if n in col_idx:
                    return col_idx[n]
                for h, i in col_idx.items():
                    if n in h:
                        return i
            return None

        idx_symbol = col("stock code", "trading code", "symbol", "scrip")
        idx_ltp = col("ltp", "last")
        idx_open = col("open")
        idx_high = col("high")
        idx_low = col("low")
        idx_close = col("ycp", "close", "previous")
        idx_volume = col("volume")
        idx_trades = col("trade", "deals")
        idx_value = col("value(mn)", "value", "turnover")
        value_is_millions = idx_value is not None and "mn" in (
            headers[idx_value] if idx_value < len(headers) else ""
        )

        captured_at = datetime.now(timezone.utc)
        ticks: list[TickIn] = []

        for tr in best.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4 or idx_symbol is None:
                continue
            symbol = tds[idx_symbol].get_text(strip=True) if idx_symbol < len(tds) else ""
            if not symbol or symbol.lower() in {"trading code", "symbol", "scrip"}:
                continue

            def cell(i: int | None) -> str | None:
                if i is None or i >= len(tds):
                    return None
                return tds[i].get_text(strip=True)

            ltp = _to_float(cell(idx_ltp))
            opn = _to_float(cell(idx_open))
            high = _to_float(cell(idx_high))
            low = _to_float(cell(idx_low))
            ycp = _to_float(cell(idx_close))
            volume = _to_int(cell(idx_volume))
            trades = _to_int(cell(idx_trades))
            value_raw = _to_float(cell(idx_value))
            value_bdt = (
                value_raw * 1_000_000
                if value_raw is not None and value_is_millions
                else value_raw
            )

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
                    exchange="CSE",
                    captured_at=captured_at,
                    ltp=ltp,
                    open=opn,
                    high=high,
                    low=low,
                    ycp=ycp,
                    change=change,
                    change_pct=change_pct,
                    trades=trades,
                    volume=volume,
                    value_bdt=value_bdt,
                    source="cse_latest",
                )
            )

        return ticks
