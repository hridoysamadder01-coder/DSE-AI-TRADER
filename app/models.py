from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Sector(Base):
    __tablename__ = "sectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    companies: Mapped[list["Company"]] = relationship(back_populates="sector")


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    exchange: Mapped[str] = mapped_column(String(8), index=True)  # DSE | CSE
    sector_id: Mapped[int | None] = mapped_column(ForeignKey("sectors.id"), nullable=True)
    is_active: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    sector: Mapped[Sector | None] = relationship(back_populates="companies")
    ticks: Mapped[list["PriceTick"]] = relationship(back_populates="company")
    daily: Mapped[list["PriceDaily"]] = relationship(back_populates="company")


class PriceTick(Base):
    """Intraday snapshot of latest share price (scraped every N minutes)."""

    __tablename__ = "price_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ltp: Mapped[float | None] = mapped_column(Float, nullable=True)  # last traded price
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ycp: Mapped[float | None] = mapped_column(Float, nullable=True)  # yesterday close
    change: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_bdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32))  # dsebd_latest, cse_latest, etc.

    company: Mapped[Company] = relationship(back_populates="ticks")

    __table_args__ = (
        Index("ix_ticks_company_time", "company_id", "captured_at"),
    )


class PriceDaily(Base):
    """End-of-day OHLCV row per (company, trade_date)."""

    __tablename__ = "price_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    ycp: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_bdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    company: Mapped[Company] = relationship(back_populates="daily")

    __table_args__ = (
        UniqueConstraint("company_id", "trade_date", name="uq_daily_company_date"),
    )


class MarketSnapshot(Base):
    """Index-level snapshot (DSEX, DS30, CSCX, etc.)."""

    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    exchange: Mapped[str] = mapped_column(String(8))
    index_name: Mapped[str] = mapped_column(String(32))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    change: Mapped[float | None] = mapped_column(Float, nullable=True)
    change_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_trades: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_value_bdt: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(32))


class CollectionRun(Base):
    """One row per scheduled collector execution."""

    __tablename__ = "collection_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collector: Mapped[str] = mapped_column(String(64), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | failed
    rows_in: Mapped[int] = mapped_column(Integer, default=0)
    rows_written: Mapped[int] = mapped_column(Integer, default=0)
    rows_rejected: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)


class DataQualityLog(Base):
    """Each validation failure or anomaly we want to keep visible."""

    __tablename__ = "data_quality_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    collector: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(16))  # info | warn | error
    code: Mapped[str] = mapped_column(String(64))  # MISSING_LTP, NEG_PRICE, STALE_TICK ...
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
