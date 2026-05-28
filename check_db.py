"""Full DB diagnostic — checks ALL sources for UUID notice_ids."""
import psycopg2

conn = psycopg2.connect(
    host='XXXXXXXX',
    port=5432, dbname='XXXXXXXX',
    user='XXXXXXXX', password='XXXXXXXX', sslmode='XXXXXXXX'
)


cur = conn.cursor()

UUID_PAT = r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'

print("=== wyber_universal_rfps_expt_1 — FULL TABLE ===")
cur.execute("SELECT COUNT(*) FROM wyber_universal_rfps_expt_1")
print("Total rows (all sources):", cur.fetchone()[0])

cur.execute(f"SELECT COUNT(*) FROM wyber_universal_rfps_expt_1 WHERE notice_id ~ '{UUID_PAT}'")
print("UUID notice_id rows (all sources):", cur.fetchone()[0])

cur.execute(f"SELECT COUNT(*) FROM wyber_universal_rfps_expt_1 WHERE global_notice_id ~ '{UUID_PAT}'")
print("UUID global_notice_id rows (all sources):", cur.fetchone()[0])

print("\nBreakdown by source_name (UUID notice_id):")
cur.execute(f"""
    SELECT source_name, COUNT(*) 
    FROM wyber_universal_rfps_expt_1 
    WHERE notice_id ~ '{UUID_PAT}'
    GROUP BY source_name 
    ORDER BY COUNT(*) DESC
""")
for r in cur.fetchall():
    print(f"  {r[1]:>5}  {r[0]}")

print("\nBreakdown by source_name (UUID global_notice_id):")
cur.execute(f"""
    SELECT source_name, COUNT(*) 
    FROM wyber_universal_rfps_expt_1 
    WHERE global_notice_id ~ '{UUID_PAT}'
    GROUP BY source_name 
    ORDER BY COUNT(*) DESC
""")
for r in cur.fetchall():
    print(f"  {r[1]:>5}  {r[0]}")

print("\nAll distinct source_names in table:")
cur.execute("SELECT source_name, COUNT(*) FROM wyber_universal_rfps_expt_1 GROUP BY source_name ORDER BY COUNT(*) DESC")
for r in cur.fetchall():
    print(f"  {r[1]:>5}  {r[0]}")

print("\nSample UUID notice_id rows (first 10, any source):")
cur.execute(f"""
    SELECT notice_id, global_notice_id, source_name
    FROM wyber_universal_rfps_expt_1
    WHERE notice_id ~ '{UUID_PAT}'
    LIMIT 10
""")
for r in cur.fetchall():
    print(" ", r)

print("\n=== wyber_universal_rfp_docs_expt_1 — FULL TABLE ===")
cur.execute("SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1")
print("Total rows (all sources):", cur.fetchone()[0])

cur.execute(f"SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1 WHERE notice_id ~ '{UUID_PAT}'")
print("UUID notice_id doc rows (all sources):", cur.fetchone()[0])

print("\nBreakdown by source_name (UUID notice_id in docs):")
cur.execute(f"""
    SELECT source_name, COUNT(*) 
    FROM wyber_universal_rfp_docs_expt_1 
    WHERE notice_id ~ '{UUID_PAT}'
    GROUP BY source_name 
    ORDER BY COUNT(*) DESC
""")
for r in cur.fetchall():
    print(f"  {r[1]:>5}  {r[0]}")

print("\nAll distinct source_names in docs table:")
cur.execute("SELECT source_name, COUNT(*) FROM wyber_universal_rfp_docs_expt_1 GROUP BY source_name ORDER BY COUNT(*) DESC")
for r in cur.fetchall():
    print(f"  {r[1]:>5}  {r[0]}")

conn.close()
print("\nDone.")
