"""Scrape the current Kelley Autoplex Carsforsale site into JSON.

This is a deferred migration tool. It is intentionally read-only: it does not
write to Postgres or import inventory. Run it after the Kelley replacement app
is deployed and just before cutting over DNS, so the captured inventory/business
data reflects the old public site at migration time.

Usage:

    python scripts/scrape_kelley_current_site.py --dry-run
    python scripts/scrape_kelley_current_site.py \
        --output data/reports/kelley_current_site_scrape.json

The output is shaped to feed the future vehicle inventory importer:
``business_profile`` contains NAP/hours, and ``vehicles`` contains one object per
detail page with VIN, specs, description, and photo URLs when present.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx


DEFAULT_BASE_URL = "https://www.kelleyautoplex.com"
DEFAULT_INVENTORY_PATH = "/cars-for-sale"
DEFAULT_OUTPUT = Path("data/reports/kelley_current_site_scrape.json")
USER_AGENT = "KelleyAutoplexMigrationScraper/1.0"


@dataclass
class ParsedPage:
    text: list[str] = field(default_factory=list)
    links: list[dict[str, str]] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)
    title: str | None = None


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.page = ParsedPage()
        self._skip_depth = 0
        self._current_link: dict[str, str] | None = None
        self._current_text: list[str] = []
        self._in_title = False
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): v or "" for k, v in attrs}
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag == "a" and attr.get("href"):
            self._current_link = {"href": attr["href"], "text": ""}
            self._current_text = []
            return
        if tag == "img":
            src = attr.get("src") or attr.get("data-src") or attr.get("data-lazy")
            if src:
                self.page.images.append(
                    {
                        "src": src,
                        "alt": attr.get("alt", ""),
                    }
                )

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            title = _clean(" ".join(self._title_parts))
            self.page.title = title or None
            return
        if tag == "a" and self._current_link is not None:
            self._current_link["text"] = _clean(" ".join(self._current_text))
            self.page.links.append(self._current_link)
            self._current_link = None
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = _clean(data)
        if not text:
            return
        if self._in_title:
            self._title_parts.append(text)
        if self._current_link is not None:
            self._current_text.append(text)
        self.page.text.append(text)


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _parse_page(html: str) -> ParsedPage:
    parser = _PageParser()
    parser.feed(html)
    return parser.page


def _abs_url(base_url: str, href: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", href)


def _same_host(url_a: str, url_b: str) -> bool:
    return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _to_int(value: str | None) -> int | None:
    if not value:
        return None
    digits = re.sub(r"[^0-9]", "", value)
    return int(digits) if digits else None


def _price_cents(value: str | None) -> int | None:
    dollars = _to_int(value)
    return dollars * 100 if dollars is not None else None


def _value_after(text: list[str], *labels: str) -> str | None:
    lowered = {label.lower() for label in labels}
    for idx, token in enumerate(text[:-1]):
        if token.rstrip(":").lower() in lowered:
            return text[idx + 1]
    return None


def _slice_after(text: list[str], start: str, *stops: str) -> list[str]:
    start_idx = next(
        (idx for idx, token in enumerate(text) if token.lower() == start.lower()),
        None,
    )
    if start_idx is None:
        return []
    stop_set = {stop.lower() for stop in stops}
    end_idx = len(text)
    for idx in range(start_idx + 1, len(text)):
        if text[idx].lower() in stop_set:
            end_idx = idx
            break
    return text[start_idx + 1 : end_idx]


def _find_vehicle_title(text: list[str], page_title: str | None) -> str | None:
    for token in text:
        if re.match(r"^(19|20)\d{2}\s+\S+", token) and " for sale " not in token:
            return token
    if page_title:
        return re.sub(r"\s+for sale.*$", "", page_title, flags=re.I)
    return None


def _split_title(title: str | None) -> dict[str, Any]:
    if not title:
        return {"year": None, "make": None, "model": None}
    match = re.match(r"^(?P<year>(19|20)\d{2})\s+(?P<rest>.+)$", title)
    if not match:
        return {"year": None, "make": None, "model": None}
    rest = match.group("rest").split()
    return {
        "year": int(match.group("year")),
        "make": rest[0] if rest else None,
        "model": " ".join(rest[1:]) if len(rest) > 1 else None,
    }


def _business_profile_from_page(page: ParsedPage, base_url: str) -> dict[str, Any]:
    text = page.text
    email = next((t for t in text if "@" in t and "." in t), None)
    phone = next((t for t in text if re.search(r"\(\d{3}\)\s*\d{3}-\d{4}", t)), None)
    address = next((t for t in text if "San Pedro" in t), None)
    city_state_zip = next((t for t in text if "San Antonio" in t and "TX" in t), None)
    hours: dict[str, str] = {}
    for day in (
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
    ):
        for token in text:
            if token.startswith(day):
                hours[day.lower()] = token.removeprefix(day).strip() or token
                break
    return {
        "business_name": "Kelley Autoplex",
        "phone": phone,
        "email": email,
        "address_line1": address,
        "city_state_zip": city_state_zip,
        "website": base_url.rstrip("/"),
        "hours": hours,
    }


def _inventory_links(page: ParsedPage, base_url: str) -> list[str]:
    links: list[str] = []
    for link in page.links:
        href = _abs_url(base_url, link["href"])
        if not _same_host(base_url, href):
            continue
        if "/inventory/details/" in urlparse(href).path.lower():
            links.append(href)
    return _unique(links)


def _images_for_vehicle(page: ParsedPage, base_url: str, title: str | None) -> list[str]:
    title_terms = [part.lower() for part in (title or "").split() if len(part) > 2]
    urls: list[str] = []
    for image in page.images:
        alt = image.get("alt", "").lower()
        if title_terms and not all(term in alt for term in title_terms[:3]):
            continue
        urls.append(_abs_url(base_url, image["src"]))
    return _unique(urls)


def _parse_vehicle_detail(url: str, html: str, base_url: str) -> dict[str, Any]:
    page = _parse_page(html)
    text = page.text
    title = _find_vehicle_title(text, page.title)
    title_parts = _split_title(title)
    subtitle = text[text.index(title) + 1] if title in text and text.index(title) + 1 < len(text) else None
    description = " ".join(_slice_after(text, "Description", "Show More", "Features"))
    features = _slice_after(text, "Features", "Fuel Economy", "Financing Available")
    city_mpg = _value_after(text, "City")
    highway_mpg = _value_after(text, "Hwy", "Highway")
    price_label = next((t for t in text if re.match(r"^\$[\d,]+$", t)), None)

    return {
        "source_url": url,
        "source_platform": "carsforsale",
        "source_product_id": urlparse(url).path.rstrip("/").split("/")[-1],
        "title": title,
        "year": title_parts["year"],
        "make": title_parts["make"],
        "model": title_parts["model"],
        "trim": _value_after(text, "Trim") or subtitle,
        "condition": _value_after(text, "Condition"),
        "vin": _value_after(text, "VIN"),
        "price_cents": _price_cents(price_label),
        "mileage": _to_int(_value_after(text, "Mileage")),
        "engine": _value_after(text, "Engine"),
        "transmission": _value_after(text, "Transmission"),
        "drivetrain": _value_after(text, "Drivetrain"),
        "fuel_type": _value_after(text, "Fuel", "Fuel Type"),
        "body_type": _value_after(text, "Vehicle Type"),
        "exterior_color": _value_after(text, "Ext. Color", "Exterior Color"),
        "interior_color": _value_after(text, "Int. Color", "Interior Color"),
        "mpg_city": _to_int(city_mpg),
        "mpg_highway": _to_int(highway_mpg),
        "description_text": description or None,
        "features": features,
        "image_urls": _images_for_vehicle(page, base_url, title),
        "raw_title": page.title,
    }


class ScrapeClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        delay: float,
        ignore_robots: bool,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.delay = delay
        self._last_fetch = 0.0
        self.client = httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        self.robots = None if ignore_robots else self._load_robots()

    def close(self) -> None:
        self.client.close()

    def _load_robots(self) -> RobotFileParser:
        robots = RobotFileParser()
        robots.set_url(_abs_url(self.base_url, "/robots.txt"))
        try:
            robots.read()
        except Exception:
            # If robots cannot be fetched, urllib leaves it in "allow all"
            # behavior. The operator still gets a source URL in the output.
            pass
        return robots

    def get(self, url: str) -> str:
        if self.robots and not self.robots.can_fetch(USER_AGENT, url):
            raise RuntimeError(f"robots.txt disallows scraping {url}")
        elapsed = time.monotonic() - self._last_fetch
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        res = self.client.get(url)
        self._last_fetch = time.monotonic()
        res.raise_for_status()
        return res.text


def scrape_current_site(
    *,
    base_url: str,
    inventory_path: str,
    max_vehicles: int | None,
    timeout: float,
    delay: float,
    ignore_robots: bool,
) -> dict[str, Any]:
    client = ScrapeClient(
        base_url=base_url,
        timeout=timeout,
        delay=delay,
        ignore_robots=ignore_robots,
    )
    try:
        home_url = base_url.rstrip("/") + "/"
        inventory_url = _abs_url(base_url, inventory_path)
        home_page = _parse_page(client.get(home_url))
        inventory_page = _parse_page(client.get(inventory_url))
        detail_urls = _inventory_links(inventory_page, base_url)
        if max_vehicles is not None:
            detail_urls = detail_urls[:max_vehicles]

        vehicles = []
        failures = []
        for url in detail_urls:
            try:
                vehicles.append(_parse_vehicle_detail(url, client.get(url), base_url))
            except Exception as exc:  # pragma: no cover - defensive run summary
                failures.append({"url": url, "error": str(exc)})

        return {
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "base_url": base_url.rstrip("/"),
            "inventory_url": inventory_url,
            "user_agent": USER_AGENT,
            "business_profile": _business_profile_from_page(home_page, base_url),
            "vehicles_found": len(detail_urls),
            "vehicles_scraped": len(vehicles),
            "vehicles_failed": len(failures),
            "failures": failures,
            "vehicles": vehicles,
        }
    finally:
        client.close()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--inventory-path", default=DEFAULT_INVENTORY_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-vehicles", type=int)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--delay", type=float, default=0.75)
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Bypass robots.txt checks. Use only with explicit site-owner approval.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summary and do not write the output JSON file.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    payload = scrape_current_site(
        base_url=args.base_url,
        inventory_path=args.inventory_path,
        max_vehicles=args.max_vehicles,
        timeout=args.timeout,
        delay=args.delay,
        ignore_robots=args.ignore_robots,
    )
    summary = {
        "vehicles_found": payload["vehicles_found"],
        "vehicles_scraped": payload["vehicles_scraped"],
        "vehicles_failed": payload["vehicles_failed"],
        "output": None if args.dry_run else str(args.output),
    }
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not args.dry_run:
        _write_json(args.output, payload)
    return 0 if payload["vehicles_failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
