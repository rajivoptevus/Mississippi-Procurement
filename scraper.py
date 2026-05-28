"""
Mississippi Procurement Portal Scraper
========================================
Site: https://www.ms.gov/dfa/contract_bid_search/Bid?autoloadGrid=true

HOW IT WORKS:
1. POST to /Bid/BidData?AppId=1  → gets ALL bid IDs + basic fields in one call
2. GET  /Bid/BidDetailData/{BidID} → full detail per bid (contact, attachments, items)
3. Direct requests.get() to SRM.MAGIC.MS.GOV → download each attachment

NO CAPTCHA. NO BROWSER NEEDED (except for the initial BidData POST).
Fully resumable — skips already-downloaded files.

HOW TO RUN:
    pip install requests beautifulsoup4 playwright
    playwright install chromium

    cd C:\Scraping\Mississippi-Procurement
    python scraper.py

OUTPUT:
    scraped_data/
        all_bids.json              ← all bid records with full details
        BidID_XXXXX/
            details.json           ← full detail record
            001_Attachment_Name.pdf
            002_Another_File.pdf
    manifest.json                  ← tracks downloaded files (resume support)
    scraper.log
"""

import json
import logging
import re
import time
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional, Set

import requests
from bs4 import BeautifulSoup

# ─────────────────────────── CONFIG ───────────────────────────
BASE_URL      = "https://www.ms.gov/dfa/contract_bid_search"
SAP_BASE      = "https://SRM.MAGIC.MS.GOV:443"
OUTPUT_DIR    = Path("scraped_data")
MANIFEST_FILE = Path("manifest.json")

# Set to None to scrape all bids, or a number for testing (e.g. 10)
MAX_BIDS = None

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.ms.gov/dfa/contract_bid_search/Bid?autoloadGrid=true",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# ─────────────────────────── LOGGING ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("scraper.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("ms_scraper")


# ─────────────────────────── HELPERS ──────────────────────────
def sanitize(name: str, max_len: int = 180) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', str(name))[:max_len]


def parse_ms_date(val: str) -> Optional[str]:
    """Parse ASP.NET /Date(ms)/ format → YYYY-MM-DD HH:MM."""
    if not val:
        return None
    m = re.search(r'/Date\((\d+)([+-]\d+)?\)/', val)
    if m:
        ms = int(m.group(1))
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    return val


def load_manifest() -> Set[str]:
    if MANIFEST_FILE.exists():
        try:
            return set(json.loads(MANIFEST_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_manifest(done: Set[str]):
    MANIFEST_FILE.write_text(
        json.dumps(sorted(done), indent=2), encoding="utf-8")


# ─────────────────────────── PHASE 1: GET ALL BIDS ────────────
def fetch_all_bids() -> List[Dict]:
    """
    POST to /Bid/BidData?AppId=1 to get all bids in one call.
    The DataTable is configured with iDisplayLength=9999 so all records
    come back in a single response.
    """
    url = f"{BASE_URL}/Bid/BidData?AppId=1"

    # DataTables 1.9 server-side POST params
    post_data = {
        "draw":                    "1",
        "sEcho":                   "1",
        "iDisplayStart":           "0",
        "iDisplayLength":          "9999",
        "sSearch":                 "",
        "bRegex":                  "false",
        "iSortCol_0":              "0",
        "sSortDir_0":              "asc",
        "iSortingCols":            "1",
        "columns[0][data]":        "SmartNumber",
        "columns[1][data]":        "ObjectID",
        "columns[2][data]":        "BidDescription",
        "columns[3][data]":        "BidStatus",
        "columns[4][data]":        "AdvertiseDate",
        "columns[5][data]":        "SubmissionDate",
        "columns[6][data]":        "OpeningDate",
    }

    log.info(f"  Fetching all bids from {url}")
    try:
        resp = requests.post(url, data=post_data, headers=HEADERS, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        # DataTables 1.9 format uses aaData
        rows = data.get("aaData") or data.get("data") or []
        log.info(f"  Got {len(rows)} bids from API")
        return rows

    except Exception as e:
        log.error(f"  Failed to fetch bids: {e}")
        return []


def fetch_all_bids_playwright() -> List[Dict]:
    """
    Fallback: use Playwright to load the page and intercept the BidData response.
    Used if the direct POST fails (session required).
    """
    import asyncio
    from playwright.async_api import async_playwright

    async def _fetch():
        rows = []
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page    = await browser.new_page()

            # Intercept the BidData response
            async def handle_response(response):
                if "BidData" in response.url and response.status == 200:
                    try:
                        data = await response.json()
                        nonlocal rows
                        rows = data.get("aaData") or data.get("data") or []
                        log.info(f"  Intercepted BidData: {len(rows)} rows")
                    except Exception:
                        pass

            page.on("response", handle_response)

            log.info("  Loading listing page via Playwright...")
            await page.goto(
                f"{BASE_URL}/Bid?autoloadGrid=true",
                wait_until="networkidle",
                timeout=60000
            )
            await asyncio.sleep(3)
            await browser.close()
        return rows

    return asyncio.run(_fetch())


# ─────────────────────────── PHASE 2: GET DETAILS ─────────────
def fetch_bid_detail(bid_id: int) -> Optional[Dict]:
    """
    GET /Bid/BidDetailData/{BidID} → full JSON with contact + attachments.
    No session required.
    """
    url = f"{BASE_URL}/Bid/BidDetailData/{bid_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning(f"  Detail fetch failed for BidID {bid_id}: {e}")
        return None


def parse_detail(raw: Dict) -> Dict:
    """Clean and normalize a raw BidDetailData response."""
    attachments = []
    for att in raw.get("Attachments") or []:
        att_url = att.get("Url", "")
        if att_url:
            attachments.append({
                "attachment_id":  att.get("AttachmentID"),
                "description":    att.get("Description", ""),
                "url":            att_url,
            })

    items = []
    for item in raw.get("Items") or []:
        items.append({
            "category_number":      item.get("CategoryNumber", ""),
            "category_description": item.get("CategoryDescription", ""),
        })

    awards = []
    for awd in raw.get("AwdVendor") or []:
        awards.append({
            "vendor_name":    awd.get("VendorName", ""),
            "vendor_number":  awd.get("VendorNumber", ""),
            "award_date":     parse_ms_date(awd.get("AwardDate", "")),
            "award_amount":   awd.get("AwardAmount"),
            "funding_source": awd.get("FundingSource", ""),
        })

    return {
        "bid_id":                   raw.get("BidID"),
        "smart_number":             raw.get("BidNumber", ""),
        "rfx_number":               raw.get("ObjectID", ""),
        "rfx_status":               raw.get("BidStatus", ""),
        "rfx_type":                 raw.get("BidType", ""),
        "agency":                   raw.get("Agency", ""),
        "description":              raw.get("BidDescription", ""),
        "major_procurement_category": raw.get("MajorProcurementCategory", ""),
        "sub_procurement_category": raw.get("SubProcurementCategory", ""),
        "advertised_date":          parse_ms_date(raw.get("AdvertiseDate", "")),
        "submission_date":          parse_ms_date(raw.get("SubmissionDate", "")),
        "opening_date":             parse_ms_date(raw.get("OpeningDate", "")),
        "contact_name":             raw.get("BuyerName", ""),
        "contact_email":            raw.get("BuyerEmail", ""),
        "contact_phone":            raw.get("BuyerPhone", ""),
        "contact_fax":              raw.get("BuyerFax", ""),
        "detail_url":               f"{BASE_URL}/Bid/Details/{raw.get('BidID')}?AppId=1",
        "pdf_url":                  raw.get("PDFUrl", ""),
        "attachments":              attachments,
        "items":                    items,
        "awards":                   awards,
        "scraped_at":               datetime.now(timezone.utc).isoformat(),
    }


# ─────────────────────────── PHASE 3: DOWNLOAD ATTACHMENTS ────
def download_attachment(url: str, dest: Path) -> bool:
    """
    Download a file from SRM.MAGIC.MS.GOV directly.
    No CAPTCHA — these are plain HTTP file downloads.
    """
    if dest.exists() and dest.stat().st_size > 100:
        return True  # already downloaded

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Referer": f"{BASE_URL}/Bid?autoloadGrid=true"},
            timeout=60,
            stream=True,
            verify=False,  # SRM.MAGIC.MS.GOV may have cert issues
        )
        resp.raise_for_status()

        ct = resp.headers.get("Content-Type", "").lower()
        if "html" in ct and resp.status_code == 200:
            # Check if it's actually HTML (error page)
            first = b""
            for chunk in resp.iter_content(512):
                first = chunk
                break
            if b"<!doc" in first.lower() or b"<html" in first.lower():
                log.warning(f"    Got HTML response for {url[-60:]}")
                return False

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)

        size = dest.stat().st_size
        if size < 100:
            dest.unlink(missing_ok=True)
            return False

        return True

    except Exception as e:
        log.warning(f"    Download failed {url[-60:]}: {e}")
        return False


def get_attachment_filename(att: Dict, idx: int, bid_id: int) -> str:
    """Build a clean filename for an attachment."""
    desc = att.get("description", "")
    url  = att.get("url", "")

    # Try to get extension from URL
    ext = ".pdf"
    url_path = url.split("?")[0]
    if "." in url_path.split("/")[-1]:
        ext = "." + url_path.split("/")[-1].rsplit(".", 1)[-1].lower()
        if ext not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip", ".txt"}:
            ext = ".pdf"

    # Clean description for filename
    if desc:
        base = sanitize(desc)[:100]
    else:
        base = f"attachment_{idx:03d}"

    return f"{idx:03d}_{base}{ext}"


# ─────────────────────────── MAIN ─────────────────────────────
def run():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("Mississippi Procurement Portal Scraper")
    log.info(f"Output : {OUTPUT_DIR.resolve()}")
    log.info("=" * 60)

    manifest = load_manifest()
    log.info(f"  Already downloaded: {len(manifest)} files")

    # ── Phase 1: Get all bid IDs ───────────────────────────────
    log.info("\n── Phase 1: Fetching all bids ──")
    rows = fetch_all_bids()

    if not rows:
        log.info("  Direct POST failed — trying Playwright fallback...")
        rows = fetch_all_bids_playwright()

    if not rows:
        log.error("  Could not fetch bids. Aborting.")
        return

    log.info(f"  Total bids found: {len(rows)}")

    # Extract BidIDs from rows
    # Row format varies — try common field names
    bid_ids = []
    for row in rows:
        bid_id = (row.get("BidID") or row.get("bidID") or
                  row.get("Id") or row.get("id"))
        if bid_id:
            bid_ids.append(int(bid_id))

    if not bid_ids:
        # Try to extract from SmartNumber links or other fields
        log.warning("  Could not extract BidIDs from rows — check row structure")
        log.info(f"  Sample row: {json.dumps(rows[0], indent=2)[:500] if rows else 'none'}")
        return

    log.info(f"  Extracted {len(bid_ids)} BidIDs")

    if MAX_BIDS:
        bid_ids = bid_ids[:MAX_BIDS]
        log.info(f"  Limited to {MAX_BIDS} bids for testing")

    # Save raw listing
    listing_file = OUTPUT_DIR / "bid_listing.json"
    listing_file.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    log.info(f"  Saved listing: {listing_file}")

    # ── Phase 2 & 3: Detail + Download per bid ─────────────────
    log.info(f"\n── Phase 2+3: Details + Downloads for {len(bid_ids)} bids ──")
    all_records = []
    start_time  = time.time()
    total_dl    = 0
    total_fail  = 0

    for idx, bid_id in enumerate(bid_ids, 1):
        elapsed = time.time() - start_time
        avg     = elapsed / idx
        eta_min = (len(bid_ids) - idx) * avg / 60
        log.info(f"\n  [{idx}/{len(bid_ids)}] BidID {bid_id} | ETA ~{eta_min:.0f} min")

        bid_dir = OUTPUT_DIR / f"BidID_{bid_id}"
        bid_dir.mkdir(parents=True, exist_ok=True)

        detail_file = bid_dir / "details.json"

        # Load existing detail or fetch fresh
        if detail_file.exists():
            try:
                record = json.loads(detail_file.read_text(encoding="utf-8"))
                log.info(f"    Loaded cached detail: {record.get('smart_number','')}")
            except Exception:
                record = None
        else:
            record = None

        if not record:
            raw = fetch_bid_detail(bid_id)
            if not raw:
                log.warning(f"    No detail for BidID {bid_id}")
                continue
            record = parse_detail(raw)
            detail_file.write_text(
                json.dumps(record, indent=2, default=str), encoding="utf-8")
            log.info(f"    {record.get('smart_number','')} — {record.get('agency','')[:40]}")
            log.info(f"    Contact: {record.get('contact_name','')} | {record.get('contact_email','')}")

        all_records.append(record)

        # Download attachments
        attachments = record.get("attachments", [])
        if attachments:
            log.info(f"    Downloading {len(attachments)} attachment(s)...")
            for att_idx, att in enumerate(attachments, 1):
                att_url = att.get("url", "")
                if not att_url:
                    continue

                manifest_key = f"{bid_id}:{att_url}"
                if manifest_key in manifest:
                    log.info(f"      [{att_idx}/{len(attachments)}] SKIP (done): {att.get('description','')[:50]}")
                    continue

                fname = get_attachment_filename(att, att_idx, bid_id)
                dest  = bid_dir / fname

                log.info(f"      [{att_idx}/{len(attachments)}] → {fname}")
                ok = download_attachment(att_url, dest)

                if ok:
                    size = dest.stat().st_size
                    log.info(f"        ✓ {fname} ({size:,} bytes)")
                    manifest.add(manifest_key)
                    save_manifest(manifest)
                    total_dl += 1
                else:
                    log.warning(f"        ✗ Failed: {att.get('description','')[:50]}")
                    total_fail += 1
        else:
            log.info(f"    No attachments")

        # Small delay to be polite
        time.sleep(random.uniform(0.3, 0.8))

    # ── Save combined output ───────────────────────────────────
    log.info(f"\n── Saving combined output ──")
    all_file = OUTPUT_DIR / "all_bids_complete.json"
    all_file.write_text(
        json.dumps(all_records, indent=2, default=str), encoding="utf-8")
    log.info(f"  Saved {len(all_records)} records → {all_file}")

    # Summary
    log.info(f"\n{'='*60}")
    log.info(f"DONE")
    log.info(f"  Bids processed       : {len(all_records)}")
    log.info(f"  Attachments downloaded: {total_dl}")
    log.info(f"  Attachments failed   : {total_fail}")
    log.info(f"  Output               : {OUTPUT_DIR.resolve()}")
    log.info("=" * 60)

    summary = {
        "timestamp":              datetime.now(timezone.utc).isoformat(),
        "total_bids":             len(all_records),
        "attachments_downloaded": total_dl,
        "attachments_failed":     total_fail,
    }
    Path("summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


if __name__ == "__main__":
    # Suppress SSL warnings for SRM.MAGIC.MS.GOV
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    run()
