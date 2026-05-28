"""Inspect the NULL source_name UUID rows before deleting."""
import psycopg2

conn = psycopg2.connect(
    host='XXXXXXXX',
    port=5432, dbname='XXXXXXXX',
    user='XXXXXXXX', password='XXXXXXXX', sslmode='XXXXXXXX'
)


cur = conn.cursor()

UUID_PAT = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

print("=== Sample NULL-source UUID RFP rows (10) ===")
cur.execute(f"""
    SELECT notice_id, global_notice_id, title, agency, state_code,
           source_url, created_on
    FROM wyber_universal_rfps_expt_1
    WHERE notice_id ~ '{UUID_PAT}'
    LIMIT 10
""")
cols = [d[0] for d in cur.description]
for r in cur.fetchall():
    for c, v in zip(cols, r):
        print(f"  {c}: {v}")
    print()

print("=== Sample NULL-source UUID DOC rows (5) ===")
cur.execute(f"""
    SELECT notice_id, global_notice_id, file_name, blob_url, source_url, created_at
    FROM wyber_universal_rfp_docs_expt_1
    WHERE notice_id ~ '{UUID_PAT}'
    LIMIT 5
""")
cols = [d[0] for d in cur.description]
for r in cur.fetchall():
    for c, v in zip(cols, r):
        print(f"  {c}: {v}")
    print()

conn.close()
print("Done.")
