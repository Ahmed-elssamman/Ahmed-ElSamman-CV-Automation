"""Read-only discovery for the audited public Odoo recruitment layout.

Follow the board's real Next links and retrieve complete target-role pages.
Application URLs are recorded, never opened or posted by this adapter.
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urljoin, urlsplit

from bs4 import BeautifulSoup


def public_jobs(source: dict) -> tuple[list[dict], str]:
    from .jobs import ROLE_PATTERN, _fetch, _now, plain_text

    url = source["url"]
    base = urlsplit(url)
    if base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password:
        raise ValueError("Odoo board requires a public HTTP(S) URL without credentials")
    if base.path.rstrip("/") != "/jobs" or base.query or base.fragment:
        raise ValueError("Odoo adapter supports the unfiltered /jobs board only")
    max_pages, max_listings = source.get("max_pages", 20), source.get("max_listings", 250)
    if type(max_pages) is not int or not 1 <= max_pages <= 50 or type(max_listings) is not int or not 1 <= max_listings <= 1000:
        raise ValueError("Invalid Odoo discovery bounds")

    def scoped(href: str, pattern: str) -> str:
        target = urljoin(url, href)
        parts = urlsplit(target)
        if ((parts.scheme, parts.netloc.lower()) != (base.scheme, base.netloc.lower())
                or parts.fragment or parts.query or not re.fullmatch(pattern, parts.path)):
            raise ValueError("Odoo link left the audited origin/path or changed filters")
        return target

    cards, page_records, expected_total, next_url = {}, [], None, url
    fingerprints = set()
    for page_number in range(1, max_pages + 1):
        markup = _fetch(next_url, expect_json=False)
        dom = BeautifulSoup(markup, "html.parser")
        grids = dom.select("#wrap.o_website_hr_recruitment_jobs_list #jobs_grid")
        if len(grids) != 1:
            raise ValueError("Odoo listing layout is missing or ambiguous")
        total_labels = [a.get_text(" ", strip=True) for a in dom.select('a[href]')
                        if parse_qsl(urlsplit(a["href"]).query) == [("all_departments", "1")]]
        totals = {int(m.group(1)) for label in total_labels
                  if (m := re.fullmatch(r"All Departments\s+(\d+)", label, re.I))}
        if len(totals) != 1:
            raise ValueError("Odoo board lacks an unambiguous advertised listing count")
        total = totals.pop()
        if total > max_listings or expected_total is not None and total != expected_total:
            raise ValueError("Odoo listing count exceeds the limit or changed during discovery")
        expected_total = total
        entries = grids[0].select(".card > a[href]")
        page_ids = []
        for entry in entries:
            headings = entry.select("h3")
            detail_url = scoped(entry["href"], r"/jobs/[a-zA-Z0-9_-]+-\d+")
            identifier = re.search(r"-(\d+)$", urlsplit(detail_url).path).group(1)
            if len(headings) != 1 or not headings[0].get_text(strip=True):
                raise ValueError("Odoo vacancy title is missing or ambiguous")
            title = headings[0].get_text(" ", strip=True)
            page_ids.append(identifier)
            if identifier in cards:
                raise ValueError("Odoo repeated a vacancy ID across listing pages")
            cards[identifier] = {"title": title, "url": detail_url, "listing_page": next_url}
        fingerprint = tuple(sorted(page_ids))
        if fingerprint in fingerprints or not page_ids and (total != 0 or page_number != 1):
            raise ValueError("Odoo returned a repeated or unexpectedly empty listing page")
        fingerprints.add(fingerprint)
        if len(cards) > total:
            raise ValueError("Odoo listing pages exceed the advertised count")
        page_records.append({"url": next_url, "page": page_number,
                             "sha256": hashlib.sha256(markup.encode()).hexdigest(), "listing_ids": page_ids})
        pagers = dom.select("ul.pagination")
        if len(pagers) > 1:
            raise ValueError("Odoo pagination is ambiguous")
        following = []
        if pagers:
            active = pagers[0].select("li.active > a")
            if len(active) != 1 or active[0].get_text(strip=True) != str(page_number):
                raise ValueError("Odoo active page does not match the requested page")
            for item in pagers[0].select("li"):
                if "disabled" in item.get("class", []):
                    continue
                link = item.select_one('a[href]:has([aria-label="Next"])')
                if link:
                    following.append(scoped(link["href"], rf"/jobs/page/{page_number + 1}"))
        if len(following) > 1:
            raise ValueError("Odoo has multiple Next links")
        if not following:
            if len(cards) != total:
                raise ValueError("Odoo pagination ended before the advertised count was retrieved")
            break
        next_url = following[0]
    else:
        raise ValueError("Odoo pagination exceeds the configured page limit")

    jobs = []
    for identifier, card in cards.items():
        # A department named Software Engineering is not itself a target title.
        if not ROLE_PATTERN.search(card["title"]):
            continue
        markup = _fetch(card["url"], expect_json=False)
        dom = BeautifulSoup(markup, "html.parser")
        roots = dom.select("#wrap.js_hr_recruitment")
        if len(roots) != 1:
            raise ValueError("Odoo vacancy detail layout is missing or ambiguous")
        root = roots[0]
        headings = root.select("h1")
        if len(headings) != 1 or headings[0].get_text(" ", strip=True) != card["title"]:
            raise ValueError("Odoo vacancy detail title does not match its listing")
        apply_urls = {scoped(a["href"], r"/jobs/apply/[a-zA-Z0-9_-]+-\d+")
                      for a in root.select('a[href*="/jobs/apply/"]')}
        expected_apply = card["url"].replace("/jobs/", "/jobs/apply/", 1)
        if apply_urls != {expected_apply}:
            raise ValueError("Odoo application route does not identify the listed vacancy")
        descriptions = [el for el in root.find_all("div", recursive=False)
                        if "oe_structure" not in el.get("class", []) and el.select_one("section")]
        if len(descriptions) != 1:
            raise ValueError(f"Odoo full vacancy description is missing or ambiguous: {card['url']}")
        description = plain_text(str(descriptions[0]))
        # Some correctly identified public vacancies have an empty description.
        # Preserve the missing fact so eligibility blocks this job, not every
        # unrelated listing on the same employer board.
        addresses = root.select('section [itemprop="address"][itemtype$="PostalAddress"]')
        address = {}
        if len(addresses) > 1:
            raise ValueError("Odoo vacancy location is ambiguous")
        if addresses:
            for field in ["addressCountry", "addressLocality", "addressRegion", "streetAddress"]:
                value = addresses[0].select_one(f'[itemprop="{field}"]')
                if value:
                    address[field] = value.get("content") or value.get_text(" ", strip=True)
        jobs.append({"external_id": base.hostname.lower() + ":" + identifier,
                     "title": card["title"], "description": description, "job_url": card["url"],
                     "apply_url": expected_apply, "jobLocation": {"address": address},
                     "_source_url": card["url"], "source_kind": "employer_published",
                     "provenance": {"retrieved_at": _now(), "listing_url": url,
                                    "listing_pages": page_records, "advertised_listings": expected_total,
                                    "publisher_job_id": identifier, "detail_url": card["url"],
                                    "description_status": "PRESENT" if description else "MISSING",
                                    "detail_sha256": hashlib.sha256(markup.encode()).hexdigest(),
                                    "description_sha256": hashlib.sha256(description.encode()).hexdigest(),
                                    "limitations": ["Posting date and employment type are not exposed by this audited layout.",
                                                    "An empty vacancy address stays unknown; employer headquarters do not establish job location."]}})
    return jobs, url
