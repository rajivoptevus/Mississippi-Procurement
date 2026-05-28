"""
Collect all RFP documents from scraped_data into one directory.
Organizes by RFx type and names files with BidID + Smart Number prefix.

OUTPUT:
    all_rfp_documents/
        Request_for_Proposal/
            BidID_38017_1601-25-R-RFPR-00005_001_General_RFP_No_3850.pdf
        Invitation_for_Bid/
            BidID_44934_XXXX_001_Bid_Ad.pdf
        MDA_RFx/
            ...
        all_documents_flat/        ← all files in one flat folder
"""

import json
import shutil
import re
from pathlib import Path

SCRAPED_DIR  = Path(r"C:\Scraping\Mississippi-Procurement\scraped_data")
OUTPUT_DIR   = Path(r"C:\Scraping\Mississippi-Procurement\all_rfp_documents")
FLAT_DIR     = OUTPUT_DIR / "all_documents_flat"

# Files to skip (not real RFP documents)
SKIP_PATTERNS = [
    "details.json",
    "resolved_001_Mississippi AI",
    "resolved_002_Acceptable Use Policy",
    "resolved_003_Appropriate and Acceptable",
    "resolved_004_Domain Name",
    "resolved_005_Procurement Handbook",
    "resolved_006_Strategic Master Plan",
    "resolved_007_State Data Centers",
    "resolved_008_ITS Annual Report",
    "resolved_009_Enterprise Cybersecurity",
    "resolved_010_ITS Services Catalog",
    "resolved_011_IT Planning",
]


def sanitize(name: str, max_len: int = 120) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', str(name))[:max_len]


def should_skip(filename: str) -> bool:
    for pattern in SKIP_PATTERNS:
        if pattern.lower() in filename.lower():
            return True
    return False


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    FLAT_DIR.mkdir(parents=True, exist_ok=True)

    bid_dirs = sorted(SCRAPED_DIR.glob("BidID_*"))
    print(f"Found {len(bid_dirs)} BidID folders")

    total_copied = 0
    total_skipped = 0
    summary = []

    for bid_dir in bid_dirs:
        bid_id = bid_dir.name.replace("BidID_", "")

        # Load details.json
        details_file = bid_dir / "details.json"
        smart_number = ""
        rfx_type     = "Unknown"
        agency       = ""

        if details_file.exists():
            try:
                data = json.loads(details_file.read_text(encoding="utf-8"))
                smart_number = data.get("smart_number", "")
                rfx_type     = data.get("rfx_type", "Unknown") or "Unknown"
                agency       = data.get("agency", "")
            except Exception:
                pass

        # Create type-specific subfolder
        type_folder = OUTPUT_DIR / sanitize(rfx_type.replace(" ", "_").replace("-", "_"))
        type_folder.mkdir(parents=True, exist_ok=True)

        # Find all PDF files in this BidID folder
        pdf_files = [f for f in bid_dir.iterdir()
                     if f.suffix.lower() == ".pdf"
                     and not should_skip(f.name)]

        if not pdf_files:
            continue

        bid_summary = {
            "bid_id":       bid_id,
            "smart_number": smart_number,
            "rfx_type":     rfx_type,
            "agency":       agency,
            "files":        [],
        }

        for pdf in sorted(pdf_files):
            # Build destination filename: BidID_XXXXX_SmartNumber_original_name.pdf
            prefix = f"BidID_{bid_id}"
            if smart_number:
                prefix += f"_{sanitize(smart_number)}"

            dest_name = f"{prefix}_{pdf.name}"
            dest_name = sanitize(dest_name, max_len=200)

            # Copy to type folder
            dest_type = type_folder / dest_name
            if not dest_type.exists():
                shutil.copy2(pdf, dest_type)

            # Copy to flat folder
            dest_flat = FLAT_DIR / dest_name
            if not dest_flat.exists():
                shutil.copy2(pdf, dest_flat)

            bid_summary["files"].append(dest_name)
            total_copied += 1
            print(f"  ✓ [{rfx_type}] {dest_name[:80]}")

        summary.append(bid_summary)

    # Save summary JSON
    summary_file = OUTPUT_DIR / "collection_summary.json"
    summary_file.write_text(
        json.dumps({
            "total_bids_with_docs": len(summary),
            "total_files_copied":   total_copied,
            "bids": summary,
        }, indent=2),
        encoding="utf-8"
    )

    print(f"\n{'='*60}")
    print(f"DONE")
    print(f"  Total files copied : {total_copied}")
    print(f"  Output (by type)   : {OUTPUT_DIR}")
    print(f"  Output (flat)      : {FLAT_DIR}")
    print(f"  Summary            : {summary_file}")
    print("=" * 60)

    # Print breakdown by type
    from collections import Counter
    type_counts = Counter(b["rfx_type"] for b in summary)
    print("\nFiles by RFx type:")
    for t, c in type_counts.most_common():
        print(f"  {c:3d} bids — {t}")


if __name__ == "__main__":
    main()
