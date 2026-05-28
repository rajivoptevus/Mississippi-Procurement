"""
Delete all UUID notice_id rows with source_name = NULL from both tables.
These are bad Mississippi records inserted by the old db_uploader.py.
"""
import psycopg2

conn = psycopg2.connect(
    host='rfpwyer-postgresql.postgres.database.azure.com',
    port=5432, dbname='masterwyber',
    user='rfppgadmin', password='H@Sh1CoR3!', sslmode='require'
)
conn.autocommit = False
cur = conn.cursor()

UUID_PAT = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

# Count first
cur.execute(f"SELECT COUNT(*) FROM wyber_universal_rfps_expt_1 WHERE notice_id ~ '{UUID_PAT}' AND source_name IS NULL")
rfp_count = cur.fetchone()[0]

cur.execute(f"SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1 WHERE notice_id ~ '{UUID_PAT}' AND source_name IS NULL")
doc_count = cur.fetchone()[0]

print(f"About to delete:")
print(f"  {rfp_count} UUID RFP rows  (source_name IS NULL)")
print(f"  {doc_count} UUID Doc rows  (source_name IS NULL)")

# Delete docs first (FK dependency)
cur.execute(f"DELETE FROM wyber_universal_rfp_docs_expt_1 WHERE notice_id ~ '{UUID_PAT}' AND source_name IS NULL")
print(f"\n✓ Deleted {cur.rowcount} doc rows")

cur.execute(f"DELETE FROM wyber_universal_rfps_expt_1 WHERE notice_id ~ '{UUID_PAT}' AND source_name IS NULL")
print(f"✓ Deleted {cur.rowcount} RFP rows")

conn.commit()

# Verify
cur.execute(f"SELECT COUNT(*) FROM wyber_universal_rfps_expt_1 WHERE notice_id ~ '{UUID_PAT}'")
print(f"\nRemaining UUID RFP rows (any source): {cur.fetchone()[0]}")

cur.execute(f"SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1 WHERE notice_id ~ '{UUID_PAT}'")
print(f"Remaining UUID Doc rows (any source): {cur.fetchone()[0]}")

cur.execute("SELECT source_name, COUNT(*) FROM wyber_universal_rfps_expt_1 GROUP BY source_name ORDER BY COUNT(*) DESC")
print("\nFinal RFP table breakdown:")
for r in cur.fetchall():
    print(f"  {r[1]:>5}  {r[0]}")

cur.execute("SELECT source_name, COUNT(*) FROM wyber_universal_rfp_docs_expt_1 GROUP BY source_name ORDER BY COUNT(*) DESC")
print("\nFinal Docs table breakdown:")
for r in cur.fetchall():
    print(f"  {r[1]:>5}  {r[0]}")

conn.close()
print("\nDone.")
