
# # output ...  rfp_download_report.json
# import os
# import re
# import json
# from pathlib import Path
# from urllib.parse import urlparse

# # ============================================================
# # CONFIGURATION
# # ============================================================

# # Folder containing downloaded RFP documents
# DOWNLOADED_DIR = r"C:\Scraping\Mississippi-Procurement\downloaded_rfps"

# # Folder containing scraped bid JSON/data
# SCRAPED_DATA_DIR = r"C:\Scraping\Mississippi-Procurement\scraped_data"

# # Output report
# OUTPUT_JSON = r"C:\Scraping\Mississippi-Procurement\rfp_download_report.json"

# # ============================================================
# # HELPER FUNCTIONS
# # ============================================================

# def extract_urls(obj, found=None):
#     """
#     Recursively extract URLs from nested JSON/dict/list structures.
#     """
#     if found is None:
#         found = []

#     if isinstance(obj, dict):
#         for k, v in obj.items():

#             # If key suggests URL/link
#             if isinstance(v, str):
#                 if v.startswith("http://") or v.startswith("https://"):
#                     found.append((k, v))

#             extract_urls(v, found)

#     elif isinstance(obj, list):
#         for item in obj:
#             extract_urls(item, found)

#     return found


# def identify_parent_url(url):
#     """
#     Create parent website URL from direct download URL.
#     """
#     try:
#         parsed = urlparse(url)
#         return f"{parsed.scheme}://{parsed.netloc}"
#     except:
#         return None


# def find_bidid_from_filename(name):
#     """
#     Try extracting BidID or RFP number from filename/folder.
#     Examples:
#         Bidid_38017
#         rfp_4599
#         BPM006081
#     """

#     patterns = [
#         r"Bidid[_\-]?(\d+)",
#         r"bid[_\-]?(\d+)",
#         r"rfp[_\-]?(\d+)",
#         r"RFP[_\-]?(\d+)",
#         r"(\d{3,})"
#     ]

#     for p in patterns:
#         m = re.search(p, name, re.IGNORECASE)
#         if m:
#             return m.group(1)

#     return None


# # ============================================================
# # STEP 1: BUILD MAPPING FROM SCRAPED DATA
# # ============================================================

# print("\nBuilding bid mapping from scraped_data folder...")

# bid_mapping = {}

# for root, dirs, files in os.walk(SCRAPED_DATA_DIR):

#     for file in files:

#         if file.lower().endswith(".json"):

#             json_path = os.path.join(root, file)

#             try:
#                 with open(json_path, "r", encoding="utf-8") as f:
#                     data = json.load(f)

#                 # Extract all URLs
#                 urls = extract_urls(data)

#                 # Try identifying bid number from:
#                 # - filename
#                 # - path
#                 # - JSON content

#                 combined_text = json.dumps(data)

#                 bidid = (
#                     find_bidid_from_filename(file)
#                     or find_bidid_from_filename(root)
#                     or find_bidid_from_filename(combined_text)
#                 )

#                 if not bidid:
#                     continue

#                 if bidid not in bid_mapping:
#                     bid_mapping[bidid] = {
#                         "json_source_file": json_path,
#                         "urls": []
#                     }

#                 for key, url in urls:

#                     entry = {
#                         "field_name": key,
#                         "url": url,
#                         "parent_website": identify_parent_url(url)
#                     }

#                     if entry not in bid_mapping[bidid]["urls"]:
#                         bid_mapping[bidid]["urls"].append(entry)

#             except Exception as e:
#                 print(f"Error reading {json_path}: {e}")

# print(f"Total bid mappings found: {len(bid_mapping)}")


# # ============================================================
# # STEP 2: SCAN DOWNLOADED FILES
# # ============================================================

# print("\nScanning downloaded_rfps folder...")

# report = []

# for root, dirs, files in os.walk(DOWNLOADED_DIR):

#     for file in files:

#         file_path = os.path.join(root, file)

#         relative_path = os.path.relpath(file_path, DOWNLOADED_DIR)

#         subdirectory = Path(relative_path).parts[0] \
#             if len(Path(relative_path).parts) > 1 else "ROOT"

#         # Try extracting bid/RFP number
#         detected_id = (
#             find_bidid_from_filename(file)
#             or find_bidid_from_filename(subdirectory)
#             or find_bidid_from_filename(root)
#         )

#         matched_data = bid_mapping.get(detected_id, {})

#         urls_info = matched_data.get("urls", [])

#         # Try identifying scrape website
#         websites = set()

#         for u in urls_info:
#             websites.add(u["parent_website"])

#         report_item = {
#             "file_name": file,
#             "file_path": file_path,
#             "subdirectory": subdirectory,
#             "detected_bid_or_rfp_id": detected_id,
#             "source_json_file": matched_data.get("json_source_file"),

#             "scraped_websites": sorted(list(websites)),

#             "download_links": urls_info
#         }

#         report.append(report_item)


# # ============================================================
# # STEP 3: SAVE REPORT
# # ============================================================

# output_data = {
#     "downloaded_rfps_directory": DOWNLOADED_DIR,
#     "scraped_data_directory": SCRAPED_DATA_DIR,
#     "total_files_processed": len(report),
#     "report": report
# }

# with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
#     json.dump(output_data, f, indent=4, ensure_ascii=False)

# print("\n====================================================")
# print("REPORT GENERATED SUCCESSFULLY")
# print("====================================================")
# print(f"Output JSON: {OUTPUT_JSON}")
# print(f"Total files processed: {len(report)}")
# print("====================================================")


import os
import re
import json
from pathlib import Path
from urllib.parse import urlparse

# ============================================================
# CONFIG
# ============================================================

DOWNLOADED_DIR = r"C:\Scraping\Mississippi-Procurement\downloaded_rfps"
SCRAPED_DATA_DIR = r"C:\Scraping\Mississippi-Procurement\scraped_data"

OUTPUT_JSON = r"C:\Scraping\Mississippi-Procurement\rfp_bid_mapping_report.json"

# ============================================================
# HELPERS
# ============================================================

def extract_urls(obj, found=None):
    if found is None:
        found = []

    if isinstance(obj, dict):
        for k, v in obj.items():

            if isinstance(v, str):
                if v.startswith("http://") or v.startswith("https://"):
                    found.append((k, v))

            extract_urls(v, found)

    elif isinstance(obj, list):
        for item in obj:
            extract_urls(item, found)

    return found


def get_parent_website(url):
    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"
    except:
        return None


def find_rfp_numbers(text):
    """
    Extract possible RFP numbers.
    """
    nums = re.findall(r"\b\d{4,6}\b", str(text))
    return list(set(nums))


def find_bidid(text):
    """
    Find BidID_XXXXX
    """

    patterns = [
        r"Bidid[_\-]?(\d+)",
        r"bidid[_\-]?(\d+)",
        r"bid[_\-]?(\d+)"
    ]

    for p in patterns:
        m = re.search(p, str(text), re.IGNORECASE)
        if m:
            return m.group(1)

    return None


# ============================================================
# STEP 1:
# BUILD RFP -> BIDID MAP FROM scraped_data
# ============================================================

print("\nBuilding mapping from scraped_data...\n")

rfp_bid_map = {}

for root, dirs, files in os.walk(SCRAPED_DATA_DIR):

    for file in files:

        if not file.lower().endswith(".json"):
            continue

        json_path = os.path.join(root, file)

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            text_blob = json.dumps(data)

            # ------------------------------------------------
            # Find BidID
            # ------------------------------------------------

            bidid = (
                find_bidid(file)
                or find_bidid(root)
                or find_bidid(text_blob)
            )

            if not bidid:
                continue

            # ------------------------------------------------
            # Find RFP numbers
            # ------------------------------------------------

            possible_rfps = set()

            possible_rfps.update(find_rfp_numbers(file))
            possible_rfps.update(find_rfp_numbers(root))
            possible_rfps.update(find_rfp_numbers(text_blob))

            # ------------------------------------------------
            # Extract URLs
            # ------------------------------------------------

            urls = extract_urls(data)

            url_entries = []

            for key, url in urls:

                url_entries.append({
                    "field_name": key,
                    "direct_download_url": url,
                    "parent_website": get_parent_website(url)
                })

            # ------------------------------------------------
            # Store mapping
            # ------------------------------------------------

            for rfp in possible_rfps:

                if rfp not in rfp_bid_map:

                    rfp_bid_map[rfp] = {
                        "rfp_number": rfp,
                        "bidid": bidid,
                        "source_json_file": json_path,
                        "urls": url_entries
                    }

        except Exception as e:
            print(f"Error reading {json_path}: {e}")

# ============================================================
# STEP 2:
# MATCH DOWNLOADED FILES
# ============================================================

print("\nMatching downloaded files...\n")

report = []

for root, dirs, files in os.walk(DOWNLOADED_DIR):

    for file in files:

        file_path = os.path.join(root, file)

        subdirectory = Path(file_path).parts[-2]

        # IMPORTANT:
        # Use subdirectory as the PRIMARY RFP number
        # because your folders are:
        #
        # downloaded_rfps/3850/
        # downloaded_rfps/4599/
        #
        # which already represent RFP IDs.
        #

        rfp_number = subdirectory

        mapping = rfp_bid_map.get(rfp_number, {})

        report_item = {

            "rfp_number": rfp_number,

            "mapped_bidid": mapping.get("bidid"),

            "file_name": file,

            "file_path": file_path,

            "subdirectory": subdirectory,

            "source_json_file": mapping.get("source_json_file"),

            "scraped_websites": list(set([
                x["parent_website"]
                for x in mapping.get("urls", [])
            ])),

            "download_links": mapping.get("urls", [])
        }

        report.append(report_item)

# ============================================================
# STEP 3:
# SAVE REPORT
# ============================================================

output = {
    "total_files_processed": len(report),
    "report": report
}

with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=4, ensure_ascii=False)

print("\n=================================================")
print("REPORT GENERATED")
print("=================================================")
print(f"Output File: {OUTPUT_JSON}")
print("=================================================")