#!/usr/bin/env python3
"""
Mississippi ITS Procurement Document Downloader
================================================
For each BidID_XXXXX folder:
  1. Finds the PDF containing a URL (e.g. "001_RFP No. XXXX URL.pdf")
  2. Extracts the URL from that PDF
  3. Opens the procurement listing page and finds all RFP/Bid number links
  4. Visits each bid detail page
  5. Downloads every document (PDF, DOCX, DOC, XLSX, XLS, etc.)

Requirements:
    pip install requests beautifulsoup4 selenium pdfplumber lxml
    Also install ChromeDriver matching your Chrome version, or use:
    pip install webdriver-manager
"""

import os
import re
import time
import logging
import requests
import pdfplumber
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
from bs4 import BeautifulSoup

# Selenium for JS-rendered pages
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException

# Auto-manage ChromeDriver (optional but recommended)
try:
    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service
    USE_WDM = True
except ImportError:
    USE_WDM = False

# ─────────────────────────────────────────────
# CONFIGURATION — edit these as needed
# ─────────────────────────────────────────────
BASE_SCRAPING_DIR = r"C:\Scraping\Mississippi-Procurement\scraped_data"

# Downloadable file extensions to look for
DOWNLOAD_EXTENSIONS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".txt", ".zip"}

# Seconds to wait for a page to load in Selenium
PAGE_LOAD_TIMEOUT = 20

# Delay between requests (be polite to the server)
REQUEST_DELAY = 1.5

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(BASE_SCRAPING_DIR, "download_log.txt"), encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
# STEP 1 ─ Extract URL from the PDF in BidID_XXXXX folder
# ══════════════════════════════════════════════════════════

def find_url_pdf(bid_dir: Path) -> Path | None:
    """Return the first PDF whose name looks like a URL reference."""
    for f in sorted(bid_dir.iterdir()):
        if f.suffix.lower() == ".pdf":
            name_lower = f.name.lower()
            if "url" in name_lower or "rfp" in name_lower:
                return f
    # Fall back to any PDF
    pdfs = [f for f in bid_dir.iterdir() if f.suffix.lower() == ".pdf"]
    return pdfs[0] if pdfs else None


def extract_urls_from_pdf(pdf_path: Path) -> list[str]:
    """Extract all http/https URLs from every page of a PDF."""
    urls = []
    url_pattern = re.compile(r"https?://[^\s\"'<>\[\]]+")
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                found = url_pattern.findall(text)
                urls.extend(found)

                # Also check hyperlink annotations
                if hasattr(page, "hyperlinks"):
                    for link in page.hyperlinks or []:
                        uri = link.get("uri", "")
                        if uri.startswith("http"):
                            urls.append(uri)
    except Exception as e:
        log.warning(f"  Could not read PDF {pdf_path.name}: {e}")
    # Deduplicate, preserve order
    seen = set()
    unique = []
    for u in urls:
        u = u.rstrip(".,;)")
        if u not in seen:
            seen.add(u)
            unique.append(u)
    return unique


# ══════════════════════════════════════════════════════════
# STEP 2 ─ Selenium browser helper
# ══════════════════════════════════════════════════════════

def build_driver(headless: bool = True) -> webdriver.Chrome:
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    if USE_WDM:
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=opts)
    return webdriver.Chrome(options=opts)


def get_page_source(driver: webdriver.Chrome, url: str, wait_selector: str = None) -> str:
    """Navigate to URL and return rendered HTML."""
    driver.get(url)
    if wait_selector:
        try:
            WebDriverWait(driver, PAGE_LOAD_TIMEOUT).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, wait_selector))
            )
        except TimeoutException:
            log.warning(f"  Timed out waiting for '{wait_selector}' on {url}")
    else:
        time.sleep(3)  # Generic wait for JS to render
    return driver.page_source


# ══════════════════════════════════════════════════════════
# STEP 3 ─ Parse the listing page → find RFP/Bid numbers
# ══════════════════════════════════════════════════════════

def find_bid_detail_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """
    Returns [(label, absolute_url), ...] for every RFP/Bid number link on the listing page.
    Matches patterns like 'No. 4733', '4733', 'RFP 4733', etc.
    """
    soup = BeautifulSoup(html, "lxml")
    bid_pattern = re.compile(r"\b\d{4,5}\b")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        text = a.get_text(strip=True)
        href = a["href"].strip()
        if not href or href.startswith("javascript") or href == "#":
            continue
        if bid_pattern.search(text) or bid_pattern.search(href):
            abs_url = urljoin(base_url, href)
            if abs_url not in seen:
                seen.add(abs_url)
                results.append((text or href, abs_url))

    return results


# ══════════════════════════════════════════════════════════
# STEP 4 ─ Parse a bid detail page → find downloadable docs
# ══════════════════════════════════════════════════════════

def find_download_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """
    Returns [(filename_hint, absolute_url), ...] for every downloadable link.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("javascript") or href == "#":
            continue
        abs_url = urljoin(base_url, href)
        parsed = urlparse(abs_url)
        ext = Path(parsed.path).suffix.lower()

        # Accept known extensions OR links whose text/href contains doc-type keywords
        text_lower = a.get_text(strip=True).lower()
        is_doc_link = (
            ext in DOWNLOAD_EXTENSIONS
            or any(kw in text_lower for kw in ["pdf", "word", "doc", "excel", "attachment", "exhibit", "rfp"])
            or any(kw in href.lower() for kw in ["pdf", "doc", "xls"])
        )

        if is_doc_link and abs_url not in seen:
            seen.add(abs_url)
            label = a.get_text(strip=True) or Path(parsed.path).name
            results.append((label, abs_url))

    return results


# ══════════════════════════════════════════════════════════
# STEP 5 ─ Download a file
# ══════════════════════════════════════════════════════════

def safe_filename(label: str, url: str, index: int) -> str:
    """Derive a safe filename from a link label or URL."""
    # Try to get from URL path first
    url_name = unquote(Path(urlparse(url).path).name)
    ext_from_url = Path(url_name).suffix.lower()

    # Clean the label for use as filename
    clean_label = re.sub(r'[\\/:*?"<>|]', "_", label)[:120]
    clean_label = clean_label.strip(". ")

    if ext_from_url in DOWNLOAD_EXTENSIONS:
        # Prefer URL filename; prefix with index for ordering
        return f"{index:03d}_{url_name}"
    elif clean_label:
        # Guess extension from label text
        for ext in [".pdf", ".docx", ".doc", ".xlsx", ".xls"]:
            if ext.lstrip(".") in label.lower():
                return f"{index:03d}_{clean_label}{ext}"
        return f"{index:03d}_{clean_label}.bin"
    return f"{index:03d}_document_{index}.bin"


def download_file(url: str, dest_path: Path, session: requests.Session) -> bool:
    """Download a single file. Returns True on success."""
    try:
        resp = session.get(url, stream=True, timeout=60)
        resp.raise_for_status()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
        size_kb = dest_path.stat().st_size // 1024
        log.info(f"    ✓ {dest_path.name}  ({size_kb} KB)")
        return True
    except Exception as e:
        log.warning(f"    ✗ Failed to download {url}: {e}")
        return False


# ══════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ══════════════════════════════════════════════════════════

def process_bid_directory(bid_dir: Path, driver: webdriver.Chrome, session: requests.Session):
    log.info(f"\n{'='*60}")
    log.info(f"Processing: {bid_dir.name}")
    log.info(f"{'='*60}")

    # ── 1. Find and read the URL PDF ──
    url_pdf = find_url_pdf(bid_dir)
    if not url_pdf:
        log.warning(f"  No PDF found in {bid_dir.name} — skipping")
        return

    log.info(f"  Reading URL from: {url_pdf.name}")
    urls = extract_urls_from_pdf(url_pdf)
    if not urls:
        log.warning(f"  No URLs found in {url_pdf.name} — skipping")
        return

    for listing_url in urls:
        log.info(f"  Found URL: {listing_url}")

        # ── 2. Load the listing/main page ──
        try:
            html = get_page_source(driver, listing_url)
        except WebDriverException as e:
            log.error(f"  Browser error loading {listing_url}: {e}")
            continue

        soup_check = BeautifulSoup(html, "lxml")
        page_title = soup_check.title.string if soup_check.title else ""
        log.info(f"  Page title: {page_title}")

        # ── 3. Decide: is this already a bid detail page, or a listing? ──
        # A detail page typically has download links directly.
        direct_downloads = find_download_links(html, listing_url)
        bid_links = find_bid_detail_links(html, listing_url)

        if direct_downloads and not bid_links:
            # This URL IS the detail page — download directly
            log.info(f"  → Direct detail page detected; {len(direct_downloads)} document(s)")
            _download_all(direct_downloads, bid_dir / "documents", session)

        elif bid_links:
            # This is a listing page — iterate each bid entry
            log.info(f"  → Listing page detected; {len(bid_links)} bid link(s)")
            for label, detail_url in bid_links:
                log.info(f"\n  ── Bid: {label}")
                log.info(f"     URL : {detail_url}")

                try:
                    detail_html = get_page_source(driver, detail_url)
                except WebDriverException as e:
                    log.error(f"     Browser error: {e}")
                    continue
                time.sleep(REQUEST_DELAY)

                doc_links = find_download_links(detail_html, detail_url)
                if not doc_links:
                    log.info("     No downloadable documents found on detail page.")
                    continue

                # Save into bid_dir / "documents" / <bid_label_sanitized>/
                safe_label = re.sub(r"[^a-zA-Z0-9_\-]", "_", label)[:60]
                dest_subdir = bid_dir / "documents" / safe_label
                _download_all(doc_links, dest_subdir, session)

        else:
            log.warning(f"  Could not find bid links or direct downloads on {listing_url}")


def _download_all(doc_links: list[tuple[str, str]], dest_dir: Path, session: requests.Session):
    """Download all (label, url) pairs into dest_dir."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    for idx, (label, url) in enumerate(doc_links, start=1):
        fname = safe_filename(label, url, idx)
        dest_path = dest_dir / fname
        if dest_path.exists():
            log.info(f"    ⟳ Already exists: {fname}")
            continue
        log.info(f"    ↓ Downloading [{idx}/{len(doc_links)}]: {label[:80]}")
        download_file(url, dest_path, session)
        time.sleep(REQUEST_DELAY)


def main():
    base = Path(BASE_SCRAPING_DIR)
    if not base.exists():
        log.error(f"Base directory not found: {base}")
        return

    # Collect all BidID_XXXXX directories
    bid_dirs = sorted(
        [d for d in base.iterdir() if d.is_dir() and d.name.startswith("BidID_")]
    )
    if not bid_dirs:
        log.error(f"No BidID_XXXXX directories found in {base}")
        return

    log.info(f"Found {len(bid_dirs)} BidID directories to process")

    # Set up shared HTTP session (for file downloads)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }
    )

    # Set up Selenium browser (shared across all BidIDs for efficiency)
    log.info("Starting Chrome browser...")
    driver = build_driver(headless=True)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

    try:
        for bid_dir in bid_dirs:
            try:
                process_bid_directory(bid_dir, driver, session)
            except Exception as e:
                log.error(f"Unexpected error processing {bid_dir.name}: {e}", exc_info=True)
    finally:
        driver.quit()
        log.info("\nBrowser closed. All done!")


if __name__ == "__main__":
    main()
