"""Inspect columns of all 4 tables to understand schema differences."""
import psycopg2, sys
sys.path.insert(0, '.')
import config as _cfg

conn = psycopg2.connect(
    host=_cfg.DB_HOST, port=_cfg.DB_PORT, dbname=_cfg.DB_NAME,
    user=_cfg.DB_USER, password=_cfg.DB_PASSWORD, sslmode=_cfg.DB_SSLMODE
)
cur = conn.cursor()

tables = [
    'wyber_universal_rfps_expt_1',
    'wyber_universal_rfps',
    'wyber_universal_rfp_docs_expt_1',
    'wyber_universal_rfp_docs',
]

for table in tables:
    cur.execute("""
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_name = %s
        ORDER BY ordinal_position
    """, (table,))
    rows = cur.fetchall()
    print(f"\n{'='*60}")
    print(f"TABLE: {table}  ({len(rows)} columns)")
    print(f"{'='*60}")
    for col, dtype, nullable, default in rows:
        d = f"  DEFAULT={default[:40]}" if default else ""
        n = "NULL" if nullable == 'YES' else "NOT NULL"
        print(f"  {col:<40} {dtype:<25} {n}{d}")

# Also check row counts
print("\n\n=== ROW COUNTS ===")
for table in tables:
    try:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"  {table}: {cur.fetchone()[0]}")
    except Exception as e:
        print(f"  {table}: ERROR - {e}")
        conn.rollback()

# Check constraints / unique indexes on target tables
print("\n\n=== UNIQUE CONSTRAINTS on target tables ===")
for table in ['wyber_universal_rfps', 'wyber_universal_rfp_docs']:
    cur.execute("""
        SELECT indexname, indexdef
        FROM pg_indexes
        WHERE tablename = %s
    """, (table,))
    for row in cur.fetchall():
        print(f"  [{table}] {row[0]}: {row[1][:100]}")

conn.close()
print("\nDone.")
