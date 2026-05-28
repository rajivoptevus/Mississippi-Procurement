"""
Compare files on disk vs files in Azure blob and DB docs table.
"""
import json, re
from pathlib import Path
from azure.storage.blob import BlobServiceClient

AZURE_CONN_STR = (
    "DefaultEndpointsProtocol=https;"
    "AccountName=rfpsources;"
    "AccountKey="XXXXXXXX"
    "EndpointSuffix=core.windows.net"
)
AZURE_CONTAINER = "rfp-attachments"
SCRAPED_DIR     = Path(r"C:\Scraping\Mississippi-Procurement\scraped_data")
SUPPORTED_EXT   = {".pdf", ".docx", ".doc", ".xlsx", ".xls", ".zip"}

# ── Count files on disk ────────────────────────────────────────
disk_files = []
for bid_dir in sorted(SCRAPED_DIR.glob("BidID_*")):
    for f in bid_dir.iterdir():
        if f.suffix.lower() in SUPPORTED_EXT:
            disk_files.append(f)

print(f"Files on disk : {len(disk_files)}")

# By extension
from collections import Counter
ext_counts = Counter(f.suffix.lower() for f in disk_files)
for ext, cnt in sorted(ext_counts.items()):
    print(f"  {ext:8} {cnt}")

# ── Count blobs in Azure under mississippi/ ────────────────────
svc = BlobServiceClient.from_connection_string(AZURE_CONN_STR)
container = svc.get_container_client(AZURE_CONTAINER)

blob_names = set()
folder_counts = Counter()
for blob in container.list_blobs(name_starts_with="mississippi/"):
    blob_names.add(blob.name)
    # folder = mississippi/{rfx_type}/{bid_folder}/
    parts = blob.name.split("/")
    if len(parts) >= 3:
        folder_counts[parts[1]] += 1

print(f"\nBlobs in Azure: {len(blob_names)}")
print("By RFx type folder:")
for folder, cnt in sorted(folder_counts.items()):
    print(f"  {folder:40} {cnt}")

# ── Find disk files NOT in Azure ───────────────────────────────
import psycopg2, sys
sys.path.insert(0, str(Path(__file__).parent))
import config as _cfg

conn = psycopg2.connect(
    host=_cfg.DB_HOST, port=_cfg.DB_PORT, dbname=_cfg.DB_NAME,
    user=_cfg.DB_USER, password=_cfg.DB_PASSWORD, sslmode=_cfg.DB_SSLMODE
)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM wyber_universal_rfp_docs_expt_1 WHERE source_name = 'Mississippi Procurement Portal'")
print(f"\nDB doc rows   : {cur.fetchone()[0]}")

cur.execute("""
    SELECT file_path FROM wyber_universal_rfp_docs_expt_1
    WHERE source_name = 'Mississippi Procurement Portal'
""")
db_paths = {r[0] for r in cur.fetchall()}
print(f"Distinct file_paths in DB: {len(db_paths)}")

conn.close()

# ── Summary ────────────────────────────────────────────────────
missing_from_azure = len(disk_files) - len(blob_names)
print(f"\nFiles on disk not yet in Azure: ~{missing_from_azure}")
print(f"DB rows vs disk files gap     : {len(disk_files) - len(db_paths)}")
