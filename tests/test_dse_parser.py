from app.collectors.dse import DSELatestPriceCollector


SAMPLE = """
<html><body><table class="table">
<tr><th>#</th><th>Trading Code</th><th>LTP</th><th>High</th><th>Low</th><th>Close</th><th>YCP</th><th>Trade</th><th>Volume</th><th>Value (mn)</th></tr>
<tr><td>1</td><td>GP</td><td>320.5</td><td>325.0</td><td>318.0</td><td>319.0</td><td>319.0</td><td>1,234</td><td>56,000</td><td>17.9</td></tr>
<tr><td>2</td><td>BATBC</td><td>510.0</td><td>515.0</td><td>505.0</td><td>508.0</td><td>508.0</td><td>700</td><td>12,000</td><td>6.1</td></tr>
</table></body></html>
"""


def test_dse_parser_extracts_rows():
    ticks = DSELatestPriceCollector()._parse(SAMPLE)
    assert len(ticks) == 2
    by_sym = {t.symbol: t for t in ticks}
    assert "GP" in by_sym and "BATBC" in by_sym
    gp = by_sym["GP"]
    assert gp.ltp == 320.5
    assert gp.high == 325.0
    assert gp.low == 318.0
    assert gp.exchange == "DSE"
    assert gp.source == "dsebd_latest"
    assert gp.value_bdt == 17.9 * 1_000_000
    # change derived from ltp - ycp
    assert gp.change == 1.5
    assert gp.change_pct is not None
