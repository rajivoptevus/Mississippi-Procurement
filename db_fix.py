"""
Mississippi Procurement — DB Fix & Full Re-uploader
=====================================================
1. Fixes global_notice_id to use pattern: ms_gov_dfa_contract_bid_search_{bid_id}
2. Deletes UUID-style notice_id rows (bad inserts from old code)
3. Uploads ALL file types (.pdf, .docx, .xlsx, .doc, .zip) to Azure
4. Upserts all RFP + doc records with correct IDs and Azure blob_urls
5. Fixes any blob_url still pointing to SAP instead of Azure

HOW TO RUN:
    cd C:\Scraping\Mississippi-Procurement
    python db_fix.py
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
SCRAPED_DIR   = Path(r"C:\Scraping\Mississippi-Procurement\scraped_data")
ALL_BIDS_FILE = SCRAPED_DIR / "all_bids_complete.json"

AZURE_CONN_STR  = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=rfpsources;"
    "AccountKey=XXXXXXXXX"
    "EndpointSuffix=core.windows.net"
)
AZURE_CONTAINER = "rfp-attachments"
BLOB_PREFIX     = "mississippi"

import sys
sys.path.insert(0, str(Path(__file__).parent))
import config as _cfg
DB_HOST     = _cfg.DB_HOST
DB_PORT     = _cfg.DB_PORT
DB_NAME     = _cfg.DB_NAME
DB_USER     = _cfg.DB_USER
DB_PASSWORD = _cfg.DB_PASSWORD
DB_SSLMODE  = getattr(_cfg, "DB_SSLMODE", "require")

SOURCE_NAME = "Mississippi Procurement Portal"
SOURCE_URL  = "ms.gov/dfa/contract_bid_search"
STATE_CODE  = "MS"
BASE_URL    = "https://www.ms.gov/dfa/contract_bid_search/Bid?autoloadGrid=true"

# Pre-computed source key for global_notice_id
# normalize_key("ms.gov/dfa/contract_bid_search") → "ms_gov_dfa_contract_bid_search"
SOURCE_KEY  = "ms_gov_dfa_contract_bid_search"

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".zip", ".txt"}

# ─────────────────────────── LOGGING ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("db_fix.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("db_fix")


# ─────────────────────────── ID HELPERS ───────────────────────
def normalize_key(value: str) -> str:
    """Normalize a string for use in global_notice_id."""
    value = str(value or "").lower().strip()
    value = re.sub(r'^https?://(www\.)?', '', value)
    value = value.split('/')[0]
    value = re.sub(r'[^a-z0-9]+', '_', value)
    value = re.sub(r'_+', '_', value)
    return value.strip('_')


def make_global_notice_id(bid_id: int) -> str:
    """
    Build global_notice_id from source URL + bid_id.
    Pattern: ms_gov_dfa_contract_bid_search_{bid_id}
    Matches team convention: normalize_key(source_url) + "_" + notice_id
    """
    return f"{SOURCE_KEY}_{bid_id}"


def is_uuid(val: str) -> bool:
    """Return True if val looks like a UUID (36-char hex with dashes)."""
    return bool(re.match(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$',
        str(val or "").lower()
    ))


# ─────────────────────────── MISC HELPERS ─────────────────────
def sanitize_blob(name: str, max_len: int = 200) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(name))
    name = re.sub(r'_+', '_', name).strip('_')
    return name[:max_len]


def parse_date(val: str) -> Optional[str]:
    if not val:
        return None
    m = re.search(r'(\d{4}-\d{2}-\d{2})', val)
    return m.group(1) if m else None


def parse_datetime_iso(val: str) -> Optional[str]:
    if not val:
        return None
    try:
        val_clean = val.replace(" UTC", "").strip()
        dt = datetime.strptime(val_clean, "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=timezone.utc).isoformat()
    except Exception:
        return None


def map_notice_type(rfx_type: str) -> str:
    return {
        "Request for Proposal": "RFP",
        "Invitation for Bid":   "IFB",
        "Req. for Information": "RFI",
        "RFQ - Informal":       "RFQ",
        "RFQ - Formal":         "RFQ",
        "Negotiated Bid":       "Negotiated Bid",
        "MDA - RFx":            "MDA-RFx",
    }.get(rfx_type, rfx_type or "Unknown")


def map_notice_base_type(rfx_type: str) -> str:
    return {
        "Request for Proposal": "Active_Pursuit",
        "Invitation for Bid":   "Commodity_Low_Value",
        "Req. for Information": "Pre_Sales_Intel",
        "RFQ - Informal":       "Active_Pursuit",
        "RFQ - Formal":         "Active_Pursuit",
        "Negotiated Bid":       "Active_Pursuit",
        "MDA - RFx":            "Commodity_Low_Value",
    }.get(rfx_type, "Active_Pursuit")


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
        try:
            _blob_service.create_container(AZURE_CONTAINER)
        except Exception:
            pass
        log.info(f"✓ Azure Blob connected → {AZURE_CONTAINER}")
        return _blob_service
    except ImportError:
        log.error("azure-storage-blob not installed")
        return None
    except Exception as e:
        log.error(f"Azure connection failed: {e}")
        return None


def upload_to_blob(local_path: Path, blob_name: str) -> Optional[str]:
    svc = get_blob_service()
    if not svc:
        return None
    try:
        client = svc.get_blob_client(container=AZURE_CONTAINER, blob=blob_name)
        with open(local_path, "rb") as f:
            client.upload_blob(f, overwrite=True)
        return client.url
    except Exception as e:
        log.warning(f"  Azure upload failed {blob_name}: {e}")
        return None


def blob_exists(blob_name: str) -> Optional[str]:
    """Return blob URL if it already exists, else None."""
    svc = get_blob_service()
    if not svc:
        return None
    try:
        client = svc.get_blob_client(container=AZURE_CONTAINER, blob=blob_name)
        props = client.get_blob_properties()
        return client.url
    except Exception:
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
        return None


def delete_uuid_rows(conn):
    """
    Delete rows from both tables where notice_id or global_notice_id is a UUID.
    These are bad rows inserted by the old code that used uuid5() for global_notice_id
    and stored UUID strings as notice_id.
    """
    uuid_pattern = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

    with conn.cursor() as cur:
        # Count first
        cur.execute(
            "SELECT COUNT(*) FROM wyber_universal_rfps_expt_1 "
            "WHERE source_name = %s AND ("
            "  notice_id ~ %s OR global_notice_id ~ %s"
            ")",
            (SOURCE_NAME, uuid_pattern, uuid_pattern)
        )
        rfp_count = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1 "
            "WHERE source_name = %s AND ("
            "  notice_id ~ %s OR global_notice_id ~ %s"
            ")",
            (SOURCE_NAME, uuid_pattern, uuid_pattern)
        )
        doc_count = cur.fetchone()[0]

        log.info(f"  UUID rows to delete — RFPs: {rfp_count}, Docs: {doc_count}")

        if rfp_count > 0:
            cur.execute(
                "DELETE FROM wyber_universal_rfps_expt_1 "
                "WHERE source_name = %s AND ("
                "  notice_id ~ %s OR global_notice_id ~ %s"
                ")",
                (SOURCE_NAME, uuid_pattern, uuid_pattern)
            )
            log.info(f"  ✓ Deleted {cur.rowcount} UUID RFP rows")

        if doc_count > 0:
            cur.execute(
                "DELETE FROM wyber_universal_rfp_docs_expt_1 "
                "WHERE source_name = %s AND ("
                "  notice_id ~ %s OR global_notice_id ~ %s"
                ")",
                (SOURCE_NAME, uuid_pattern, uuid_pattern)
            )
            log.info(f"  ✓ Deleted {cur.rowcount} UUID Doc rows")

    conn.commit()


def upsert_rfp(conn, bid: Dict, global_notice_id: str):
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
        notice_id           = EXCLUDED.notice_id,
        title               = EXCLUDED.title,
        notice_type         = EXCLUDED.notice_type,
        agency              = EXCLUDED.agency,
        description_text    = EXCLUDED.description_text,
        contact_name        = EXCLUDED.contact_name,
        contact_email       = EXCLUDED.contact_email,
        contact_phone       = EXCLUDED.contact_phone,
        rfx_status          = EXCLUDED.rfx_status,
        bid_status          = EXCLUDED.bid_status,
        source_bid_id       = EXCLUDED.source_bid_id,
        smart_number        = EXCLUDED.smart_number,
        rfx_number          = EXCLUDED.rfx_number,
        modified_on         = NOW()
    """
    rfx_type = bid.get("rfx_type", "")
    params = {
        "global_notice_id":    global_notice_id,
        "notice_id":           str(bid.get("bid_id", "")),
        "title":               bid.get("description", "") or "",
        "notice_type":         map_notice_type(rfx_type),
        "notice_base_type":    map_notice_base_type(rfx_type),
        "agency":              bid.get("agency", "") or "",
        "description_text":    bid.get("description", "") or "",
        "source_url":          bid.get("detail_url", ""),
        "ui_link":             bid.get("detail_url", ""),
        "base_source_url":     BASE_URL,
        "posted_date":         parse_date(bid.get("advertised_date", "")),
        "response_deadline":   parse_date(bid.get("submission_date", "")),
        "contact_name":        bid.get("contact_name", "") or "",
        "contact_email":       bid.get("contact_email", "") or "",
        "contact_phone":       bid.get("contact_phone", "") or "",
        "contact_fax":         bid.get("contact_fax", "") or "",
        "solicitation_number": bid.get("smart_number", "") or "",
        "active":              bid.get("rfx_status", "").lower() == "open",
        "lifecycle_stage":     bid.get("rfx_status", "") or "",
        "state_code":          STATE_CODE,
        "source_name":         SOURCE_NAME,
        "source_bid_id":       bid.get("bid_id"),
        "smart_number":        bid.get("smart_number", "") or "",
        "rfx_number":          bid.get("rfx_number", "") or "",
        "rfx_status":          bid.get("rfx_status", "") or "",
        "rfx_type":            rfx_type or "",
        "advertised_datetime": parse_datetime_iso(bid.get("advertised_date", "")),
        "submission_datetime": parse_datetime_iso(bid.get("submission_date", "")),
        "opening_datetime":    parse_datetime_iso(bid.get("opening_date", "")),
        "pdf_url":             bid.get("pdf_url", "") or "",
        "bid_status":          bid.get("rfx_status", "") or "",
        "bid_opening_date":    parse_date(bid.get("opening_date", "")),
    }
    with conn.cursor() as cur:
        cur.execute(sql, params)


def upsert_doc(conn, doc: Dict):
    """
    Upsert a document record keyed on file_path (blob path), which is unique per file.
    The table has a unique constraint on (notice_id, source_url) which would block
    multiple docs per bid — so we use UPDATE by file_path + INSERT if not exists.
    """
    # Try update first (match on the blob file_path which is unique per file)
    update_sql = """
    UPDATE wyber_universal_rfp_docs_expt_1
    SET blob_url               = %(blob_url)s,
        file_name              = %(file_name)s,
        file_size_bytes        = %(file_size_bytes)s,
        mime_type              = %(mime_type)s,
        source_file_url        = %(source_file_url)s,
        attachment_description = %(attachment_description)s,
        global_notice_id       = %(global_notice_id)s,
        attachment_id          = %(attachment_id)s,
        document_type          = %(document_type)s,
        updated_at             = NOW()
    WHERE file_path   = %(file_path)s
      AND source_name = %(source_name)s
    """
    # Insert ignoring the (notice_id, source_url) constraint by using the blob URL
    # as source_url so each file gets a unique (notice_id, source_url) combo
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
        %(file_name)s, %(file_path)s, %(blob_url)s,
        %(blob_url)s,
        %(source_file_url)s,
        %(file_size_bytes)s, %(mime_type)s,
        FALSE, FALSE, FALSE, FALSE,
        FALSE, %(document_type)s,
        %(global_notice_id)s, %(attachment_id)s, %(attachment_description)s,
        NOW(), NOW()
    )
    ON CONFLICT DO NOTHING
    """
    with conn.cursor() as cur:
        cur.execute(update_sql, doc)
        if cur.rowcount == 0:
            cur.execute(insert_sql, doc)


# ─────────────────────────── MAIN ─────────────────────────────
def run():
    log.info("=" * 65)
    log.info("Mississippi Procurement — DB Fix & Full Re-uploader")
    log.info("=" * 65)

    # ── Load all bids ──────────────────────────────────────────
    if not ALL_BIDS_FILE.exists():
        log.error(f"Not found: {ALL_BIDS_FILE}")
        return
    bids = json.loads(ALL_BIDS_FILE.read_text(encoding="utf-8"))
    bid_lookup: Dict[int, Dict] = {b["bid_id"]: b for b in bids}
    log.info(f"Loaded {len(bids)} bids")

    # ── Connect ────────────────────────────────────────────────
    blob_svc = get_blob_service()
    conn     = get_db_conn()
    if not conn:
        log.error("Cannot connect to DB — aborting")
        return

    # ── Step 1: Delete UUID garbage rows ──────────────────────
    log.info("\n── Step 1: Deleting UUID-style rows ──")
    delete_uuid_rows(conn)

    # ── Step 1b: Fix wrong global_notice_id pattern ───────────
    log.info("\n── Step 1b: Fixing wrong global_notice_id (ms_gov_X → ms_gov_dfa_contract_bid_search_X) ──")
    try:
        with conn.cursor() as cur:
            # Fix RFPs table
            cur.execute("""
                UPDATE wyber_universal_rfps_expt_1
                SET global_notice_id = REPLACE(global_notice_id, 'ms_gov_', 'ms_gov_dfa_contract_bid_search_'),
                    modified_on = NOW()
                WHERE source_name = %s
                  AND global_notice_id LIKE 'ms_gov_%%'
                  AND global_notice_id NOT LIKE 'ms_gov_dfa%%'
            """, (SOURCE_NAME,))
            log.info(f"  ✓ Fixed {cur.rowcount} RFP global_notice_ids")

            # Fix docs table
            cur.execute("""
                UPDATE wyber_universal_rfp_docs_expt_1
                SET global_notice_id = REPLACE(global_notice_id, 'ms_gov_', 'ms_gov_dfa_contract_bid_search_'),
                    updated_at = NOW()
                WHERE source_name = %s
                  AND global_notice_id LIKE 'ms_gov_%%'
                  AND global_notice_id NOT LIKE 'ms_gov_dfa%%'
            """, (SOURCE_NAME,))
            log.info(f"  ✓ Fixed {cur.rowcount} Doc global_notice_ids")
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"  global_notice_id fix failed: {e}")

    # ── Step 2: Process each BidID folder ─────────────────────
    log.info("\n── Step 2: Upserting all RFPs and documents ──")

    stats = {
        "rfps_upserted": 0,
        "docs_upserted": 0,
        "blobs_uploaded": 0,
        "blobs_reused": 0,
        "upload_failures": 0,
        "skipped_folders": 0,
    }

    bid_dirs = sorted(SCRAPED_DIR.glob("BidID_*"))
    log.info(f"Processing {len(bid_dirs)} BidID folders...")

    for bid_dir in bid_dirs:
        bid_id_str = bid_dir.name.replace("BidID_", "")
        try:
            bid_id = int(bid_id_str)
        except ValueError:
            stats["skipped_folders"] += 1
            continue

        # Get bid metadata
        bid = bid_lookup.get(bid_id)
        if not bid:
            details_file = bid_dir / "details.json"
            if details_file.exists():
                try:
                    bid = json.loads(details_file.read_text(encoding="utf-8"))
                    bid["bid_id"] = bid_id
                except Exception:
                    pass
        if not bid:
            log.warning(f"  No bid data for {bid_dir.name} — skipping")
            stats["skipped_folders"] += 1
            continue

        rfx_type         = bid.get("rfx_type", "Unknown") or "Unknown"
        smart_num        = bid.get("smart_number", "") or bid_id_str
        notice_type      = map_notice_type(rfx_type)
        global_notice_id = make_global_notice_id(bid_id)

        log.info(f"\n  [{bid_id}] {smart_num} | {rfx_type} | gid={global_notice_id}")

        # ── Upsert RFP ────────────────────────────────────────
        try:
            upsert_rfp(conn, bid, global_notice_id)
            conn.commit()
            stats["rfps_upserted"] += 1
        except Exception as e:
            conn.rollback()
            log.error(f"    ✗ RFP upsert failed: {e}")

        # ── Build attachment metadata index ───────────────────
        # Index by sequence number (1-based) from the attachments list
        att_list = bid.get("attachments", [])
        att_by_seq: Dict[int, Dict] = {}
        for i, att in enumerate(att_list, start=1):
            att_by_seq[i] = att

        # ── Process all supported files ───────────────────────
        doc_files = sorted([
            f for f in bid_dir.iterdir()
            if f.suffix.lower() in SUPPORTED_EXTENSIONS
            and f.name != "details.json"
        ])

        for doc_file in doc_files:
            # Build blob path
            type_clean  = sanitize_blob(rfx_type.replace(" ", "_").replace("-", "_"))
            folder_name = sanitize_blob(f"{bid_id}_{smart_num}")
            blob_name   = f"{BLOB_PREFIX}/{type_clean}/{folder_name}/{doc_file.name}"

            # Upload (or reuse if already uploaded)
            blob_url = blob_exists(blob_name)
            if blob_url:
                stats["blobs_reused"] += 1
                log.debug(f"    ↩ Reused: {doc_file.name[:50]}")
            elif blob_svc:
                blob_url = upload_to_blob(doc_file, blob_name)
                if blob_url:
                    stats["blobs_uploaded"] += 1
                    log.info(f"    ☁ Uploaded: {doc_file.name[:50]}")
                else:
                    stats["upload_failures"] += 1
                    log.warning(f"    ✗ Upload failed: {doc_file.name[:50]}")

            # Match attachment metadata by sequence number in filename
            seq_match = re.match(r'^(\d{3})_', doc_file.name)
            seq = int(seq_match.group(1)) if seq_match else 0
            att_meta = att_by_seq.get(seq, {})

            att_id   = str(att_meta.get("attachment_id", f"seq_{seq}_{bid_id}"))
            att_desc = att_meta.get("description", doc_file.stem)
            att_url  = att_meta.get("url", "")

            file_size = doc_file.stat().st_size if doc_file.exists() else None

            doc_record = {
                "notice_id":              str(bid_id),
                "source_name":            SOURCE_NAME,
                "state_code":             STATE_CODE,
                "file_name":              doc_file.name,
                "file_path":              blob_name,
                "blob_url":               blob_url or "",
                "source_url":             bid.get("detail_url", ""),
                "source_file_url":        att_url or "",
                "file_size_bytes":        file_size,
                "mime_type":              get_mime_type(doc_file.name),
                "document_type":          notice_type,
                "global_notice_id":       global_notice_id,
                "attachment_id":          att_id,
                "attachment_description": att_desc,
            }

            try:
                upsert_doc(conn, doc_record)
                conn.commit()
                stats["docs_upserted"] += 1
                log.info(f"    ✓ Doc: {doc_file.name[:50]}")
            except Exception as e:
                conn.rollback()
                log.error(f"    ✗ Doc upsert failed {doc_file.name}: {e}")

    # ── Step 3: Fix any remaining SAP blob_urls ───────────────
    log.info("\n── Step 3: Fixing SAP blob_urls ──")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1 "
                "WHERE source_name = %s AND blob_url LIKE '%%SRM.MAGIC%%'",
                (SOURCE_NAME,)
            )
            sap_count = cur.fetchone()[0]
            log.info(f"  SAP blob_url rows remaining: {sap_count}")
            if sap_count > 0:
                cur.execute(
                    "UPDATE wyber_universal_rfp_docs_expt_1 "
                    "SET blob_url = '', updated_at = NOW() "
                    "WHERE source_name = %s AND blob_url LIKE '%%SRM.MAGIC%%'",
                    (SOURCE_NAME,)
                )
                log.info(f"  ✓ Cleared {cur.rowcount} SAP blob_urls (will be re-uploaded on next run)")
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error(f"  SAP fix failed: {e}")

    # ── Step 4: Check for duplicates ──────────────────────────
    log.info("\n── Step 4: Checking for duplicates ──")
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT global_notice_id, COUNT(*) as cnt "
                "FROM wyber_universal_rfps_expt_1 "
                "WHERE source_name = %s "
                "GROUP BY global_notice_id HAVING COUNT(*) > 1",
                (SOURCE_NAME,)
            )
            dup_rfps = cur.fetchall()
            if dup_rfps:
                log.warning(f"  Duplicate RFP global_notice_ids: {len(dup_rfps)}")
                for row in dup_rfps:
                    log.warning(f"    {row[0]}: {row[1]} copies")
            else:
                log.info("  ✓ No duplicate RFPs")

            cur.execute(
                "SELECT global_notice_id, attachment_id, COUNT(*) as cnt "
                "FROM wyber_universal_rfp_docs_expt_1 "
                "WHERE source_name = %s "
                "GROUP BY global_notice_id, attachment_id HAVING COUNT(*) > 1",
                (SOURCE_NAME,)
            )
            dup_docs = cur.fetchall()
            if dup_docs:
                log.warning(f"  Duplicate Doc (gid+att_id) combos: {len(dup_docs)}")
                for row in dup_docs[:10]:
                    log.warning(f"    gid={row[0]} att={row[1]}: {row[2]} copies")
            else:
                log.info("  ✓ No duplicate Docs")
    except Exception as e:
        log.error(f"  Duplicate check failed: {e}")

    # ── Final summary ──────────────────────────────────────────
    try:
        conn.close()
    except Exception:
        pass

    log.info(f"\n{'='*65}")
    log.info("DONE")
    log.info(f"  RFPs upserted    : {stats['rfps_upserted']}")
    log.info(f"  Docs upserted    : {stats['docs_upserted']}")
    log.info(f"  Blobs uploaded   : {stats['blobs_uploaded']}")
    log.info(f"  Blobs reused     : {stats['blobs_reused']}")
    log.info(f"  Upload failures  : {stats['upload_failures']}")
    log.info(f"  Skipped folders  : {stats['skipped_folders']}")
    log.info("=" * 65)

    report = {
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "global_notice_id_pattern": "ms_gov_dfa_contract_bid_search_{bid_id}",
        **stats,
    }
    Path("upload_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("Report saved: upload_report.json")


if __name__ == "__main__":
    run()
