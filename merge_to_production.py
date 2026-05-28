"""
Merge Experiment Tables → Production Tables
============================================
Merges:
  wyber_universal_rfps_expt_1   →  wyber_universal_rfps
  wyber_universal_rfp_docs_expt_1  →  wyber_universal_rfp_docs

Strategy:
  - RFPs:  ON CONFLICT (global_notice_id) DO UPDATE  (upsert)
  - Docs:  ON CONFLICT (global_notice_id, file_name) DO UPDATE  (upsert)
  - Only copies columns that exist in BOTH tables (safe schema mapping)
  - Skips expt_1-only columns that don't exist in production
  - Never overwrites production data with NULL if production already has a value
  - Dry-run mode available (--dry-run flag)

USAGE:
    cd C:\\Scraping\\Mississippi-Procurement

    # Preview what would happen (no writes)
    python merge_to_production.py --dry-run

    # Merge only RFPs
    python merge_to_production.py --rfps-only

    # Merge only docs
    python merge_to_production.py --docs-only

    # Full merge
    python merge_to_production.py

    # Filter by source (merge only Mississippi rows)
    python merge_to_production.py --source "Mississippi Procurement Portal"

REQUIREMENTS:
    pip install psycopg2-binary
"""

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

# ─────────────────────────── CONFIG ───────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
import config as _cfg

DB_PARAMS = dict(
    host=_cfg.DB_HOST,
    port=_cfg.DB_PORT,
    dbname=_cfg.DB_NAME,
    user=_cfg.DB_USER,
    password=_cfg.DB_PASSWORD,
    sslmode=getattr(_cfg, "DB_SSLMODE", "require"),
)

SRC_RFPS  = "wyber_universal_rfps_expt_1"
DST_RFPS  = "wyber_universal_rfps"
SRC_DOCS  = "wyber_universal_rfp_docs_expt_1"
DST_DOCS  = "wyber_universal_rfp_docs"

# ─────────────────────────── LOGGING ──────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("merge_to_production.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("merge")


# ─────────────────────────── HELPERS ──────────────────────────
def get_columns(cur, table: str) -> List[str]:
    """Return ordered list of column names for a table."""
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (table,))
    return [r[0] for r in cur.fetchall()]


def get_shared_columns(cur, src: str, dst: str, exclude: List[str] = None) -> List[str]:
    """
    Return columns that exist in BOTH src and dst, minus excluded ones.
    The 'id' column is always excluded (auto-generated in dst).
    """
    src_cols = set(get_columns(cur, src))
    dst_columns = get_columns(cur, dst)
    dst_cols = set(dst_columns)
    shared   = src_cols & dst_cols
    always_exclude = {"id"}
    if exclude:
        always_exclude.update(exclude)
    result = [c for c in dst_columns if c in shared and c not in always_exclude]
    return result


def get_row_count(cur, table: str, source_filter: Optional[str] = None) -> int:
    if source_filter:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE source_name = %s", (source_filter,))
    else:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
    return cur.fetchone()[0]


# ─────────────────────────── RFP MERGE ────────────────────────
def merge_rfps(conn, dry_run: bool, source_filter: Optional[str]):
    """
    Merge wyber_universal_rfps_expt_1 → wyber_universal_rfps.
    Conflict key: global_notice_id (unique in production).
    On conflict: update all shared columns ONLY if the incoming value is not NULL
                 and the column is not a system/audit column we want to preserve.
    """
    cur = conn.cursor()

    shared_cols = get_shared_columns(cur, SRC_RFPS, DST_RFPS, exclude=["id"])
    log.info(f"RFPs — shared columns: {len(shared_cols)}")

    # Columns we never overwrite on conflict (preserve production values)
    preserve_on_conflict = {
        "created_on", "created_by_id",
        "is_vector", "vector_model", "vector_updated_at",
        "files_processed", "vectors_processed",
        "storing_file_error", "storing_vectors_error",
        "classification", "reasoning", "classification_calculated",
        "relevant_company", "change_log",
    }

    # Columns to update on conflict (everything shared except preserved + conflict key)
    # Also exclude modified_on — it's set explicitly as NOW() at the end
    update_cols = [
        c for c in shared_cols
        if c not in preserve_on_conflict
        and c not in {"global_notice_id", "modified_on"}
    ]

    src_count = get_row_count(cur, SRC_RFPS, source_filter)
    log.info(f"RFPs — source rows to merge: {src_count}")

    if dry_run:
        log.info(f"[DRY RUN] Would merge {src_count} RFP rows into {DST_RFPS}")
        log.info(f"[DRY RUN] Conflict key: global_notice_id")
        log.info(f"[DRY RUN] Update cols on conflict: {len(update_cols)}")
        log.info(f"[DRY RUN] Preserved cols on conflict: {sorted(preserve_on_conflict)}")
        cur.close()
        return 0, 0

    cols_str    = ", ".join(shared_cols)
    update_str  = ",\n        ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, {DST_RFPS}.{c})"
        for c in update_cols
    )

    where_clause = "WHERE s.source_name = %(source)s" if source_filter else ""

    sql = f"""
    INSERT INTO {DST_RFPS} ({cols_str})
    SELECT {cols_str}
    FROM {SRC_RFPS} s
    {where_clause}
    ON CONFLICT (global_notice_id) DO UPDATE SET
        {update_str},
        modified_on = NOW()
    """

    params = {"source": source_filter} if source_filter else {}

    inserted = 0
    updated  = 0

    try:
        cur.execute(sql, params)
        affected = cur.rowcount
        conn.commit()
        log.info(f"RFPs — {affected} rows affected (inserted + updated)")
        inserted = affected
        updated  = 0
    except Exception as e:
        conn.rollback()
        log.error(f"RFP merge failed: {e}")
        raise

    cur.close()
    return inserted, updated


# ─────────────────────────── DOCS MERGE ───────────────────────
def merge_docs(conn, dry_run: bool, source_filter: Optional[str]):
    """
    Merge wyber_universal_rfp_docs_expt_1 → wyber_universal_rfp_docs.
    Conflict key: (global_notice_id, file_name)  [unique_notice_file index]
    On conflict: update blob_url, file_size_bytes, mime_type, updated_at.
    """
    cur = conn.cursor()

    shared_cols = get_shared_columns(cur, SRC_DOCS, DST_DOCS, exclude=["id"])
    log.info(f"Docs — shared columns: {len(shared_cols)}")

    # Columns not in production docs table (expt_1 extras — skip them)
    expt_only = {"attachment_id", "attachment_description"}
    shared_cols = [c for c in shared_cols if c not in expt_only]

    src_count = get_row_count(cur, SRC_DOCS, source_filter)
    log.info(f"Docs — source rows to merge: {src_count}")

    if dry_run:
        log.info(f"[DRY RUN] Would merge {src_count} Doc rows into {DST_DOCS}")
        log.info(f"[DRY RUN] Conflict key: (global_notice_id, file_name)")
        log.info(f"[DRY RUN] Shared cols: {shared_cols}")
        cur.close()
        return 0, 0

    cols_str   = ", ".join(shared_cols)
    where_clause = "WHERE source_name = %(source)s" if source_filter else ""

    # On conflict: update the mutable fields, preserve extracted text / vectors
    update_str = """,
        blob_url        = COALESCE(EXCLUDED.blob_url, {dst}.blob_url),
        file_size_bytes = COALESCE(EXCLUDED.file_size_bytes, {dst}.file_size_bytes),
        mime_type       = COALESCE(EXCLUDED.mime_type, {dst}.mime_type),
        source_url      = COALESCE(EXCLUDED.source_url, {dst}.source_url),
        source_file_url = COALESCE(EXCLUDED.source_file_url, {dst}.source_file_url),
        document_type   = COALESCE(EXCLUDED.document_type, {dst}.document_type),
        updated_at      = NOW()
    """.format(dst=DST_DOCS)

    sql = f"""
    INSERT INTO {DST_DOCS} ({cols_str})
    SELECT {cols_str}
    FROM {SRC_DOCS}
    {where_clause}
    ON CONFLICT (global_notice_id, file_name) DO UPDATE SET
        notice_id = EXCLUDED.notice_id
        {update_str}
    """

    params = {"source": source_filter} if source_filter else {}

    try:
        cur.execute(sql, params)
        affected = cur.rowcount
        conn.commit()
        log.info(f"Docs — {affected} rows affected (inserted + updated)")
    except Exception as e:
        conn.rollback()
        log.error(f"Docs merge failed: {e}")
        raise

    cur.close()
    return affected, 0


# ─────────────────────────── VERIFY ───────────────────────────
def verify(conn, source_filter: Optional[str]):
    """Print before/after counts and spot-check a few rows."""
    cur = conn.cursor()

    log.info("\n── Verification ──────────────────────────────────────")

    for src, dst in [(SRC_RFPS, DST_RFPS), (SRC_DOCS, DST_DOCS)]:
        src_n = get_row_count(cur, src, source_filter)
        dst_n = get_row_count(cur, dst, source_filter)
        dst_total = get_row_count(cur, dst)
        log.info(f"  {src:<40} → {src_n} rows")
        log.info(f"  {dst:<40} → {dst_n} rows (this source) / {dst_total} total")

    # Check no SAP URLs leaked into production docs
    if source_filter:
        sap_query = f"""
            SELECT COUNT(*) FROM {DST_DOCS}
            WHERE blob_url LIKE '%SRM.MAGIC%'
              AND source_name = %s
        """
        cur.execute(sap_query, (source_filter,))
    else:
        sap_query = f"""
            SELECT COUNT(*) FROM {DST_DOCS}
            WHERE blob_url LIKE '%SRM.MAGIC%'
        """
        cur.execute(sap_query)
    sap_count = cur.fetchone()[0]
    if sap_count:
        log.warning(f"  ⚠ {sap_count} SAP blob_urls found in {DST_DOCS} — fix needed")
    else:
        log.info(f"  ✓ No SAP blob_urls in {DST_DOCS}")

    # Check global_notice_id format
    if source_filter:
        gid_query = f"""
            SELECT COUNT(*) FROM {DST_RFPS}
            WHERE source_name = %s
              AND global_notice_id NOT LIKE 'ms_gov_dfa%%'
        """
        cur.execute(gid_query, (source_filter,))
    else:
        gid_query = f"""
            SELECT COUNT(*) FROM {DST_RFPS}
            WHERE global_notice_id NOT LIKE 'ms_gov_dfa%%'
        """
        cur.execute(gid_query)
    bad_gid = cur.fetchone()[0]
    if bad_gid:
        log.warning(f"  ⚠ {bad_gid} RFPs with unexpected global_notice_id format")
    else:
        log.info(f"  ✓ All global_notice_ids have correct format")

    cur.close()


# ─────────────────────────── MAIN ─────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Merge experiment tables into production tables"
    )
    parser.add_argument("--dry-run",    action="store_true",
                        help="Preview only — no writes to DB")
    parser.add_argument("--rfps-only",  action="store_true",
                        help="Merge only the RFPs table")
    parser.add_argument("--docs-only",  action="store_true",
                        help="Merge only the docs table")
    parser.add_argument("--source",     type=str, default=None,
                        help="Filter by source_name (e.g. 'Mississippi Procurement Portal')")
    args = parser.parse_args()

    do_rfps = not args.docs_only
    do_docs = not args.rfps_only

    log.info("=" * 65)
    log.info("Merge: experiment tables → production tables")
    log.info(f"  Source filter : {args.source or '(all sources)'}")
    log.info(f"  Dry run       : {args.dry_run}")
    log.info(f"  Merge RFPs    : {do_rfps}")
    log.info(f"  Merge Docs    : {do_docs}")
    log.info("=" * 65)

    import psycopg2
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        conn.autocommit = False
        log.info(f"✓ Connected to {DB_PARAMS['dbname']}@{DB_PARAMS['host']}")
    except Exception as e:
        log.error(f"DB connection failed: {e}")
        sys.exit(1)

    try:
        if do_rfps:
            log.info(f"\n── Merging RFPs: {SRC_RFPS} → {DST_RFPS} ──")
            rfp_affected, _ = merge_rfps(conn, args.dry_run, args.source)
            log.info(f"RFPs done — {rfp_affected} rows affected")

        if do_docs:
            log.info(f"\n── Merging Docs: {SRC_DOCS} → {DST_DOCS} ──")
            doc_affected, _ = merge_docs(conn, args.dry_run, args.source)
            log.info(f"Docs done — {doc_affected} rows affected")

        if not args.dry_run:
            verify(conn, args.source)

    except Exception as e:
        log.error(f"Merge failed: {e}")
        conn.close()
        sys.exit(1)

    conn.close()
    log.info("\n✓ Merge complete")


if __name__ == "__main__":
    main()
