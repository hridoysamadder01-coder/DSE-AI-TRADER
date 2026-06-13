"""DSE index snapshot (DSEX, DS30, DSES) from the public ticker page."""
from __future__ import annotations

from datetime import datetime, timezone

from bs4 import BeautifulSoup
from loguru import logger

from ..config import get_settings
from ..schemas import IndexSnapshotIn, TickIn
from .base import BaseCollector
from .dse import _to_float, _to_int
from .http import fetch_html


class DSEIndexSnapshotCollector(BaseCollector):
    name = "dse_index"
    URL_PATH = "/"  # ticker bar lives on the homepage

    def fetch(self) -> tuple[list[TickIn], list[IndexSnapshotIn]]:
        settings = get_settings()
        html = fetch_html(settings.dse_base_url + self.URL_PATH)
        return [], self._parse(html)

    def _parse(self, html: str) -> list[IndexSnapshotIn]:
        soup = BeautifulSoup(html, "lxml")
        captured_at = datetime.now(timezone.utc)
        snaps: list[IndexSnapshotIn] = []

        # The homepage ticker exposes DSEX/DS30/DSES in a small marquee table.
        # We look for any element whose text starts with the index name and
        # extract the two adjacent numbers (value, change).
        text = soup.get_text(" ", strip=True)
        for index_name in ("DSEX", "DS30", "DSES"):
            idx = text.find(index_name)
            if idx == -1:
                continue
            window = text[idx : idx + 120]
            nums: list[float] = []
            for token in window.replace(",", "").split():
                try:
                    nums.append(float(token))
                except ValueError:
                    continue
                if len(nums) == 3:
                    break
            if not nums:
                continue
            value = nums[0] if len(nums) >= 1 else None
            change = nums[1] if len(nums) >= 2 else None
            change_pct = nums[2] if len(nums) >= 3 else None
            snaps.append(
                IndexSnapshotIn(
                    captured_at=captured_at,
                    exchange="DSE",
                    index_name=index_name,
                    value=value,
                    change=change,
                    change_pct=change_pct,
                    source="dsebd_home",
                )
            )

        if not snaps:
            logger.info("DSE index: no indices parsed (page layout may have changed)")
        return snaps
