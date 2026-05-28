"""Delete all existing MS doc rows so db_fix.py can re-insert them cleanly."""
import psycopg2, sys
sys.path.insert(0, '.')
import config as _cfg

conn = psycopg2.connect(
    host=_cfg.DB_HOST, port=_cfg.DB_PORT, dbname=_cfg.DB_NAME,
    user=_cfg.DB_USER, password=_cfg.DB_PASSWORD, sslmode=_cfg.DB_SSLMODE
)
conn.autocommit = False
cur = conn.cursor()

cur.execute("SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1 WHERE source_name = 'Mississippi Procurement Portal'")
print(f"Current MS doc rows: {cur.fetchone()[0]}")

cur.execute("DELETE FROM wyber_universal_rfp_docs_expt_1 WHERE source_name = 'Mississippi Procurement Portal'")
print(f"Deleted: {cur.rowcount} rows")
conn.commit()

# Reset sequence
cur.execute("SELECT MAX(id) FROM wyber_universal_rfp_docs_expt_1")
max_id = cur.fetchone()[0] or 0
cur.execute(f"SELECT setval('wyber_universal_rfp_docs_expt_1_id_seq', {max(max_id, 1)})")
conn.commit()

cur.execute("SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1")
print(f"Remaining total doc rows: {cur.fetchone()[0]}")
conn.close()
print("Done.")
