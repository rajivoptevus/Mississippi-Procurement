"""
Re-number id columns from 1 and reset sequences for both tables.
Steps:
  1. Add a temp column to preserve order
  2. Re-assign id values starting from 1 (ordered by created_on/created_at)
  3. Reset the sequence to MAX(id) + 1
"""
import psycopg2

conn = psycopg2.connect(
    host='rfpwyer-postgresql.postgres.database.azure.com',
    port=5432, dbname='masterwyber',
    user='rfppgadmin', password='H@Sh1CoR3!', sslmode='require'
)
conn.autocommit = False
cur = conn.cursor()

print("=== Resetting IDs ===\n")

# ── RFPs table ─────────────────────────────────────────────────
print("1. Re-numbering wyber_universal_rfps_expt_1 ...")
cur.execute("""
    UPDATE wyber_universal_rfps_expt_1 r
    SET id = sub.new_id
    FROM (
        SELECT id, ROW_NUMBER() OVER (ORDER BY created_on, id) AS new_id
        FROM wyber_universal_rfps_expt_1
    ) sub
    WHERE r.id = sub.id
""")
print(f"   Updated {cur.rowcount} rows")

cur.execute("SELECT MAX(id) FROM wyber_universal_rfps_expt_1")
max_rfp_id = cur.fetchone()[0]
cur.execute(f"SELECT setval('wyber_universal_rfps_expt_1_id_seq', {max_rfp_id})")
print(f"   Sequence reset to {max_rfp_id}")

# ── Docs table ─────────────────────────────────────────────────
print("\n2. Re-numbering wyber_universal_rfp_docs_expt_1 ...")
cur.execute("""
    UPDATE wyber_universal_rfp_docs_expt_1 d
    SET id = sub.new_id
    FROM (
        SELECT id, ROW_NUMBER() OVER (ORDER BY created_at, id) AS new_id
        FROM wyber_universal_rfp_docs_expt_1
    ) sub
    WHERE d.id = sub.id
""")
print(f"   Updated {cur.rowcount} rows")

cur.execute("SELECT MAX(id) FROM wyber_universal_rfp_docs_expt_1")
max_doc_id = cur.fetchone()[0]
cur.execute(f"SELECT setval('wyber_universal_rfp_docs_expt_1_id_seq', {max_doc_id})")
print(f"   Sequence reset to {max_doc_id}")

conn.commit()

# ── Verify ─────────────────────────────────────────────────────
print("\n=== Verification ===")
cur.execute("SELECT MIN(id), MAX(id), COUNT(*) FROM wyber_universal_rfps_expt_1")
r = cur.fetchone()
print(f"RFPs  — min id: {r[0]}, max id: {r[1]}, count: {r[2]}")

cur.execute("SELECT MIN(id), MAX(id), COUNT(*) FROM wyber_universal_rfp_docs_expt_1")
r = cur.fetchone()
print(f"Docs  — min id: {r[0]}, max id: {r[1]}, count: {r[2]}")

print("\nFirst 5 RFP ids:")
cur.execute("SELECT id, notice_id, global_notice_id FROM wyber_universal_rfps_expt_1 ORDER BY id LIMIT 5")
for row in cur.fetchall():
    print(f"  id={row[0]}  notice_id={row[1]}  gid={row[2]}")

print("\nFirst 5 Doc ids:")
cur.execute("SELECT id, notice_id, global_notice_id FROM wyber_universal_rfp_docs_expt_1 ORDER BY id LIMIT 5")
for row in cur.fetchall():
    print(f"  id={row[0]}  notice_id={row[1]}  gid={row[2]}")

conn.close()
print("\nDone.")
