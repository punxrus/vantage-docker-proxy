"""Prices + 1-month sparkline from Yahoo Finance's public chart API.

The API is unofficial (Glance uses it too): no key, but it can change or
rate-limit without notice. When it does, the widget shows its last good data
with a "stale" badge, then an error state.
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from pydantic import Field

from ..config import Model
from .base import BaseWidget, WidgetError, WidgetOptions

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CURRENCY = {"USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "INR": "₹", "CAD": "C$", "AUD": "A$"}


class Market(Model):
    symbol: str
    name: str | None = None
    symbol_link: str | None = None
    chart_link: str | None = None


class MarketsOptions(WidgetOptions):
    markets: list[Market] = Field(min_length=1)
    sort_by: Literal["none", "change", "absolute-change"] = "none"
    symbol_link_template: str | None = None
    chart_link_template: str | None = None


class Widget(BaseWidget):
    type = "markets"
    Options = MarketsOptions
    default_cache = "1h"
    options: MarketsOptions

    async def _one(self, m: Market) -> dict[str, Any]:
        url = CHART.format(symbol=quote(m.symbol, safe=""))
        try:
            data = await self.get_json(url, params={"range": "1mo", "interval": "1d"})
        except WidgetError as exc:
            if "429" in exc.message:
                raise WidgetError("Yahoo Finance is rate-limiting requests (HTTP 429); will retry later", source=url) from exc
            raise
        try:
            result = data["chart"]["result"][0]
            meta = result["meta"]
            closes = [c for c in result["indicators"]["quote"][0].get("close", []) if c is not None]
        except (KeyError, IndexError, TypeError) as exc:
            err = ((data or {}).get("chart") or {}).get("error") or {}
            raise WidgetError(err.get("description") or f"no data for {m.symbol}", source=url) from exc
        price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
        if price is None:
            raise WidgetError(f"no price for {m.symbol}", source=url)
        change = meta.get("regularMarketChangePercent")
        if change is None and len(closes) >= 2 and closes[-2]:
            change = (price - closes[-2]) / closes[-2] * 100
        return {
            "symbol": m.symbol,
            "name": m.name or meta.get("shortName") or m.symbol,
            "price": price,
            "currency": CURRENCY.get(meta.get("currency", ""), ""),
            "change": change or 0.0,
            "closes": closes[-21:],
        }

    async def fetch(self) -> dict[str, Any]:
        # One at a time: Yahoo rate-limits bursts.
        results: list[Any] = []
        for m in self.options.markets:
            try:
                results.append(await self._one(m))
            except Exception as exc:  # noqa: BLE001 - reported per symbol below
                results.append(exc)
        rows, failed = [], []
        for m, r in zip(self.options.markets, results):
            if isinstance(r, WidgetError):
                failed.append(f"{m.symbol}: {r.message}")
            elif isinstance(r, BaseException):
                failed.append(f"{m.symbol}: {r}")
            else:
                rows.append(r)
        if not rows:
            raise WidgetError("; ".join(failed), source="query1.finance.yahoo.com")
        return {"rows": rows, "failed": failed}

    def present(self, data: dict[str, Any]) -> dict[str, Any]:
        rows = list(data["rows"])
        if self.options.sort_by == "change":
            rows.sort(key=lambda r: r["change"], reverse=True)
        elif self.options.sort_by == "absolute-change":
            rows.sort(key=lambda r: abs(r["change"]), reverse=True)
        links = {m.symbol: m for m in self.options.markets}
        out = []
        for r in rows:
            m = links.get(r["symbol"])
            sym_tpl = self.options.symbol_link_template
            chart_tpl = self.options.chart_link_template
            out.append(
                {
                    **r,
                    "symbol_link": (m and m.symbol_link) or (sym_tpl.replace("{SYMBOL}", r["symbol"]) if sym_tpl else None),
                    "chart_link": (m and m.chart_link)
                    or (chart_tpl.replace("{SYMBOL}", r["symbol"]) if chart_tpl else f"https://finance.yahoo.com/quote/{quote(r['symbol'])}"),
                    "price_text": f"{r['price']:,.2f}" if r["price"] >= 1 else f"{r['price']:,.4f}",
                }
            )
        return {"rows": out, "failed": data.get("failed", [])}
