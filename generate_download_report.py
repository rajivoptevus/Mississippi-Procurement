from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse, urljoin

import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(r"C:\Scraping\Mississippi-Procurement")
SCRAPED_DATA_DIR = BASE_DIR / "scraped_data"
DOWNLOADED_RFPS_DIR = BASE_DIR / "downloaded_rfps"
OUTPUT_JSON = BASE_DIR / "rfp_download_report_from_mapping_rfid_bidids.json"
ALLOWED_FILE_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".txt"}


def normalize_url(url: str | None) -> str | None:
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    return None


def parent_website(url: str | None) -> str | None:
    normalized = normalize_url(url)
    if not normalized:
        return None
    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def extract_rfp_number(text: str | None) -> str | None:
    if not text:
        return None

    patterns = [
        re.compile(r"\bGeneral\s+RFP\s+No\.?\s*(\d+)\b", re.IGNORECASE),
        re.compile(r"\bGeneral\s+RFP\s*(\d+)\b", re.IGNORECASE),
        re.compile(r"\bRFP\s+No\.?\s*(\d+)\b", re.IGNORECASE),
        re.compile(r"\bRFP\s*(\d+)\b", re.IGNORECASE),
        re.compile(r"\bSole\s+Source\s+No\.?\s*(\d+)\b", re.IGNORECASE),
        re.compile(r"\bSole\s+Source\s*(\d+)\b", re.IGNORECASE),
    ]

    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1)

    smart_match = re.search(r"(\d{3,})", text)
    if smart_match:
        return smart_match.group(1)

    return None


def normalize_file_name(name: str | Path) -> str:
    return unquote(str(name).strip().lower())


def fetch_its_download_links(rfp_number: str) -> list[str]:
    page_url = f"https://www.ms.gov/its/suite/Admin/Rfp?rfpNumber={rfp_number}"

    try:
        response = requests.get(page_url, timeout=30)
        response.raise_for_status()
    except Exception:
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    links: list[str] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = normalize_url(urljoin(page_url, anchor["href"]))
        if not href:
            continue

        path = urlparse(href).path.lower()
        if not any(path.endswith(ext) for ext in ALLOWED_FILE_EXTENSIONS):
            continue

        if href not in seen:
            seen.add(href)
            links.append(href)

    return links


def match_its_links(file_name: str, its_links: list[str]) -> list[str]:
    target = normalize_file_name(file_name)
    matched = []

    for link in its_links:
        link_name = normalize_file_name(Path(urlparse(link).path).name)
        if link_name == target:
            matched.append(link)

    return matched


def collect_bid_records() -> dict[str, list[dict]]:
    bid_records: dict[str, list[dict]] = defaultdict(list)

    for bid_dir in SCRAPED_DATA_DIR.glob("BidID_*"):
        details_path = bid_dir / "details.json"
        if not details_path.exists():
            continue

        try:
            data = json.loads(details_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue

        if not isinstance(data, dict):
            continue

        bid_id = data.get("bid_id")
        detail_url = normalize_url(data.get("detail_url"))
        pdf_url = normalize_url(data.get("pdf_url"))

        attachment_urls = []
        for attachment in data.get("attachments", []):
            if isinstance(attachment, dict):
                url = normalize_url(attachment.get("url"))
                if url:
                    attachment_urls.append(url)

        direct_urls = []
        for url in [pdf_url, *attachment_urls]:
            if url and url not in direct_urls:
                direct_urls.append(url)

        search_text = " ".join(
            [
                str(data.get("description", "")),
                str(data.get("smart_number", "")),
                str(data.get("rfx_number", "")),
            ]
        )

        rfp_number = extract_rfp_number(search_text)
        if not rfp_number:
            continue

        bid_records[str(rfp_number)].append(
            {
                "bid_id": bid_id,
                "detail_url": detail_url,
                "source_website": parent_website(detail_url),
                "direct_download_urls": direct_urls,
                "source_file": str(details_path),
            }
        )

    return bid_records


def build_report() -> dict:
    bid_records = collect_bid_records()
    report_entries = []

    for subdir in sorted(DOWNLOADED_RFPS_DIR.iterdir()):
        if not subdir.is_dir():
            continue

        rfp_number = subdir.name
        bid_info = bid_records.get(rfp_number, [])
        matched_bid = bid_info[0] if bid_info else {}
        its_links = fetch_its_download_links(rfp_number)

        downloaded_files = []
        for file_path in sorted(subdir.iterdir()):
            if not file_path.is_file():
                continue

            file_links = match_its_links(file_path.name, its_links)
            if not file_links:
                file_links = matched_bid.get("direct_download_urls", [])

            direct_links = []
            for url in file_links:
                direct_links.append(
                    {
                        "direct_download_url": url,
                        "parent_website_url": parent_website(url),
                    }
                )

            downloaded_files.append(
                {
                    "file_name": file_path.name,
                    "file_path": str(file_path),
                    "direct_download_urls": direct_links,
                }
            )

        source_website = parent_website(its_links[0]) if its_links else matched_bid.get("source_website")

        report_entries.append(
            {
                "subdirectory": subdir.name,
                "rfp_number": rfp_number,
                "bid_id": matched_bid.get("bid_id"),
                "detail_page_url": matched_bid.get("detail_url") or f"https://www.ms.gov/its/suite/Admin/Rfp?rfpNumber={rfp_number}",
                "scraped_from_website": source_website,
                "matched_bid_source_file": matched_bid.get("source_file"),
                "downloaded_files": downloaded_files,
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "downloaded_rfps_directory": str(DOWNLOADED_RFPS_DIR),
        "scraped_data_directory": str(SCRAPED_DATA_DIR),
        "total_subdirectories": len(report_entries),
        "report": report_entries,
    }


def main() -> None:
    report_data = build_report()
    OUTPUT_JSON.write_text(
        json.dumps(report_data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
