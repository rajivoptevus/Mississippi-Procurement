"""
Mississippi Procurement — URL Attachment Resolver
===================================================
Some attachments are PDFs that contain only a URL (not actual documents).
This script:
1. Scans all BidID_* folders for downloaded attachment PDFs
2. Reads each PDF — if it contains just a URL (not real RFP/RFQ content):
   → Opens that URL
   → Scrapes the page for downloadable PDF links
   → Downloads those PDFs into the same BidID folder
3. If the PDF is a real document (RFP/RFQ content) → skips it

HOW TO RUN:
    pip install requests beautifulsoup4 pypdf2 playwright
    playwright install chromium

    cd C:\Scraping\Mississippi-Procurement
    python url_attachment_resolver.py
"""

import json
import logging
import re
import time
import asyncio
from pathlib import Path
from typing import Optional, List, Tuple
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# Try to import PDF reader
try:
    import PyPDF2
    HAS_PYPDF2 = True
except ImportError:
    try:
        import pypdf as PyPDF2
        HAS_PYPDF2 = True
    except ImportError:
        HAS_PYPDF2 = False

# ─────────────────────────── CONFIG ───────────────────────────
SCRAPED_DIR = Path("scraped_data")
MANIFEST_FILE = Path("url_resolver_manifest.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
}

# Keywords that indicate a PDF is a real document (not just a URL)
REAL_DOC_KEYWORDS = [
    "request for proposal", "request for quotation", "invitation for bid",
    "rfp", "rfq", "ifb", "scope of work", "specifications", "terms and conditions",
    "section 1", "section 2", "article", "whereas", "agreement", "contract",
    "vendor", "offeror", "proposal", "bid", "procurement", "mississippi",
    "department", "agency", "state of mississippi",
]

# ─────────────────────────── LOGGING ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("url_resolver.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("url_resolver")


# ─────────────────────────── HELPERS ──────────────────────────
def sanitize(name: str, max_len: int = 180) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', str(name))[:max_len]


def load_manifest() -> set:
    if MANIFEST_FILE.exists():
        try:
            return set(json.loads(MANIFEST_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_manifest(done: set):
    MANIFEST_FILE.write_text(
        json.dumps(sorted(done), indent=2), encoding="utf-8")


def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract all text from a PDF file."""
    if not HAS_PYPDF2:
        return ""
    try:
        text = ""
        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            for page in reader.pages:
                try:
                    text += page.extract_text() or ""
                except Exception:
                    pass
        return text
    except Exception as e:
        log.debug(f"  PDF read error {pdf_path.name}: {e}")
        return ""


def extract_urls_from_text(text: str) -> List[str]:
    """Extract all URLs from text."""
    url_pattern = re.compile(
        r'https?://[^\s\'"<>()[\]{}|\\^`\x00-\x1f\x7f-\xff]+'
    )
    urls = url_pattern.findall(text)
    # Clean up trailing punctuation
    cleaned = []
    for url in urls:
        url = re.sub(r'[.,;:!?)]+$', '', url)
        if len(url) > 10:
            cleaned.append(url)
    return list(set(cleaned))


def is_url_only_pdf(text: str, urls: List[str]) -> bool:
    """
    Determine if a PDF contains only a URL (not real document content).
    Returns True if it's a URL-only PDF that needs to be followed.
    """
    if not text.strip():
        return False

    # If text is very short and contains a URL → likely URL-only
    text_clean = text.strip()
    if len(text_clean) < 500 and urls:
        # Check if the text is mostly just the URL
        text_without_urls = text_clean
        for url in urls:
            text_without_urls = text_without_urls.replace(url, "")
        text_without_urls = re.sub(r'\s+', ' ', text_without_urls).strip()

        # If remaining text is very short → URL-only PDF
        if len(text_without_urls) < 100:
            return True

    # Check for real document keywords
    text_lower = text.lower()
    keyword_count = sum(1 for kw in REAL_DOC_KEYWORDS if kw in text_lower)

    # If many keywords → real document
    if keyword_count >= 3:
        return False

    # If has URLs and few keywords → likely URL-only
    if urls and keyword_count < 2:
        return True

    return False


def scrape_page_for_pdfs(url: str) -> List[Tuple[str, str]]:
    """
    Scrape a webpage for downloadable PDF links.
    Returns list of (link_text, pdf_url).
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        pdfs = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            text = a.get_text(strip=True)

            # Make absolute URL
            if not href.startswith("http"):
                href = urljoin(url, href)

            # Skip if already seen
            if href in seen:
                continue

            # Check if it's a PDF link
            href_lower = href.lower()
            is_pdf = (
                href_lower.endswith(".pdf") or
                ".pdf?" in href_lower or
                "docserver" in href_lower or  # SAP document server
                "download" in href_lower or
                "attachment" in href_lower or
                "document" in href_lower
            )

            # Also check for PDF icon nearby
            has_pdf_icon = bool(
                a.find("img", src=re.compile(r'pdf', re.I)) or
                a.find("i", class_=re.compile(r'pdf', re.I))
            )

            if is_pdf or has_pdf_icon:
                seen.add(href)
                pdfs.append((text or "document", href))

        log.info(f"    Found {len(pdfs)} PDF links on {url[:60]}")
        return pdfs

    except Exception as e:
        log.warning(f"    Failed to scrape {url[:60]}: {e}")
        return []


async def scrape_page_playwright(url: str) -> List[Tuple[str, str]]:
    """
    Use Playwright to scrape a JavaScript-rendered page for PDF links.
    Fallback when requests fails.
    """
    from playwright.async_api import async_playwright

    pdfs = []
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page    = await browser.new_page()
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(2)

            # Get all links
            links = await page.eval_on_selector_all(
                "a[href]",
                """els => els.map(el => ({
                    href: el.href,
                    text: el.textContent.trim()
                }))"""
            )

            seen = set()
            for link in links:
                href = link.get("href", "")
                text = link.get("text", "")
                if not href or href in seen:
                    continue
                href_lower = href.lower()
                if (href_lower.endswith(".pdf") or
                        ".pdf?" in href_lower or
                        "docserver" in href_lower):
                    seen.add(href)
                    pdfs.append((text or "document", href))

            await browser.close()
            log.info(f"    Playwright found {len(pdfs)} PDF links on {url[:60]}")
    except Exception as e:
        log.warning(f"    Playwright failed for {url[:60]}: {e}")

    return pdfs


def download_pdf(url: str, dest: Path) -> bool:
    """Download a PDF file."""
    if dest.exists() and dest.stat().st_size > 100:
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = requests.get(
            url, headers=HEADERS, timeout=60, stream=True, verify=False)
        resp.raise_for_status()

        ct = resp.headers.get("Content-Type", "").lower()

        with open(dest, "wb") as f:
            for chunk in resp.iter_content(8192):
                f.write(chunk)

        size = dest.stat().st_size
        if size < 100:
            dest.unlink(missing_ok=True)
            return False

        # Verify not HTML
        with open(dest, "rb") as f:
            hdr = f.read(5)
        if b"<!doc" in hdr.lower() or b"<html" in hdr.lower():
            dest.unlink(missing_ok=True)
            return False

        return True
    except Exception as e:
        log.warning(f"    Download failed {url[-60:]}: {e}")
        return False


# ─────────────────────────── MAIN ─────────────────────────────
def run():
    log.info("=" * 60)
    log.info("Mississippi — URL Attachment Resolver")
    log.info(f"Scanning: {SCRAPED_DIR.resolve()}")
    log.info("=" * 60)

    if not HAS_PYPDF2:
        log.warning("PyPDF2/pypdf not installed — install with: pip install pypdf2")
        log.warning("Will attempt basic text extraction fallback")

    manifest = load_manifest()
    log.info(f"Already resolved: {len(manifest)} URLs")

    # Find all BidID folders
    bid_dirs = sorted(SCRAPED_DIR.glob("BidID_*"))
    log.info(f"Found {len(bid_dirs)} BidID folders")

    total_resolved = 0
    total_downloaded = 0
    total_failed = 0

    for bid_dir in bid_dirs:
        bid_id = bid_dir.name.replace("BidID_", "")

        # Find all PDF attachments (not details.json)
        pdf_files = [f for f in bid_dir.iterdir()
                     if f.suffix.lower() == ".pdf" and f.name != "details.json"]

        if not pdf_files:
            continue

        for pdf_file in pdf_files:
            manifest_key = str(pdf_file)
            if manifest_key in manifest:
                continue

            log.info(f"\n  {bid_dir.name} / {pdf_file.name}")

            # Extract text from PDF
            text = extract_text_from_pdf(pdf_file)

            if not text:
                log.info(f"    Could not extract text — skipping")
                manifest.add(manifest_key)
                save_manifest(manifest)
                continue

            # Extract URLs from text
            urls = extract_urls_from_text(text)

            # Determine if URL-only or real document
            if not is_url_only_pdf(text, urls):
                log.info(f"    Real document (RFP/RFQ content) — skipping")
                manifest.add(manifest_key)
                save_manifest(manifest)
                continue

            if not urls:
                log.info(f"    URL-only PDF but no URLs found — skipping")
                manifest.add(manifest_key)
                save_manifest(manifest)
                continue

            log.info(f"    URL-only PDF detected. URLs found: {len(urls)}")
            for u in urls:
                log.info(f"      → {u}")

            # For each URL, scrape for PDFs
            for target_url in urls:
                log.info(f"    Scraping: {target_url}")

                # Try requests first
                pdf_links = scrape_page_for_pdfs(target_url)

                # Fallback to Playwright if no PDFs found
                if not pdf_links:
                    log.info(f"    No PDFs via requests — trying Playwright...")
                    pdf_links = asyncio.run(scrape_page_playwright(target_url))

                if not pdf_links:
                    log.warning(f"    No PDF links found on {target_url[:60]}")
                    continue

                log.info(f"    Downloading {len(pdf_links)} PDFs...")

                for dl_idx, (link_text, pdf_url) in enumerate(pdf_links, 1):
                    dl_key = f"{bid_id}:{pdf_url}"
                    if dl_key in manifest:
                        log.info(f"      [{dl_idx}] SKIP: {link_text[:50]}")
                        continue

                    # Build filename
                    clean_text = sanitize(link_text)[:80] if link_text else f"doc_{dl_idx:03d}"
                    # Get extension from URL
                    url_path = pdf_url.split("?")[0]
                    ext = ".pdf"
                    if "." in url_path.split("/")[-1]:
                        ext = "." + url_path.split("/")[-1].rsplit(".", 1)[-1].lower()
                        if ext not in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"}:
                            ext = ".pdf"

                    # Prefix with "resolved_" to distinguish from original attachments
                    fname = f"resolved_{dl_idx:03d}_{clean_text}{ext}"
                    dest  = bid_dir / fname

                    log.info(f"      [{dl_idx}] → {fname}")
                    ok = download_pdf(pdf_url, dest)

                    if ok:
                        size = dest.stat().st_size
                        log.info(f"        ✓ {fname} ({size:,} bytes)")
                        manifest.add(dl_key)
                        save_manifest(manifest)
                        total_downloaded += 1
                    else:
                        log.warning(f"        ✗ Failed: {link_text[:50]}")
                        total_failed += 1

                total_resolved += 1

            manifest.add(manifest_key)
            save_manifest(manifest)
            time.sleep(0.5)

    log.info(f"\n{'='*60}")
    log.info(f"DONE")
    log.info(f"  URL-only PDFs resolved : {total_resolved}")
    log.info(f"  PDFs downloaded        : {total_downloaded}")
    log.info(f"  Downloads failed       : {total_failed}")
    log.info("=" * 60)


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    run()
