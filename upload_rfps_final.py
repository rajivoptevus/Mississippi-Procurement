
import os
import json
import logging
import uuid
from azure.storage.blob import BlobServiceClient, ContentSettings
import psycopg2

# ======================= CONFIGURATION =======================
MAPPING_JSON_PATH = r"C:\Scraping\Mississippi-Procurement\rfp_download_report_from_mapping_rfid_bidids.json"

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


# ======================= SAFE SCHEMA UPDATE (ADD BLOB_FOLDER_URL) =======================
def ensure_blob_folder_column(conn):
    """Add blob_folder_url to wyber_universal_rfps if missing (optional)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'wyber_universal_rfps' AND column_name = 'blob_folder_url';
        """)
        if not cur.fetchone():
            logger.info("Adding blob_folder_url column to wyber_universal_rfps.")
            cur.execute("ALTER TABLE wyber_universal_rfps ADD COLUMN blob_folder_url TEXT;")
            conn.commit()
        else:
            logger.info("blob_folder_url column already exists.")


# ======================= FIND OR CREATE RFP =======================
def find_or_create_rfp(conn, bid_id, rfp_number, detail_page_url, scraped_from_website, blob_folder_url):
    """
    Find existing RFP by bid_id (if not null) or solicitation_number.
    If not found, insert a new RFP record using only existing columns.
    Returns global_notice_id.
    """
    with conn.cursor() as cur:
        # 1. Try by bid_id
        if bid_id is not None:
            cur.execute("SELECT global_notice_id FROM wyber_universal_rfps WHERE bid_id = %s LIMIT 1", (bid_id,))
            row = cur.fetchone()
            if row:
                global_notice_id = row[0]
                cur.execute("""
                    UPDATE wyber_universal_rfps
                    SET blob_folder_url = %s, modified_on = CURRENT_TIMESTAMP
                    WHERE global_notice_id = %s
                """, (blob_folder_url, global_notice_id))
                conn.commit()
                logger.info(f"Found RFP by bid_id {bid_id} -> global_notice_id {global_notice_id}")
                return global_notice_id

        # 2. Try by solicitation_number
        cur.execute("SELECT global_notice_id FROM wyber_universal_rfps WHERE solicitation_number = %s LIMIT 1", (rfp_number,))
        row = cur.fetchone()
        if row:
            global_notice_id = row[0]
            cur.execute("""
                UPDATE wyber_universal_rfps
                SET blob_folder_url = %s, modified_on = CURRENT_TIMESTAMP
                WHERE global_notice_id = %s
            """, (blob_folder_url, global_notice_id))
            conn.commit()
            logger.info(f"Found RFP by solicitation_number {rfp_number} -> global_notice_id {global_notice_id}")
            return global_notice_id

        # 3. Not found – insert new RFP record using only columns that exist
        unique_id = str(uuid.uuid4())[:8]
        global_notice_id = f"MS-RFP-{rfp_number}-{unique_id}"
        insert_sql = """
            INSERT INTO wyber_universal_rfps (
                global_notice_id, notice_id, solicitation_number, bid_id,
                source_name, state_code, source_url, description_url,
                created_on, modified_on, is_deleted
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, false)
        """
        cur.execute(insert_sql, (
            global_notice_id,
            rfp_number,                # notice_id (non‑null)
            rfp_number,                # solicitation_number
            bid_id,
            "Mississippi",             # source_name
            "MS",                      # state_code
            detail_page_url,           # source_url
            scraped_from_website       # description_url
        ))
        # Set blob_folder_url if column exists
        try:
            cur.execute("UPDATE wyber_universal_rfps SET blob_folder_url = %s WHERE global_notice_id = %s",
                        (blob_folder_url, global_notice_id))
        except Exception as e:
            logger.warning(f"Could not set blob_folder_url: {e}")
        conn.commit()
        logger.info(f"Inserted new RFP for {rfp_number} (bid_id={bid_id}) with global_notice_id {global_notice_id}")
        return global_notice_id


# ======================= INSERT DOCUMENT RECORD =======================
def add_document(conn, global_notice_id, rfp_number, file_name, blob_url, local_path, source_url):
    """Insert a row into wyber_universal_rfp_docs."""
    with conn.cursor() as cur:
        # Check duplicate
        cur.execute("SELECT 1 FROM wyber_universal_rfp_docs WHERE blob_url = %s", (blob_url,))
        if cur.fetchone():
            logger.info(f"Document {file_name} already exists for blob {blob_url}, skipping.")
            return

        cur.execute("""
            INSERT INTO wyber_universal_rfp_docs (
                global_notice_id, notice_id, source_name, state_code,
                file_name, file_path, blob_url, source_url,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, (
            global_notice_id,
            rfp_number,
            "Mississippi",
            "MS",
            file_name,
            local_path,
            blob_url,
            source_url or ""
        ))
        conn.commit()
        logger.info(f"Inserted document {file_name} for global_notice_id {global_notice_id}")


# ======================= AZURE BLOB UPLOAD =======================
def upload_file_to_blob(blob_service_client, container_name, local_file_path, blob_name):
    """Upload a file and return its public URL."""
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
    # 1. Load mapping JSON
    if not os.path.exists(MAPPING_JSON_PATH):
        logger.error(f"Mapping JSON not found: {MAPPING_JSON_PATH}")
        return
    with open(MAPPING_JSON_PATH, 'r', encoding='utf-8') as f:
        mapping = json.load(f)
    report = mapping.get("report", [])
    if not report:
        logger.warning("No report entries found.")
        return

    # 2. Azure Blob connection
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(AZURE_CONTAINER_NAME)
        if not container_client.exists():
            container_client.create_container()
            logger.info(f"Created container {AZURE_CONTAINER_NAME}")
    except Exception as e:
        logger.error(f"Azure Blob connection failed: {e}")
        return

    # 3. PostgreSQL connection and schema prep
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = False
        ensure_blob_folder_column(conn)   # safe add
    except Exception as e:
        logger.error(f"PostgreSQL setup failed: {e}")
        return

    # 4. Process each RFP entry
    for entry in report:
        rfp_number = entry.get("rfp_number")
        bid_id = entry.get("bid_id")
        detail_page_url = entry.get("detail_page_url")
        scraped_from_website = entry.get("scraped_from_website")
        downloaded_files = entry.get("downloaded_files", [])

        if not downloaded_files:
            logger.info(f"No files for RFP {rfp_number}")
            continue

        # Determine blob folder name
        folder_key = str(bid_id) if bid_id is not None else f"rfp_{rfp_number}"
        blob_folder_prefix = f"mississippi/{folder_key}"
        blob_folder_url = f"https://{blob_service_client.account_name}.blob.core.windows.net/{AZURE_CONTAINER_NAME}/{blob_folder_prefix}/"

        # Find or create RFP record
        try:
            global_notice_id = find_or_create_rfp(
                conn, bid_id, rfp_number, detail_page_url,
                scraped_from_website, blob_folder_url
            )
        except Exception as e:
            logger.error(f"Error finding/creating RFP for {rfp_number}: {e}")
            conn.rollback()
            continue

        # Upload each file and insert document record
        for file_info in downloaded_files:
            file_name = file_info.get("file_name")
            local_path = file_info.get("file_path")
            if not os.path.exists(local_path):
                logger.warning(f"File missing: {local_path}")
                continue

            # Get source URL (first direct_download_url if any)
            source_url = None
            if file_info.get("direct_download_urls") and len(file_info["direct_download_urls"]) > 0:
                source_url = file_info["direct_download_urls"][0].get("direct_download_url")

            blob_name = f"{blob_folder_prefix}/{file_name}"
            try:
                blob_url = upload_file_to_blob(blob_service_client, AZURE_CONTAINER_NAME,
                                               local_path, blob_name)
                add_document(conn, global_notice_id, rfp_number, file_name,
                             blob_url, local_path, source_url)
            except Exception as e:
                logger.error(f"Error processing {local_path}: {e}")
                conn.rollback()
                continue

    conn.close()
    logger.info("All processing completed.")


if __name__ == "__main__":
    main()