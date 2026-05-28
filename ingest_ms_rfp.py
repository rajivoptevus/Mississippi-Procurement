#!/usr/bin/env python3
"""
Ingest Mississippi RFP JSON data into wyber_universal_rfps_expt_1 and wyber_universal_rfp_docs_expt_1.
Usage: python ingest_ms_rfp.py <path_to_json>
"""

import json
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from zoneinfo import ZoneInfo

import psycopg2
import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Database connection string
DSN = (
    f"dbname={config.DB_NAME} user={config.DB_USER} password={config.DB_PASSWORD} "
    f"host={config.DB_HOST} port={config.DB_PORT} sslmode={config.DB_SSLMODE}"
)


def parse_utc_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parse UTC datetime strings like '2026-05-01 05:00 UTC' or ISO format."""
    if not dt_str:
        return None
    dt_str = dt_str.strip()
    try:
        if dt_str.endswith('UTC'):
            dt_str = dt_str.replace('UTC', '').strip()
            naive = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
            return naive.replace(tzinfo=ZoneInfo("UTC"))
        else:
            return datetime.fromisoformat(dt_str)
    except Exception as e:
        logger.warning(f"Could not parse datetime '{dt_str}': {e}")
        return None


def upsert_rfp(conn, record: Dict[str, Any]) -> str:
    """Insert or update the main RFP record, return notice_id."""
    cur = conn.cursor()
    source_url = record.get('detail_url')
    if not source_url:
        raise ValueError("Missing detail_url in JSON record")

    # Check if record already exists
    cur.execute("SELECT notice_id FROM wyber_universal_rfps_expt_1 WHERE source_url = %s", (source_url,))
    row = cur.fetchone()

    advertised_dt = parse_utc_datetime(record.get('advertised_date'))
    submission_dt = parse_utc_datetime(record.get('submission_date'))
    opening_dt = parse_utc_datetime(record.get('opening_date'))

    if row:
        notice_id = row[0]
        logger.info(f"Updating existing RFP notice_id={notice_id}")
        cur.execute("""
            UPDATE wyber_universal_rfps_expt_1
            SET source_bid_id = %s,
                smart_number = %s,
                rfx_number = %s,
                rfx_status = %s,
                rfx_type = %s,
                major_procurement_category = %s,
                sub_procurement_category = %s,
                advertised_datetime = %s,
                submission_datetime = %s,
                opening_datetime = %s,
                pdf_url = %s,
                modified_on = NOW()
            WHERE notice_id = %s
        """, (
            record.get('bid_id'),
            record.get('smart_number'),
            record.get('rfx_number'),
            record.get('rfx_status'),
            record.get('rfx_type'),
            record.get('major_procurement_category'),
            record.get('sub_procurement_category'),
            advertised_dt,
            submission_dt,
            opening_dt,
            record.get('pdf_url'),
            notice_id
        ))
    else:
        notice_id = str(uuid.uuid4())
        logger.info(f"Inserting new RFP with notice_id={notice_id}")
        cur.execute("""
            INSERT INTO wyber_universal_rfps_expt_1 (
                notice_id, source_url, title, description_text, notice_type,
                source_bid_id, smart_number, rfx_number, rfx_status,
                major_procurement_category, sub_procurement_category,
                advertised_datetime, submission_datetime, opening_datetime,
                pdf_url, global_notice_id, created_on, modified_on
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
        """, (
            notice_id,
            source_url,
            record.get('title', ''),
            record.get('description', ''),
            record.get('rfx_type'),
            record.get('bid_id'),
            record.get('smart_number'),
            record.get('rfx_number'),
            record.get('rfx_status'),
            record.get('major_procurement_category'),
            record.get('sub_procurement_category'),
            advertised_dt,
            submission_dt,
            opening_dt,
            record.get('pdf_url'),
            notice_id   # global_notice_id = same as notice_id
        ))
    conn.commit()
    cur.close()
    return notice_id


def upsert_attachments(conn, notice_id: str, attachments: List[Dict[str, Any]]):
    """Insert or update attachments using conflict on (notice_id, attachment_id)."""
    if not attachments:
        return
    cur = conn.cursor()
    for att in attachments:
        attachment_id = str(att.get('attachment_id'))
        description = att.get('description', '')
        blob_url = att.get('url')
        if not blob_url:
            continue

        # Simple document type classification
        doc_type = None
        desc_upper = description.upper()
        if 'RFP' in desc_upper:
            doc_type = 'Request_for_Proposal'
        elif 'INVITATION' in desc_upper:
            doc_type = 'Invitation_for_Bid'
        elif 'RFQ' in desc_upper:
            doc_type = 'RFQ_Formal' if 'FORMAL' in desc_upper else 'RFQ_Informal'

        # Use the unique constraint on (notice_id, attachment_id)
        cur.execute("""
            INSERT INTO wyber_universal_rfp_docs_expt_1 (
                notice_id, blob_url, attachment_id, attachment_description, document_type,
                source_url, file_name, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON CONFLICT (notice_id, attachment_id) DO UPDATE
            SET blob_url = EXCLUDED.blob_url,
                attachment_description = EXCLUDED.attachment_description,
                document_type = COALESCE(EXCLUDED.document_type, wyber_universal_rfp_docs_expt_1.document_type),
                updated_at = NOW()
        """, (
            notice_id,
            blob_url,
            attachment_id,
            description,
            doc_type,
            blob_url,
            description[:255] or f"attachment_{attachment_id}"
        ))
    conn.commit()
    cur.close()


def main(input_json_path: str):
    with open(input_json_path, 'r', encoding='utf-8') as f:
        records = json.load(f)
        if isinstance(records, dict):
            records = [records]

    conn = psycopg2.connect(DSN)
    try:
        for rec in records:
            notice_id = upsert_rfp(conn, rec)
            upsert_attachments(conn, notice_id, rec.get('attachments', []))
        logger.info("Ingestion completed successfully.")
    except Exception as e:
        logger.exception("Error during ingestion")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print("Usage: python ingest_ms_rfp.py <path_to_json>")
        sys.exit(1)
    main(sys.argv[1])