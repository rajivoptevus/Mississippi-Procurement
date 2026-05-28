"""
Check table id columns and reset sequences to start from 1.
WARNING: Only safe if the tables are empty or you want to re-number from scratch.
"""
import psycopg2

conn = psycopg2.connect(
    host='rfpwyer-postgresql.postgres.database.azure.com',
    port=5432, dbname='masterwyber',
    user='rfppgadmin', password='H@Sh1CoR3!', sslmode='require'
)
conn.autocommit = False
cur = conn.cursor()

# Check current id range in both tables
cur.execute("SELECT MIN(id), MAX(id), COUNT(*) FROM wyber_universal_rfps_expt_1")
r = cur.fetchone()
print(f"RFPs  — min id: {r[0]}, max id: {r[1]}, count: {r[2]}")

cur.execute("SELECT MIN(id), MAX(id), COUNT(*) FROM wyber_universal_rfp_docs_expt_1")
r = cur.fetchone()
print(f"Docs  — min id: {r[0]}, max id: {r[1]}, count: {r[2]}")

# Find the sequence names for both tables
cur.execute("""
    SELECT table_name, column_name, column_default
    FROM information_schema.columns
    WHERE table_name IN ('wyber_universal_rfps_expt_1', 'wyber_universal_rfp_docs_expt_1')
      AND column_default LIKE 'nextval%'
""")
print("\nSequence columns:")
sequences = []
for row in cur.fetchall():
    print(f"  {row[0]}.{row[1]} → {row[2]}")
    # Extract sequence name from nextval('seq_name'::regclass)
    import re
    m = re.search(r"nextval\('([^']+)'", row[2])
    if m:
        sequences.append((row[0], row[1], m.group(1)))

conn.close()
print("\nSequences found:", sequences)
print("\nTo reset, run reset_sequence.py with --reset flag")
