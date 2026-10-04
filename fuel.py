"""Fuel price scrapers + a disk-backed cache that protects the source sites.

Rules:
  * A provider is scraped at most once per CACHE_TTL seconds (default 1h),
    even if the last attempt failed (so errors don't cause retry hammering).
  * Everything else (/fuel, daily messages) only reads the cache.
  * The cache is saved to disk, so restarting the bot doesn't trigger refetches.
"""
import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

TZ = ZoneInfo(os.getenv("BOT_TZ", "Europe/Riga"))
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))  # seconds
CACHE_FILE = Path(os.getenv("CACHE_FILE", "cache.json"))
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; fuel-bot/1.0)"}
ORDER = ["95", "98", "DD", "DD+"]


# ---------------------------------------------------------------- helpers
def to_float(text: str) -> Optional[float]:
    m = re.search(r"\d+[.,]\d+", text or "")
    return float(m.group().replace(",", ".")) if m else None


def entry(price_text: str, address: Optional[str] = None) -> dict:
    return {"price": to_float(price_text), "raw": price_text.strip(), "address": address}


def result(label: str, source: str, prices: dict) -> dict:
    return {"label": label, "source": source, "prices": prices}


async def get_soup(client: httpx.AsyncClient, url: str) -> BeautifulSoup:
    r = await client.get(url)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


# ---------------------------------------------------------------- scrapers
async def straujupite(client: httpx.AsyncClient) -> dict:
    soup = await get_soup(client, "https://straujupite.lv/degvielas-cenas/")
    grid = soup.find("div", class_="sj-fuelgrid")
    prices = grid.find_all("span", class_="sj-fprice")

    def pair(tag):
        addr = tag.find_next("span", class_="sj-faddr")
        return entry(tag.get_text(strip=True), addr.get_text(strip=True) if addr else None)

    return result("Straujupite", "official website", {"95": pair(prices[0]), "DD": pair(prices[1])})


async def virsi(client: httpx.AsyncClient) -> dict:
    soup = await get_soup(client, "https://www.virsi.lv/lv/privatpersonam/degviela/degvielas-un-elektrouzlades-cenas")
    grid = soup.find_all("ul", class_="prices-grid")[0]
    found = {}
    for card in grid.find_all("div", class_="price-card"):
        dtype = card.get("data-type")
        price_tag = card.find("p", class_="price")
        addr_tag = card.find("p", class_="address")
        if not dtype or not price_tag or not addr_tag:
            continue
        spans = price_tag.find_all("span")
        if len(spans) < 2:
            continue
        found[dtype] = entry(spans[1].get_text(strip=True), addr_tag.get_text(strip=True))

    mapping = {"95e": "95", "98e": "98", "dd": "DD"}
    return result("Virsi", "official website", {v: found[k] for k, v in mapping.items() if k in found})


async def circlek(client: httpx.AsyncClient) -> dict:
    soup = await get_soup(client, "https://www.circlek.lv/degviela-miles/degvielas-cenas")
    found = {}
    for row in soup.find_all("tbody")[0].find_all("tr"):
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        found[cells[0].get_text(strip=True)] = entry(
            cells[1].get_text(strip=True), cells[2].get_text(strip=True)
        )

    mapping = {"95miles": "95", "98miles+": "98", "Dmiles": "DD", "Dmiles+": "DD+"}
    return result("Circle K", "official website", {v: found[k] for k, v in mapping.items() if k in found})


async def neste(client: httpx.AsyncClient) -> dict:
    soup = await get_soup(client, "https://xydata.lv/degviela/neste")
    found = {}
    for card in soup.find_all("div", class_="market-cap-card"):
        label = card.find("b")
        price = card.find("span", class_="price")
        if label and price:
            found[label.get_text(separator=" ", strip=True)] = entry(price.get_text(strip=True))

    mapping = {"Neste 95 benzīns": "95", "Neste 98 benzīns": "98", "Neste dīzeļdegviela": "DD"}
    return result("Neste", "3rd party (xydata.lv)", {v: found[k] for k, v in mapping.items() if k in found})


# TODO: kool -- add a scraper here and register it below
PROVIDERS = {
    "straujupite": straujupite,
    "virsi": virsi,
    "circlek": circlek,
    "neste": neste,
}

# ---------------------------------------------------------------- cache
# name -> {"data": {...}, "updated": ts, "attempted": ts, "error": str|None}
_state: dict = {}
_lock = asyncio.Lock()


def load_cache() -> None:
    try:
        _state.update(json.loads(CACHE_FILE.read_text(encoding="utf-8")))
        log.info("Loaded fuel cache from %s", CACHE_FILE)
    except FileNotFoundError:
        pass
    except Exception:
        log.exception("Could not read cache file, starting empty")


def _save_cache() -> None:
    try:
        CACHE_FILE.write_text(json.dumps(_state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("Could not write cache file")


async def refresh_if_needed() -> None:
    """Scrape only providers whose last attempt is older than CACHE_TTL."""
    async with _lock:  # concurrent callers wait, then find a fresh cache
        now = time.time()
        due = [n for n in PROVIDERS if now - _state.get(n, {}).get("attempted", 0) >= CACHE_TTL]
        if not due:
            return
        log.info("Refreshing: %s", ", ".join(due))
        async with httpx.AsyncClient(headers=HEADERS, timeout=15, follow_redirects=True) as client:
            results = await asyncio.gather(*(PROVIDERS[n](client) for n in due), return_exceptions=True)
        for name, res in zip(due, results):
            st = _state.setdefault(name, {})
            st["attempted"] = now
            if isinstance(res, Exception):
                log.warning("%s failed: %r", name, res)
                st["error"] = type(res).__name__  # keep the old data, if any
            else:
                st.update(data=res, updated=now, error=None)
        _save_cache()


# ---------------------------------------------------------------- output
def _fmt(e: dict) -> str:
    price = f"{e['price']:.3f} €" if e.get("price") is not None else e["raw"]
    return f"{price}, {e['address']}" if e.get("address") else price


def _sort_key(f: str) -> int:
    return ORDER.index(f) if f in ORDER else 99


def _best_overall(fuels) -> list[str]:
    """One line per fuel: the cheapest price across all providers."""
    lines = []
    for fuel in sorted(fuels, key=_sort_key):
        offers = [
            (e["price"], st["data"]["label"], e)
            for st in _state.values()
            if "data" in st
            for f, e in st["data"]["prices"].items()
            if f == fuel and e.get("price") is not None
        ]
        if offers:
            _, label, e = min(offers, key=lambda o: o[0])
            lines.append(f"{fuel}: {label}, {_fmt(e)}")
    return lines


def format_prices(fuels=None) -> str:
    """fuels: iterable of fuel codes to show (None = all)."""
    wanted = set(fuels) if fuels else set(ORDER)
    lines = ["⛽ Fuel prices (cheapest station per fuel)"]
    best = _best_overall(wanted)
    if best:
        lines += ["\n🏆 Cheapest overall", *best]
    for name in PROVIDERS:
        st = _state.get(name)
        if not st or "data" not in st:
            lines.append(f"\n{name}: no data yet" + (f" ({st['error']})" if st and st.get("error") else ""))
            continue
        data = st["data"]
        when = datetime.fromtimestamp(st["updated"], TZ).strftime("%d.%m %H:%M")
        stale = " ⚠️ outdated, last refresh failed" if st.get("error") else ""
        lines.append(f"\n{data['label']} — {data['source']}, updated {when}{stale}")
        prices = {f: e for f, e in data["prices"].items() if f in wanted}
        if not prices:
            lines.append("none of your selected fuels")
        for fuel in sorted(prices, key=_sort_key):
            lines.append(f"{fuel}: {_fmt(prices[fuel])}")
    return "\n".join(lines)
