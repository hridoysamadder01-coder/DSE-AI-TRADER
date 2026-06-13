from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class TickIn(BaseModel):
    """Normalized intraday tick produced by a collector."""

    symbol: str = Field(..., max_length=32)
    name: Optional[str] = None
    sector: Optional[str] = None
    exchange: str = Field(..., pattern="^(DSE|CSE)$")
    captured_at: datetime
    ltp: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    ycp: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    trades: Optional[int] = None
    volume: Optional[int] = None
    value_bdt: Optional[float] = None
    source: str


class IndexSnapshotIn(BaseModel):
    captured_at: datetime
    exchange: str
    index_name: str
    value: Optional[float] = None
    change: Optional[float] = None
    change_pct: Optional[float] = None
    total_trades: Optional[int] = None
    total_volume: Optional[int] = None
    total_value_bdt: Optional[float] = None
    source: str


class CompanyOut(BaseModel):
    id: int
    symbol: str
    name: Optional[str]
    exchange: str
    sector: Optional[str]

    class Config:
        from_attributes = True


class PriceTickOut(BaseModel):
    captured_at: datetime
    ltp: Optional[float]
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    ycp: Optional[float]
    change: Optional[float]
    change_pct: Optional[float]
    volume: Optional[int]
    trades: Optional[int]
    value_bdt: Optional[float]
    source: str

    class Config:
        from_attributes = True


class PriceDailyOut(BaseModel):
    trade_date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    ycp: Optional[float]
    volume: Optional[int]
    value_bdt: Optional[float]
    trades: Optional[int]
    source: str

    class Config:
        from_attributes = True


class QualityIssueOut(BaseModel):
    created_at: datetime
    collector: str
    symbol: Optional[str]
    severity: str
    code: str
    detail: Optional[str]

    class Config:
        from_attributes = True


class RunOut(BaseModel):
    id: int
    collector: str
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    rows_in: int
    rows_written: int
    rows_rejected: int
    duration_ms: Optional[int]
    message: Optional[str]

    class Config:
        from_attributes = True
