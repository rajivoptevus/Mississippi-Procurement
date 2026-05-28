# #!/usr/bin/env python3
# """
# Duplicate three RFB/RFP tables for experimental purposes.
# Original tables: wyber_universal_rfps, wyber_universal_rfp_docs, wyber_universal_rfps_plan_holders
# Copy tables: same name with suffix '_copy'
# """

# import os
# import sys
# import psycopg2
# from psycopg2 import sql

# # ========== CONFIGURATION (from your ConnectionStrings) ==========
# # Replace placeholder values with your real database credentials
# DB_CONFIG = {
#     'host': os.environ.get('PG_HOST', 'rfpwyer-postgresql.postgres.database.azure.com'),       # your database host
#     'port': os.environ.get('PG_PORT', '5432'),          # usually 5432
#     'dbname': os.environ.get('PG_DBNAME', 'masterwyber'),      # database name
#     'user': os.environ.get('PG_USER', 'rfppgadmin'),          # username
#     'password': os.environ.get('PG_PASSWORD', 'H@Sh1CoR3!'),  # password
#     # Optional: connection timeout (seconds)
#     'connect_timeout': os.environ.get('PG_CONNECT_TIMEOUT', '30')
# }

# # Suffix for copy tables (change if you like)
# COPY_SUFFIX = '_expt_1'

# # List of original tables to duplicate
# ORIGINAL_TABLES = [
#     'wyber_universal_rfps',
#     'wyber_universal_rfp_docs',
#     'wyber_universal_rfps_plan_holders'
# ]

# # Set to True if you want to drop existing copy tables before recreating
# DROP_EXISTING_COPIES = True
# # ================================================================

# def get_copy_name(original_name):
#     return original_name + COPY_SUFFIX

# def table_exists(conn, table_name):
#     """Check if a table exists in the public schema."""
#     with conn.cursor() as cur:
#         cur.execute("""
#             SELECT EXISTS (
#                 SELECT 1 FROM information_schema.tables
#                 WHERE table_schema = 'public' AND table_name = %s
#             )
#         """, (table_name,))
#         return cur.fetchone()[0]

# def drop_table_if_exists(conn, table_name):
#     """Drop table if it exists (CASCADE to drop dependent objects like sequences)."""
#     if table_exists(conn, table_name):
#         with conn.cursor() as cur:
#             cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(
#                 sql.Identifier(table_name)
#             ))
#             print(f"Dropped existing table: {table_name}")

# def create_copy_table(conn, original, copy):
#     """Create copy table using LIKE INCLUDING ALL (indexes, defaults, constraints)."""
#     with conn.cursor() as cur:
#         cur.execute(sql.SQL("""
#             CREATE TABLE {} (LIKE {} INCLUDING ALL)
#         """).format(sql.Identifier(copy), sql.Identifier(original)))
#         print(f"Created table: {copy} (structure + indexes + constraints)")

# def copy_data(conn, original, copy):
#     """Copy all data from original to copy."""
#     with conn.cursor() as cur:
#         cur.execute(sql.SQL("INSERT INTO {} SELECT * FROM {}").format(
#             sql.Identifier(copy), sql.Identifier(original)
#         ))
#         print(f"Copied data: {original} -> {copy} ({cur.rowcount} rows)")

# def reset_sequence_for_id_column(conn, table_name, id_column='id'):
#     """
#     For a table with a BIGINT 'id' column that has a DEFAULT nextval(...),
#     create a new sequence owned by the copy table and set its value to max(id).
#     """
#     # First, check if the id column uses a sequence default
#     with conn.cursor() as cur:
#         cur.execute("""
#             SELECT column_default
#             FROM information_schema.columns
#             WHERE table_schema = 'public'
#               AND table_name = %s
#               AND column_name = %s
#         """, (table_name, id_column))
#         row = cur.fetchone()
#         if not row or not row[0]:
#             print(f"  No default sequence found for {table_name}.{id_column} – skipping.")
#             return

#         default_expr = row[0]
#         if 'nextval' not in default_expr:
#             print(f"  Default for {table_name}.{id_column} is not a sequence – skipping.")
#             return

#     # Generate new sequence name
#     seq_name = f"{table_name}_{id_column}_seq"

#     with conn.cursor() as cur:
#         # Create a new sequence
#         cur.execute(sql.SQL("CREATE SEQUENCE IF NOT EXISTS {}").format(sql.Identifier(seq_name)))
#         # Set its next value to max(id) + 1 (or 1 if table empty)
#         cur.execute(sql.SQL("SELECT COALESCE(max({}), 0) FROM {}").format(
#             sql.Identifier(id_column), sql.Identifier(table_name)
#         ))
#         max_id = cur.fetchone()[0]
#         cur.execute(sql.SQL("SELECT setval(%s, %s)").format(
#             sql.Identifier(seq_name)
#         ), (seq_name, max_id + 1 if max_id else 1))

#         # Alter column to use the new sequence
#         cur.execute(sql.SQL("ALTER TABLE {} ALTER COLUMN {} SET DEFAULT nextval(%s)").format(
#             sql.Identifier(table_name), sql.Identifier(id_column)
#         ), (seq_name,))

#         print(f"  Reset sequence {seq_name} to start at {max_id + 1 if max_id else 1}")

# def restore_foreign_keys(conn):
#     """
#     Optionally restore foreign key relationships between copy tables.
#     Original schema likely has FKs from docs/plan_holders to rfps on notice_id.
#     """
#     with conn.cursor() as cur:
#         fk_pairs = [
#             ('wyber_universal_rfp_docs_copy', 'wyber_universal_rfps_copy', 'notice_id'),
#             ('wyber_universal_rfps_plan_holders_copy', 'wyber_universal_rfps_copy', 'notice_id')
#         ]
#         for child, parent, col in fk_pairs:
#             # Check if the FK already exists to avoid duplicate errors
#             cur.execute("""
#                 SELECT 1 FROM information_schema.table_constraints
#                 WHERE constraint_type = 'FOREIGN KEY'
#                   AND table_name = %s
#                   AND constraint_name LIKE %s
#             """, (child, f'%{col}%'))
#             if cur.fetchone():
#                 print(f"  Foreign key on {child}.{col} -> {parent} already exists, skipping.")
#                 continue

#             if table_exists(conn, child) and table_exists(conn, parent):
#                 try:
#                     cur.execute(sql.SQL("""
#                         ALTER TABLE {} ADD CONSTRAINT fk_{}_{}
#                         FOREIGN KEY ({}) REFERENCES {} ({})
#                     """).format(
#                         sql.Identifier(child),
#                         sql.Identifier(f"{child}_{col}_fk"),
#                         sql.Identifier(parent),
#                         sql.Identifier(col),
#                         sql.Identifier(parent),
#                         sql.Identifier(col)
#                     ))
#                     print(f"  Added foreign key: {child}.{col} -> {parent}")
#                 except psycopg2.Error as e:
#                     print(f"  Could not add FK for {child}.{col}: {e}")
#             else:
#                 print(f"  Skipping FK: {child} or {parent} not found")

# def main():
#     print("Connecting to database using your ConnectionStrings...")
#     try:
#         conn = psycopg2.connect(**DB_CONFIG)
#         conn.autocommit = False
#         print("Connected successfully.\n")
#     except Exception as e:
#         print(f"Connection failed: {e}")
#         sys.exit(1)

#     try:
#         # 1. Drop copy tables if requested
#         if DROP_EXISTING_COPIES:
#             for orig in ORIGINAL_TABLES:
#                 copy_tab = get_copy_name(orig)
#                 drop_table_if_exists(conn, copy_tab)

#         # 2. Create copy tables (structure only)
#         for orig in ORIGINAL_TABLES:
#             copy_tab = get_copy_name(orig)
#             create_copy_table(conn, orig, copy_tab)

#         # 3. Copy data
#         for orig in ORIGINAL_TABLES:
#             copy_tab = get_copy_name(orig)
#             copy_data(conn, orig, copy_tab)

#         # 4. Reset sequences for id columns
#         for orig in ORIGINAL_TABLES:
#             copy_tab = get_copy_name(orig)
#             print(f"Processing sequence for {copy_tab}.id")
#             reset_sequence_for_id_column(conn, copy_tab, 'id')

#         # 5. Restore foreign keys (optional)
#         print("\nRestoring foreign keys between copies...")
#         restore_foreign_keys(conn)

#         conn.commit()
#         print("\nDuplication completed successfully!")
#         print(f"Copy tables (suffix '{COPY_SUFFIX}') are ready for experimentation.")

#     except Exception as e:
#         conn.rollback()
#         print(f"\nError occurred: {e}")
#         print("Rolled back all changes.")
#         raise
#     finally:
#         conn.close()

# if __name__ == "__main__":
#     main()
    
#!/usr/bin/env python3
"""
Duplicate ONLY the structure of three tables (no data) for experimentation.
Copy tables: same name + suffix (default '_expt_1')
No data is copied, only schema, indexes, constraints.
"""

import os
import sys
import psycopg2
from psycopg2 import sql

# ========== CONFIGURATION ==========
DB_CONFIG = {
    'host': os.environ.get('PG_HOST', 'rfpwyer-postgresql.postgres.database.azure.com'),
    'port': os.environ.get('PG_PORT', '5432'),
    'dbname': os.environ.get('PG_DBNAME', 'masterwyber'),
    'user': os.environ.get('PG_USER', 'rfppgadmin'),
    'password': os.environ.get('PG_PASSWORD', 'XXXXXX'),
    'connect_timeout': os.environ.get('PG_CONNECT_TIMEOUT', '30')
}

COPY_SUFFIX = '_expt_1'          # Change if desired
DROP_EXISTING_COPIES = True      # Drop existing copy tables before recreating
COPY_DATA = False                # Set to False for structure only

# Use the EXACT table names as they exist in your database
ORIGINAL_TABLES = [
    'wyber_universal_rfps',
    'wyber_universal_rfp_docs',
    'wyber_universal_rfp_plan_holders'   # Note: no 's' after rfp (matches your DB)
]
# ====================================

def get_copy_name(original_name):
    return original_name + COPY_SUFFIX

def table_exists(conn, table_name):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            )
        """, (table_name,))
        return cur.fetchone()[0]

def drop_table_if_exists(conn, table_name):
    if table_exists(conn, table_name):
        with conn.cursor() as cur:
            cur.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE").format(sql.Identifier(table_name)))
            print(f"Dropped existing table: {table_name}")

def create_copy_table(conn, original, copy):
    with conn.cursor() as cur:
        cur.execute(sql.SQL("""
            CREATE TABLE {} (LIKE {} INCLUDING ALL)
        """).format(sql.Identifier(copy), sql.Identifier(original)))
        print(f"Created table: {copy} (structure, indexes, constraints)")

def copy_data(conn, original, copy):
    with conn.cursor() as cur:
        cur.execute(sql.SQL("INSERT INTO {} SELECT * FROM {}").format(
            sql.Identifier(copy), sql.Identifier(original)
        ))
        print(f"Copied data: {original} -> {copy} ({cur.rowcount} rows)")

def reset_sequence(conn, table_name, id_column='id'):
    """Re‑create the sequence for the id column so that new inserts work."""
    with conn.cursor() as cur:
        # Check if column default uses a sequence
        cur.execute("""
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = %s
              AND column_name = %s
        """, (table_name, id_column))
        row = cur.fetchone()
        if not row or not row[0] or 'nextval' not in row[0]:
            print(f"  No sequence default for {table_name}.{id_column} – skipping.")
            return

    seq_name = f"{table_name}_{id_column}_seq"
    with conn.cursor() as cur:
        cur.execute(sql.SQL("CREATE SEQUENCE IF NOT EXISTS {}").format(sql.Identifier(seq_name)))
        cur.execute(sql.SQL("SELECT COALESCE(max({}), 0) FROM {}").format(
            sql.Identifier(id_column), sql.Identifier(table_name)
        ))
        max_id = cur.fetchone()[0]
        cur.execute(sql.SQL("SELECT setval(%s, %s)").format(sql.Identifier(seq_name)),
                    (seq_name, max_id + 1 if max_id else 1))
        cur.execute(sql.SQL("ALTER TABLE {} ALTER COLUMN {} SET DEFAULT nextval(%s)").format(
            sql.Identifier(table_name), sql.Identifier(id_column)
        ), (seq_name,))
        print(f"  Reset sequence {seq_name} to start at {max_id + 1 if max_id else 1}")

def restore_foreign_keys(conn):
    """Add foreign keys between copy tables if they originally existed."""
    with conn.cursor() as cur:
        # Use the actual copy table names
        fk_pairs = [
            ('wyber_universal_rfp_docs' + COPY_SUFFIX, 'wyber_universal_rfps' + COPY_SUFFIX, 'notice_id'),
            ('wyber_universal_rfp_plan_holders' + COPY_SUFFIX, 'wyber_universal_rfps' + COPY_SUFFIX, 'notice_id')
        ]
        for child, parent, col in fk_pairs:
            if not (table_exists(conn, child) and table_exists(conn, parent)):
                print(f"  Skipping FK: {child} or {parent} does not exist")
                continue
            # Avoid duplicate FK
            cur.execute("""
                SELECT 1 FROM information_schema.table_constraints
                WHERE constraint_type = 'FOREIGN KEY'
                  AND table_name = %s
                  AND constraint_name LIKE %s
            """, (child, f'%{col}%'))
            if cur.fetchone():
                print(f"  Foreign key on {child}.{col} -> {parent} already exists, skipping.")
                continue
            try:
                constr_name = f"fk_{child}_{col}"
                cur.execute(sql.SQL("""
                    ALTER TABLE {} ADD CONSTRAINT {}
                    FOREIGN KEY ({}) REFERENCES {} ({})
                """).format(
                    sql.Identifier(child),
                    sql.Identifier(constr_name),
                    sql.Identifier(col),
                    sql.Identifier(parent),
                    sql.Identifier(col)
                ))
                print(f"  Added foreign key: {child}.{col} -> {parent}")
            except psycopg2.Error as e:
                print(f"  Could not add FK for {child}.{col}: {e}")

def main():
    print("Connecting to database...")
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        print("Connected.\n")
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    try:
        # Determine which original tables actually exist
        existing = []
        for orig in ORIGINAL_TABLES:
            if table_exists(conn, orig):
                existing.append(orig)
            else:
                print(f"⚠️ Table '{orig}' does not exist – skipping.")

        if not existing:
            print("No tables to duplicate. Exiting.")
            conn.rollback()
            return

        # Drop existing copies if requested
        if DROP_EXISTING_COPIES:
            for orig in existing:
                drop_table_if_exists(conn, get_copy_name(orig))

        # Create copy tables (structure only)
        for orig in existing:
            create_copy_table(conn, orig, get_copy_name(orig))

        # Copy data only if requested
        if COPY_DATA:
            for orig in existing:
                copy_data(conn, orig, get_copy_name(orig))

        # Reset sequences for 'id' columns
        for orig in existing:
            copy_tab = get_copy_name(orig)
            print(f"Resetting sequence for {copy_tab}.id")
            reset_sequence(conn, copy_tab, 'id')

        # Restore foreign keys – COMMENTED OUT by default (uncomment if needed)
        # print("\nRestoring foreign keys between copies...")
        # restore_foreign_keys(conn)

        conn.commit()
        print("\n✅ Structure duplication complete (no data copied).")
        print(f"Copy tables suffix: '{COPY_SUFFIX}'")
        print(f"Processed tables: {existing}")

    except Exception as e:
        conn.rollback()
        print(f"\n❌ Error: {e}")
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()