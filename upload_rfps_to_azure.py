import os
import json
import logging
from azure.storage.blob import BlobServiceClient, ContentSettings
import psycopg2

# ======================= CONFIGURATION =======================
# Use the correct mapping file provided
MAPPING_JSON_PATH = r"C:\Scraping\Mississippi-Procurement\rfp_download_report_from_mapping_rfid_bidids.json"
DOWNLOADED_RFPS_BASE = r"C:\Scraping\Mississippi-Procurement\downloaded_rfps"

AZURE_STORAGE_CONNECTION_STRING = "YOUR_AZURE_CONNECTION_STRING"
AZURE_CONTAINER_NAME = "rfp-attachments"

DB_CONFIG = {
    "host": "XXXXXXX",
    "port": 5432,
    "database": "XXXXX",
    "user": "XXXXXXX",
    "password": "XXXXXXX",
    "sslmode": "XXXXX"
}


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# ======================= SAFE SCHEMA UPDATE (ADD COLUMN ONLY) =======================
def ensure_bid_id_column(conn):
    """Add bid_id column if it does not exist. No other changes."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'wyber_universal_rfps' AND column_name = 'bid_id';
        """)
        if not cur.fetchone():
            logger.info("Adding bid_id column to wyber_universal_rfps (NULL allowed).")
            cur.execute("ALTER TABLE wyber_universal_rfps ADD COLUMN bid_id INTEGER;")
            conn.commit()
        else:
            logger.info("bid_id column already exists, no schema changes needed.")


def ensure_rfp_docs_table(conn):
    """Create wyber_universal_rfp_docs if it does not exist."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS wyber_universal_rfp_docs (
                id SERIAL PRIMARY KEY,
                rfp_id INTEGER NOT NULL REFERENCES wyber_universal_rfps(id) ON DELETE CASCADE,
                file_name TEXT NOT NULL,
                blob_url TEXT NOT NULL,
                local_file_path TEXT,
                uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
            );
        """)
        conn.commit()
        logger.info("wyber_universal_rfp_docs table verified/created.")


# ======================= UPSERT LOGIC (NO UNIQUE CONSTRAINT REQUIRED) =======================
def get_or_create_rfp(conn, bid_id, rfp_number, detail_page_url, scraped_from_website, blob_folder_url):
    """
    Insert or update RFP record.
    Safe even if no unique constraint exists. Uses a transaction per RFP.
    """
    with conn.cursor() as cur:
        # Try to update existing row with matching rfp_number and bid_id (both NULL allowed)
        update_sql = """
            UPDATE wyber_universal_rfps
            SET detail_page_url = %s,
                scraped_from_website = %s,
                blob_folder_url = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE rfp_number = %s
              AND (bid_id = %s OR (bid_id IS NULL AND %s IS NULL))
            RETURNING id;
        """
        cur.execute(update_sql, (detail_page_url, scraped_from_website, blob_folder_url,
                                 rfp_number, bid_id, bid_id))
        row = cur.fetchone()
        if row:
            rfp_id = row[0]
        else:
            # Insert new row
            insert_sql = """
                INSERT INTO wyber_universal_rfps
                    (bid_id, rfp_number, detail_page_url, scraped_from_website, blob_folder_url)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING id;
            """
            cur.execute(insert_sql, (bid_id, rfp_number, detail_page_url,
                                     scraped_from_website, blob_folder_url))
            rfp_id = cur.fetchone()[0]
        conn.commit()
        return rfp_id


def add_document(conn, rfp_id, file_name, blob_url, local_file_path):
    """Insert document record, avoiding duplicates by blob_url manually."""
    with conn.cursor() as cur:
        # Check if blob_url already exists
        cur.execute("SELECT 1 FROM wyber_universal_rfp_docs WHERE blob_url = %s", (blob_url,))
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO wyber_universal_rfp_docs (rfp_id, file_name, blob_url, local_file_path)
                VALUES (%s, %s, %s, %s)
            """, (rfp_id, file_name, blob_url, local_file_path))
            conn.commit()
        else:
            logger.info(f"Document {file_name} already exists for blob {blob_url}, skipping.")


# ======================= AZURE BLOB UPLOAD =======================
def upload_file_to_blob(blob_service_client, container_name, local_file_path, blob_name):
    """Upload a file to Azure Blob. Returns the blob URL."""
    try:
        blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
        ext = os.path.splitext(local_file_path)[1].lower()
        content_type = {
            '.pdf': 'application/pdf',
            '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            '.doc': 'application/msword',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png'
        }.get(ext, 'application/octet-stream')

        with open(local_file_path, "rb") as data:
            blob_client.upload_blob(data, overwrite=True,
                                    content_settings=ContentSettings(content_type=content_type))
        blob_url = blob_client.url
        logger.info(f"Uploaded: {local_file_path} -> {blob_url}")
        return blob_url
    except Exception as e:
        logger.error(f"Failed to upload {local_file_path}: {e}")
        raise


# ======================= MAIN =======================
def main():
    # 1. Load the JSON report
    if not os.path.exists(MAPPING_JSON_PATH):
        logger.error(f"Mapping JSON not found: {MAPPING_JSON_PATH}")
        return
    with open(MAPPING_JSON_PATH, 'r', encoding='utf-8') as f:
        mapping = json.load(f)
    report = mapping.get("report", [])
    if not report:
        logger.warning("No report entries found.")
        return

    # 2. Connect to Azure Blob
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(AZURE_CONTAINER_NAME)
        if not container_client.exists():
            container_client.create_container()
            logger.info(f"Created container {AZURE_CONTAINER_NAME}")
    except Exception as e:
        logger.error(f"Azure Blob connection failed: {e}")
        return

    # 3. Connect to PostgreSQL and apply safe schema updates
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False  # explicit transaction control
        ensure_bid_id_column(conn)      # adds bid_id if missing
        ensure_rfp_docs_table(conn)     # creates doc table if missing
    except Exception as e:
        logger.error(f"PostgreSQL setup failed: {e}")
        return

    # 4. Process each RFP entry
    for entry in report:
        subdir = entry.get("subdirectory")
        rfp_number = entry.get("rfp_number")
        bid_id = entry.get("bid_id")  # may be None
        detail_page_url = entry.get("detail_page_url")
        scraped_from_website = entry.get("scraped_from_website")
        downloaded_files = entry.get("downloaded_files", [])

        if not downloaded_files:
            logger.info(f"No files for {subdir}")
            continue

        # Build blob folder path and URL
        folder_key = str(bid_id) if bid_id is not None else f"rfp_{rfp_number}"
        blob_folder_prefix = f"mississippi/{folder_key}"
        blob_folder_url = f"https://{blob_service_client.account_name}.blob.core.windows.net/{AZURE_CONTAINER_NAME}/{blob_folder_prefix}/"

        # Insert/update the RFP record (isolated transaction)
        try:
            rfp_id = get_or_create_rfp(conn, bid_id, rfp_number, detail_page_url,
                                       scraped_from_website, blob_folder_url)
        except Exception as e:
            logger.error(f"Failed to upsert RFP {rfp_number}: {e}")
            conn.rollback()
            continue

        # Upload each file and link it
        for file_info in downloaded_files:
            file_name = file_info.get("file_name")
            local_path = file_info.get("file_path")
            if not os.path.exists(local_path):
                logger.warning(f"File missing: {local_path}")
                continue

            blob_name = f"{blob_folder_prefix}/{file_name}"
            try:
                blob_url = upload_file_to_blob(blob_service_client, AZURE_CONTAINER_NAME,
                                               local_path, blob_name)
                add_document(conn, rfp_id, file_name, blob_url, local_path)
            except Exception as e:
                logger.error(f"Error processing {local_path}: {e}")
                conn.rollback()
                # Continue with next file

    conn.close()
    logger.info("All processing completed.")


if __name__ == "__main__":
    main()