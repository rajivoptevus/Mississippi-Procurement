"""
Mississippi Procurement — Database & Azure Blob Uploader
==========================================================
Reads all scraped data and:
1. Uploads all PDF documents to Azure Blob Storage
   → Container: rfp-attachments
   → Path: mississippi/{rfx_type}/{BidID}_{smart_number}/{filename}
2. Inserts/updates wyber_universal_rfps_expt_1 with all bid fields
3. Inserts/updates wyber_universal_rfp_docs_expt_1 with all document records
   (including blob_url from Azure)

REQUIREMENTS:
    pip install psycopg2-binary azure-storage-blob python-dotenv

HOW TO RUN:
    cd C:\Scraping\Mississippi-Procurement
    python db_uploader.py
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List

# ─────────────────────────── CONFIG ───────────────────────────
SCRAPED_DIR    = Path(r"C:\Scraping\Mississippi-Procurement\scraped_data")
ALL_BIDS_FILE  = SCRAPED_DIR / "all_bids_complete.json"

# Azure
AZURE_CONN_STR  = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=rfpsources;"
    "AccountKey="XXXXXXXX"
    "EndpointSuffix=core.windows.net"
)
AZURE_CONTAINER = "rfp-attachments"
BLOB_PREFIX     = "mississippi"   # all files go under mississippi/

# Database — read from config.py
import sys
sys.path.insert(0, str(Path(__file__).parent))
try:
    import config as _cfg
    DB_HOST     = _cfg.DB_HOST
    DB_PORT     = _cfg.DB_PORT
    DB_NAME     = _cfg.DB_NAME
    DB_USER     = _cfg.DB_USER
    DB_PASSWORD = _cfg.DB_PASSWORD
    DB_SSLMODE  = getattr(_cfg, "DB_SSLMODE", "require")
except ImportError:
    DB_HOST     = os.getenv("DB_HOST",     "localhost")
    DB_PORT     = os.getenv("DB_PORT",     "5432")
    DB_NAME     = os.getenv("DB_NAME",     "wyber")
    DB_USER     = os.getenv("DB_USER",     "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_SSLMODE  = "require"

SOURCE_NAME  = "Mississippi Procurement Portal"
STATE_CODE   = "MS"
BASE_URL     = "https://www.ms.gov/dfa/contract_bid_search/Bid?autoloadGrid=true"

# ─────────────────────────── LOGGING ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("db_uploader.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("db_uploader")


# ─────────────────────────── HELPERS ──────────────────────────
def sanitize_blob(name: str, max_len: int = 200) -> str:
    """Sanitize name for Azure Blob path."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name))
    name = re.sub(r'_+', '_', name).strip('_')
    return name[:max_len]


def parse_date(val: str) -> Optional[str]:
    """Parse 'YYYY-MM-DD HH:MM UTC' → 'YYYY-MM-DD'."""
    if not val:
        return None
    m = re.search(r'(\d{4}-\d{2}-\d{2})', val)
    return m.group(1) if m else None


def parse_datetime_iso(val: str) -> Optional[str]:
    """Parse 'YYYY-MM-DD HH:MM UTC' → ISO timestamp."""
    if not val:
        return None
    try:
        val_clean = val.replace(" UTC", "").strip()
        dt = datetime.strptime(val_clean, "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        return None


def map_notice_type(rfx_type: str) -> str:
    """Map Mississippi RFx type to standard notice_type."""
    mapping = {
        "Request for Proposal":  "RFP",
        "Invitation for Bid":    "IFB",
        "Req. for Information":  "RFI",
        "RFQ - Informal":        "RFQ",
        "RFQ - Formal":          "RFQ",
        "Negotiated Bid":        "Negotiated Bid",
        "MDA - RFx":             "MDA-RFx",
    }
    return mapping.get(rfx_type, rfx_type or "Unknown")


def map_notice_base_type(rfx_type: str) -> str:
    mapping = {
        "Request for Proposal":  "Active_Pursuit",
        "Invitation for Bid":    "Commodity_Low_Value",
        "Req. for Information":  "Pre_Sales_Intel",
        "RFQ - Informal":        "Active_Pursuit",
        "RFQ - Formal":          "Active_Pursuit",
        "Negotiated Bid":        "Active_Pursuit",
        "MDA - RFx":             "Commodity_Low_Value",
    }
    return mapping.get(rfx_type, "Active_Pursuit")


def get_mime_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return {
        ".pdf":  "application/pdf",
        ".doc":  "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xls":  "application/vnd.ms-excel",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".zip":  "application/zip",
        ".txt":  "text/plain",
    }.get(ext, "application/octet-stream")


# ─────────────────────────── AZURE BLOB ───────────────────────
_blob_service = None

def get_blob_service():
    global _blob_service
    if _blob_service:
        return _blob_service
    try:
        from azure.storage.blob import BlobServiceClient
        _blob_service = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
        # Ensure container exists
        try:
            _blob_service.create_container(AZURE_CONTAINER)
        except Exception:
            pass  # already exists
        log.info(f"✓ Azure Blob connected → {AZURE_CONTAINER}")
        return _blob_service
    except ImportError:
        log.error("azure-storage-blob not installed: pip install azure-storage-blob")
        return None
    except Exception as e:
        log.error(f"Azure connection failed: {e}")
        return None


def upload_to_blob(local_path: Path, blob_name: str) -> Optional[str]:
    """
    Upload a file to Azure Blob Storage.
    Returns the blob URL or None on failure.
    """
    svc = get_blob_service()
    if not svc:
        return None
    try:
        client = svc.get_blob_client(container=AZURE_CONTAINER, blob=blob_name)
        with open(local_path, "rb") as f:
            client.upload_blob(f, overwrite=True)
        url = client.url
        log.debug(f"  ☁ Uploaded: {blob_name.split('/')[-1][:50]} → {url[:60]}")
        return url
    except Exception as e:
        log.warning(f"  Azure upload failed {blob_name}: {e}")
        return None


# ─────────────────────────── DATABASE ─────────────────────────
_db_conn = None

def get_db_conn():
    global _db_conn
    if _db_conn:
        try:
            _db_conn.cursor().execute("SELECT 1")
            return _db_conn
        except Exception:
            _db_conn = None
    try:
        import psycopg2
        _db_conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASSWORD,
            sslmode=DB_SSLMODE
        )
        _db_conn.autocommit = False
        log.info(f"✓ Database connected → {DB_NAME}@{DB_HOST}")
        return _db_conn
    except Exception as e:
        log.error(f"Database connection failed: {e}")
        log.error("Set DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD env vars")
        return None


def upsert_rfp(conn, bid: Dict, global_notice_id: str):
    """Insert or update a record in wyber_universal_rfps_expt_1."""
    sql = """
    INSERT INTO wyber_universal_rfps_expt_1 (
        global_notice_id, notice_id, title, notice_type, notice_base_type,
        agency, description_text, source_url, ui_link, base_source_url,
        posted_date, response_deadline,
        contact_name, contact_email, contact_phone, contact_fax,
        solicitation_number, active, lifecycle_stage,
        state_code, source_name,
        source_bid_id, smart_number, rfx_number, rfx_status,
        rfx_type, advertised_datetime, submission_datetime, opening_datetime,
        pdf_url, bid_status, bid_opening_date,
        created_on, modified_on, is_deleted, files_processed, vectors_processed
    ) VALUES (
        %(global_notice_id)s, %(notice_id)s, %(title)s, %(notice_type)s, %(notice_base_type)s,
        %(agency)s, %(description_text)s, %(source_url)s, %(ui_link)s, %(base_source_url)s,
        %(posted_date)s, %(response_deadline)s,
        %(contact_name)s, %(contact_email)s, %(contact_phone)s, %(contact_fax)s,
        %(solicitation_number)s, %(active)s, %(lifecycle_stage)s,
        %(state_code)s, %(source_name)s,
        %(source_bid_id)s, %(smart_number)s, %(rfx_number)s, %(rfx_status)s,
        %(rfx_type)s, %(advertised_datetime)s, %(submission_datetime)s, %(opening_datetime)s,
        %(pdf_url)s, %(bid_status)s, %(bid_opening_date)s,
        NOW(), NOW(), FALSE, FALSE, FALSE
    )
    ON CONFLICT (global_notice_id) DO UPDATE SET
        title               = EXCLUDED.title,
        notice_type         = EXCLUDED.notice_type,
        agency              = EXCLUDED.agency,
        description_text    = EXCLUDED.description_text,
        contact_name        = EXCLUDED.contact_name,
        contact_email       = EXCLUDED.contact_email,
        contact_phone       = EXCLUDED.contact_phone,
        rfx_status          = EXCLUDED.rfx_status,
        bid_status          = EXCLUDED.bid_status,
        modified_on         = NOW()
    """

    rfx_type = bid.get("rfx_type", "")
    params = {
        "global_notice_id":   global_notice_id,
        "notice_id":          str(bid.get("bid_id", "")),
        "title":              bid.get("description", "") or "",
        "notice_type":        map_notice_type(rfx_type),
        "notice_base_type":   map_notice_base_type(rfx_type),
        "agency":             bid.get("agency", "") or "",
        "description_text":   bid.get("description", "") or "",
        "source_url":         bid.get("detail_url", ""),
        "ui_link":            bid.get("detail_url", ""),
        "base_source_url":    BASE_URL,
        "posted_date":        parse_date(bid.get("advertised_date", "")),
        "response_deadline":  parse_date(bid.get("submission_date", "")),
        "contact_name":       bid.get("contact_name", "") or "",
        "contact_email":      bid.get("contact_email", "") or "",
        "contact_phone":      bid.get("contact_phone", "") or "",
        "contact_fax":        bid.get("contact_fax", "") or "",
        "solicitation_number": bid.get("smart_number", "") or "",
        "active":             bid.get("rfx_status", "").lower() == "open",
        "lifecycle_stage":    bid.get("rfx_status", "") or "",
        "state_code":         STATE_CODE,
        "source_name":        SOURCE_NAME,
        "source_bid_id":      bid.get("bid_id"),
        "smart_number":       bid.get("smart_number", "") or "",
        "rfx_number":         bid.get("rfx_number", "") or "",
        "rfx_status":         bid.get("rfx_status", "") or "",
        "rfx_type":           rfx_type or "",
        "advertised_datetime": parse_datetime_iso(bid.get("advertised_date", "")),
        "submission_datetime": parse_datetime_iso(bid.get("submission_date", "")),
        "opening_datetime":    parse_datetime_iso(bid.get("opening_date", "")),
        "pdf_url":            bid.get("pdf_url", "") or "",
        "bid_status":         bid.get("rfx_status", "") or "",
        "bid_opening_date":   parse_date(bid.get("opening_date", "")),
    }

    with conn.cursor() as cur:
        cur.execute(sql, params)


def upsert_doc(conn, doc_record: Dict):
    """Insert or update a record in wyber_universal_rfp_docs_expt_1."""
    # First try to update existing record by attachment_id + notice_id
    update_sql = """
    UPDATE wyber_universal_rfp_docs_expt_1
    SET blob_url    = %(blob_url)s,
        file_path   = %(file_path)s,
        file_name   = %(file_name)s,
        source_name = %(source_name)s,
        state_code  = %(state_code)s,
        mime_type   = %(mime_type)s,
        file_size_bytes = %(file_size_bytes)s,
        document_type   = %(document_type)s,
        updated_at  = NOW()
    WHERE global_notice_id = %(global_notice_id)s
      AND attachment_id    = %(attachment_id)s
    """

    insert_sql = """
    INSERT INTO wyber_universal_rfp_docs_expt_1 (
        notice_id, source_name, state_code,
        file_name, file_path, blob_url, source_url, source_file_url,
        file_size_bytes, mime_type,
        is_text_extracted, is_vector, is_deleted, is_analysed,
        requires_login, document_type,
        global_notice_id, attachment_id, attachment_description,
        created_at, updated_at
    ) VALUES (
        %(notice_id)s, %(source_name)s, %(state_code)s,
        %(file_name)s, %(file_path)s, %(blob_url)s, %(source_url)s, %(source_file_url)s,
        %(file_size_bytes)s, %(mime_type)s,
        FALSE, FALSE, FALSE, FALSE,
        FALSE, %(document_type)s,
        %(global_notice_id)s, %(attachment_id)s, %(attachment_description)s,
        NOW(), NOW()
    )
    ON CONFLICT DO NOTHING
    """
    with conn.cursor() as cur:
        cur.execute(update_sql, doc_record)
        if cur.rowcount == 0:
            # No existing record — insert new
            cur.execute(insert_sql, doc_record)


# ─────────────────────────── MAIN ─────────────────────────────
def run():
    log.info("=" * 60)
    log.info("Mississippi Procurement — DB & Azure Uploader")
    log.info("=" * 60)

    # Load all bids
    if not ALL_BIDS_FILE.exists():
        log.error(f"Not found: {ALL_BIDS_FILE}")
        return

    bids = json.loads(ALL_BIDS_FILE.read_text(encoding="utf-8"))
    log.info(f"Loaded {len(bids)} bids from {ALL_BIDS_FILE.name}")

    # Build lookup: bid_id → bid record
    bid_lookup: Dict[int, Dict] = {b["bid_id"]: b for b in bids}

    # Connect to Azure
    blob_svc = get_blob_service()
    if not blob_svc:
        log.warning("Azure not available — will skip blob uploads")

    # Connect to DB
    conn = get_db_conn()
    if not conn:
        log.warning("Database not available — will skip DB inserts")

    total_rfps_upserted = 0
    total_docs_upserted = 0
    total_blobs_uploaded = 0
    total_failed = 0

    # Process each BidID folder
    bid_dirs = sorted(SCRAPED_DIR.glob("BidID_*"))
    log.info(f"\nProcessing {len(bid_dirs)} BidID folders...")

    for bid_dir in bid_dirs:
        bid_id_str = bid_dir.name.replace("BidID_", "")
        try:
            bid_id = int(bid_id_str)
        except ValueError:
            continue

        bid = bid_lookup.get(bid_id)
        if not bid:
            # Try loading from details.json
            details_file = bid_dir / "details.json"
            if details_file.exists():
                try:
                    bid = json.loads(details_file.read_text(encoding="utf-8"))
                    bid["bid_id"] = bid_id
                except Exception:
                    pass

        if not bid:
            log.warning(f"  No bid data for {bid_dir.name} — skipping")
            continue

        # Generate stable global_notice_id from bid_id
        # Use UUID5 with namespace for deterministic IDs
        global_notice_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"ms.gov/bid/{bid_id}"
        ))

        rfx_type    = bid.get("rfx_type", "Unknown") or "Unknown"
        smart_num   = bid.get("smart_number", "") or bid_id_str
        notice_type = map_notice_type(rfx_type)

        log.info(f"\n  [{bid_id}] {smart_num} — {rfx_type}")

        # ── 1. Upsert RFP record ──────────────────────────────
        if conn:
            try:
                upsert_rfp(conn, bid, global_notice_id)
                conn.commit()
                total_rfps_upserted += 1
                log.info(f"    ✓ RFP upserted: {global_notice_id[:20]}...")
            except Exception as e:
                conn.rollback()
                log.error(f"    ✗ RFP upsert failed: {e}")

        # ── 2. Process each PDF file ──────────────────────────
        pdf_files = [f for f in bid_dir.iterdir()
                     if f.suffix.lower() == ".pdf"
                     and f.name != "details.json"]

        # Also get attachment metadata from bid record
        att_by_idx: Dict[int, Dict] = {}
        for att in bid.get("attachments", []):
            att_by_idx[att.get("attachment_id")] = att

        for pdf_file in sorted(pdf_files):
            # Build blob path:
            # mississippi/{rfx_type_clean}/{BidID}_{smart_number}/{filename}
            type_clean  = sanitize_blob(rfx_type.replace(" ", "_").replace("-", "_"))
            folder_name = sanitize_blob(f"{bid_id}_{smart_num}")
            blob_name   = f"{BLOB_PREFIX}/{type_clean}/{folder_name}/{pdf_file.name}"

            # Upload to Azure Blob
            blob_url = None
            if blob_svc and pdf_file.exists():
                blob_url = upload_to_blob(pdf_file, blob_name)
                if blob_url:
                    total_blobs_uploaded += 1
                    log.info(f"    ☁ {pdf_file.name[:50]} → blob uploaded")
                else:
                    total_failed += 1

            # Find matching attachment metadata
            # Match by sequence number in filename (001_, 002_, etc.)
            seq_match = re.match(r'^(\d{3})_', pdf_file.name)
            seq = int(seq_match.group(1)) if seq_match else 0

            # Find attachment by sequence
            att_list = bid.get("attachments", [])
            att_meta = att_list[seq - 1] if 0 < seq <= len(att_list) else {}

            att_id   = str(att_meta.get("attachment_id", "")) if att_meta else ""
            att_desc = att_meta.get("description", pdf_file.stem) if att_meta else pdf_file.stem
            att_url  = att_meta.get("url", "") if att_meta else ""

            file_size = pdf_file.stat().st_size if pdf_file.exists() else None

            doc_record = {
                "notice_id":             str(bid_id),
                "source_name":           SOURCE_NAME,
                "state_code":            STATE_CODE,
                "file_name":             pdf_file.name,
                "file_path":             blob_name,
                "blob_url":              blob_url or "",
                "source_url":            att_url or bid.get("detail_url", ""),
                "source_file_url":       att_url or "",
                "file_size_bytes":       file_size,
                "mime_type":             get_mime_type(pdf_file.name),
                "document_type":         notice_type,
                "global_notice_id":      global_notice_id,
                "attachment_id":         att_id,
                "attachment_description": att_desc,
            }

            # ── 3. Upsert doc record ──────────────────────────
            if conn:
                try:
                    upsert_doc(conn, doc_record)
                    conn.commit()
                    total_docs_upserted += 1
                    log.info(f"    ✓ Doc upserted: {pdf_file.name[:50]}")
                except Exception as e:
                    conn.rollback()
                    log.error(f"    ✗ Doc upsert failed {pdf_file.name}: {e}")

    # ── Final summary ──────────────────────────────────────────
    if conn:
        try:
            conn.close()
        except Exception:
            pass

    log.info(f"\n{'='*60}")
    log.info(f"DONE")
    log.info(f"  RFPs upserted    : {total_rfps_upserted}")
    log.info(f"  Docs upserted    : {total_docs_upserted}")
    log.info(f"  Blobs uploaded   : {total_blobs_uploaded}")
    log.info(f"  Upload failures  : {total_failed}")
    log.info("=" * 60)

    # Save upload report
    report = {
        "timestamp":          datetime.now(timezone.utc).isoformat(),
        "rfps_upserted":      total_rfps_upserted,
        "docs_upserted":      total_docs_upserted,
        "blobs_uploaded":     total_blobs_uploaded,
        "upload_failures":    total_failed,
    }
    Path("upload_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    log.info("Report saved: upload_report.json")


if __name__ == "__main__":
    run()
