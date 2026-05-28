Mississippi Procurement Scraper - Sequence of Operations

Overview :-  This project scrapes procurement opportunities (RFPs, RFQs, IFBs) from the Mississippi 
             Procurement Portal and ITS Mississippi RFP pages, stores documents in Azure Blob Storage, and maintains records in a PostgreSQL database.

Mississippi Site :- https://www.ms.gov/dfa/contract_bid_search/Bid?autoloadGrid=true
                    https://www.ms.gov/dfa/contract_bid_search/Bid/Details/45288?AppId=1

________________________________________

Execution Sequence & Results

Step       File                       Status       Purpose 
1     `scraper.py`                    Success    Created scraped_data/BidID_XXXXX/details.json
2     `ms_procurement_downloader.py`  Success    Download extra PDF URLs
3     `url_attachment_resolver.py`    Success    Resolve embedded PDF links

Output: 115 RFP rows, 289 doc rows.



Approx 1,200+ bid records with attachments in scraped_data/

Phase 2: Database & Azure Setup
Step	File	    Purpose	                                                    Result
4	    check_db.py	Diagnostic - check UUID rows and source distribution	Informational only
5	    inspect_schema.py	Check table schemas in database	                Informational only
6	    duplicate_tables.py	Create _expt_1 copy tables for safe testing	    Successful
                                                                            -Created Experimental Tables                               
Phase 3: Data Cleanup & ID Fixes
Step	File	                 Purpose	                                  Result
7	    delete_uuid_rows.py	    Remove bad UUID rows with NULL source_name	 Successful-Cleaned    
                                                                             corrupted data
8	    db_fix.py	            Fix global_notice_id pattern                 Successful-Correct
                                + upload all files to Azure + upsert records ID
                                                                             pattern: ms_gov_dfa_contract_bid_search_{bid_id}
9	    reset_ids.py	        Re-number id columns from 1	                 Successful - Clean 
                                                                             sequential IDs

Final ID Pattern:
ms_gov_dfa_contract_bid_search_38017  (global_notice_id for bid_id=38017)


Phase 4: Data Migration to Production
Step	File	                Purpose	                                        Result
10	    merge_to_production.py	Merge experimental tables → production tables	Successful -        
                                                                                Upserted all data to wyber_universal_rfps and wyber_universal_rfp_docs

Supporting/Utility Files

These files were used for specific tasks or diagnostics:
File	                    Purpose	                                               Result
collect_rfps.py	            Organize all RFP documents into single directory	   
                                                                               Utility-Created      
                                                                               all_rfp_documents/
download_its_rfp_docs.py	Download ITS RFP documents from listing page	   Limited - Needs page 
                                                                               structure update
generate_download_report.py	Map downloaded RFP folders to bid IDs	           Utility - Created 
                                                                               mapping JSON
mapping_rfid_bidids.py	    Create mapping between RFP numbers and BidIDs	   Utility - Used for 
                                                                               document matching
upload_rfps_final.py	    Upload documents to Azure from mapping report	   Successful - Final 
                                                                               uploader
upload_rfps_to_azure.py	    Alternative uploader	                           Functional - Used as 
                                                                               fallback
reset_docs.py	            Delete MS doc rows before re-upload	               Cleanup utility
reset_sequence.py	        Check/reset PostgreSQL sequences	               Utility
check_uploads.py	        Compare disk vs Azure vs DB	                       Diagnostic
ingest_ms_rfp.py	        Direct JSON ingestion script	                   Deprecated - Replaced 
                                                                                by db_fix.py
scan_and_scrape_rfp.py	    Scan local files for RFP content	               Utility
test_its_page.py	        Test ITS Mississippi page scraping	              Diagnostic
inspect_its_page.py	        Inspect ITS page structure	                      Diagnostic

________________________________________

Final Data Locations
Location	                                Content	
scraped_data/	                            All bid JSON files + downloaded attachments (PDF, DOCX, 
                                            XLSX)	
all_rfp_documents/	                        Organized copies of all RFP documents by type	
Azure Blob: rfp-attachments/mississippi/	All documents in blob storage	
PostgreSQL: wyber_universal_rfps	        Main RFP records (production table)	
PostgreSQL: wyber_universal_rfp_docs	    Document records with blob URLs	
________________________________________

Verified Working Files
These files produced the correct final data:
1.	scraper.py              - Primary data collection 
2.	db_fix.py               - Full cleanup + Azure upload + DB upsert 
3.	merge_to_production.py  - Merge to production tables 
4.	upload_rfps_final.py    - Alternative uploader 
________________________________________

Database Schema (Target Tables)

Table	                        Key Columns

wyber_universal_rfps	        global_notice_id (PK), notice_id, bid_id, solicitation_number, 
                                source_name
wyber_universal_rfp_docs	    global_notice_id, blob_url, file_name

Unique constraint: (global_notice_id, file_name)
________________________________________

Running the Complete Pipeline

# 1. Scrape data
python scraper.py

# 2. Resolve URL-only PDFs (if needed)
python url_attachment_resolver.py

# 3. Upload to Azure + DB (clean run)
python db_fix.py

# 4. Merge to production
python merge_to_production.py --source "Mississippi Procurement Portal"


Download Log (2026-05-22):-

    Found 115 BidID directories to process

        o	Starting Chrome browser...
                    ====== WebDriver manager ======
        o	Get LATEST chromedriver version for google-chrome
        o	Get LATEST chromedriver version for google-chrome
        o	There is no [win64] chromedriver "148.0.7778.178" for browser google-chrome "148.0.7778" 
            in cache
        o	Get LATEST chromedriver version for google-chrome
        o	WebDriver version 148.0.7778.178 selected
        o	Modern chrome version https://storage.googleapis.com/chrome-for-testing-public/148.0.
                                  7778.178/win32/chromedriver-win32.zip
        o	About to download new driver from https://storage.googleapis.com/
                                chrome-for-testing-public/148.0.7778.178/win32/chromedriver-win32.zip
        o	Driver downloading response is 200
        o	Get LATEST chromedriver version for google-chrome
        o	Driver has been saved in cache [C:\Users\Rajiv Patnaik S\.  
                                            wdm\drivers\chromedriver\win64\148.0.7778.178]

project/
 ├── scraper.py
 ├── README.md
 ├── logs/
 │    └── output.log
 ├── data/
 │    └── file.json
