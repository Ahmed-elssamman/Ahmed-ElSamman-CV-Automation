"""Bounded discovery through an audited public WP Job Manager listing search.

Only listing search POSTs and same-origin vacancy GETs are performed. No
application endpoints, credentials, browser challenges or form fields are used.
"""
from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup


def _origin(url: str) -> tuple:
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Job Manager requires public HTTP(S) URLs without credentials")
    return parts.scheme, parts.hostname.lower(), parts.port or (443 if parts.scheme == "https" else 80)


def public_jobs(source: dict) -> tuple[list[dict], str]:
    from .jobs import _fetch, _jsonld_jobs, _now, plain_text, ROLE_PATTERN
    from .store import canonical_url

    listing_url = source["url"]
    origin = _origin(listing_url)
    endpoint = urljoin(listing_url, "/jm-ajax/get_listings/")
    queries = source.get("queries", ["front", "angular", "react", "full stack", "node", "nestjs", "web developer"])
    if not isinstance(queries, list) or not 1 <= len(queries) <= 12 or any(not isinstance(q, str) or not q.strip() or len(q) > 80 for q in queries):
        raise ValueError("Job Manager requires 1–12 explicit bounded search queries")
    max_pages, max_jobs = source.get("max_pages", 10), source.get("max_jobs", 150)
    if type(max_pages) is not int or not 1 <= max_pages <= 25 or type(max_jobs) is not int or not 1 <= max_jobs <= 250:
        raise ValueError("Job Manager pagination/detail limits are invalid")
    prefix = source.get("detail_path_prefix", "/job/")
    if not isinstance(prefix, str) or not prefix.startswith("/") or prefix == "/":
        raise ValueError("A scoped vacancy path prefix is required")
    candidates = {}
    for query in dict.fromkeys(queries):
        page_fingerprints, expected_pages = set(), None
        for page in range(1, max_pages + 1):
            form = {"search_keywords": query, "search_location": "", "per_page": 20,
                    "orderby": "date", "order": "DESC", "page": page, "show_pagination": "false"}
            form["form_data"] = urlencode({"search_keywords": query, "search_location": ""})
            response = _fetch(endpoint, form_body=form)
            if not isinstance(response, dict) or type(response.get("found_jobs")) is not bool:
                raise ValueError("Job Manager returned an invalid listing response")
            pages = response.get("max_num_pages")
            if type(pages) is not int or not 0 <= pages <= max_pages:
                raise ValueError("Job Manager advertised pagination exceeds the configured limit or is invalid")
            if expected_pages is not None and pages != expected_pages:
                raise ValueError("Job Manager pagination changed during discovery; retry a fresh search")
            expected_pages = pages
            markup = response.get("html")
            if not isinstance(markup, str):
                raise ValueError("Job Manager listing HTML is missing")
            cards = BeautifulSoup(markup, "html.parser").select("li.job_listing")
            if not response["found_jobs"]:
                if cards or pages > 1 or page != 1:
                    raise ValueError("Job Manager empty result contradicts pagination")
                break
            if not cards or pages < page:
                raise ValueError("Job Manager returned an empty or unexpected listing page")
            page_ids = []
            for card in cards:
                title, link = card.select_one("h3"), card.select_one("a[href]")
                ids = [m.group(1) for c in card.get("class", []) if (m := re.fullmatch(r"post-(\d+)", c))]
                if len(ids) != 1 or not title or not link:
                    raise ValueError("Job Manager card lacks an unambiguous vacancy identity")
                identifier = ids[0]
                detail_url = urljoin(listing_url, link["href"])
                if _origin(detail_url) != origin or not urlsplit(detail_url).path.startswith(prefix):
                    raise ValueError("Job Manager vacancy link left its audited origin/path")
                title_text = title.get_text(" ", strip=True)
                page_ids.append(identifier)
                if not ROLE_PATTERN.search(title_text):
                    continue
                key = canonical_url(detail_url)
                if key in candidates and candidates[key]["publisher_id"] != identifier:
                    raise ValueError("Job Manager changed the vacancy ID for an existing URL")
                entry = candidates.setdefault(key, {"publisher_id": identifier, "title": title_text,
                                                    "url": detail_url, "searches": []})
                entry["searches"].append({"query": query, "page": page, "request_body": form,
                                          "response_sha256": hashlib.sha256(markup.encode()).hexdigest()})
                if len(candidates) > max_jobs:
                    raise ValueError("Job Manager detail count exceeds the configured limit")
            page_key = tuple(sorted(page_ids))
            if page_key in page_fingerprints:
                raise ValueError("Job Manager repeated a listing page")
            page_fingerprints.add(page_key)
            if page == pages:
                break
    payloads = []
    for entry in candidates.values():
        page_html = _fetch(entry["url"], expect_json=False)
        records = _jsonld_jobs(page_html)
        if len(records) != 1 or " ".join(plain_text(records[0].get("title", "")).casefold().split()) != " ".join(entry["title"].casefold().split()):
            raise ValueError("Job Manager detail does not identify the listed vacancy")
        record = records[0]
        if record.get("url") and canonical_url(record["url"]) != canonical_url(entry["url"]):
            raise ValueError("Job Manager detail URL does not match the listing")
        declared_id = record.get("identifier", {})
        declared_id = declared_id.get("value") if isinstance(declared_id, dict) else declared_id
        if declared_id:
            value = html.unescape(html.unescape(str(declared_id)))
            numeric = value if value.isdigit() else (parse_qs(urlsplit(value).query).get("p") or [None])[0]
            if numeric and numeric != entry["publisher_id"]:
                raise ValueError("Job Manager publisher ID differs between listing and detail")
        dom = BeautifulSoup(page_html, "html.parser")
        closed = bool(dom.select_one(".job_position_filled, .job_position_expired"))
        payloads.append({**record, "external_id": origin[1] + ":" + entry["publisher_id"],
                         "job_url": entry["url"], "apply_url": entry["url"], "_source_url": entry["url"],
                         "source_kind": "employer_published", "status": "closed" if closed else None,
                         "provenance": {"listing_url": listing_url, "search_endpoint": endpoint, "http_method": "POST",
                                        "searches": entry["searches"], "publisher_job_id": entry["publisher_id"],
                                        "retrieved_at": _now(), "detail_url": entry["url"],
                                        "detail_sha256": hashlib.sha256(page_html.encode()).hexdigest(),
                                        "description_sha256": hashlib.sha256(str(record.get("description", "")).encode()).hexdigest(),
                                        "closure_evidence": "Employer page shows filled/expired marker" if closed else None}})
    return payloads, listing_url
