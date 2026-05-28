import argparse
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = 'https://www.ms.gov'
FILE_EXTENSIONS = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.txt'}

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
    'Referer': BASE_URL,
})

def normalize_url(href: str, base: str) -> str:
    href = href.strip()
    if not href:
        return ''
    if href.startswith('//'):
        return 'https:' + href
    return urljoin(base, href)


def is_download_link(href: str) -> bool:
    if not href:
        return False
    path = urlparse(href).path.lower()
    return any(path.endswith(ext) for ext in FILE_EXTENSIONS)


def extract_links(html: str, base: str):
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = normalize_url(a['href'], base)
        if not href:
            continue
        if is_download_link(href):
            links.append((a.get_text(strip=True), href))
    return links


def extract_rfp_numbers(html: str, base: str):
    soup = BeautifulSoup(html, 'html.parser')
    rfp_numbers = []
    for a in soup.find_all('a', href=True):
        href = normalize_url(a['href'], base)
        if 'Admin/Rfp?rfpNumber=' in href:
            match = re.search(r'\brfpNumber=(\d+)', href)
            if match:
                rfp_numbers.append(match.group(1))
    return sorted(set(rfp_numbers), key=int)


def find_listing_pages(html: str, base: str):
    soup = BeautifulSoup(html, 'html.parser')
    pages = {base}
    for a in soup.find_all('a', href=True):
        href = normalize_url(a['href'], base)
        if not href or href == base:
            continue
        if re.search(r'(page=|p=|start=|offset=|listing=)', href, re.I):
            pages.add(href)
        elif a.get_text(strip=True).isdigit() and base in href:
            pages.add(href)
    return sorted(pages)


def download_file(url: str, dest: Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        print(f'Skipping existing file: {dest.name}')
        return
    print(f'Downloading {url} -> {dest}')
    with session.get(url, stream=True, timeout=30) as r:
        r.raise_for_status()
        with open(dest, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]', '_', name)


def download_rfp_files(rfp_number: str, output_dir: Path):
    page_url = f'https://www.ms.gov/its/suite/Admin/Rfp?rfpNumber={rfp_number}'
    print('\nFetching', page_url)
    resp = session.get(page_url, timeout=30)
    resp.raise_for_status()

    links = extract_links(resp.text, BASE_URL)
    if not links:
        print(f'No downloadable file links found for RFP {rfp_number}.')
        return 0

    target_dir = output_dir / str(rfp_number)
    print(f'Found {len(links)} files for RFP {rfp_number}. Saving into {target_dir.absolute()}')

    downloaded = 0
    for label, link in links:
        filename = Path(urlparse(link).path).name
        if not filename:
            filename = sanitize_filename(label)[:120] or f'{rfp_number}_file'
        dest = target_dir / filename
        download_file(link, dest)
        downloaded += 1
    return downloaded


def main():
    parser = argparse.ArgumentParser(description='Download all file URLs from the ITS RFP listing')
    parser.add_argument('--list-url', default='https://www.ms.gov/its/suite/Admin/rfps_awaiting',
                        help='RFP listing page URL')
    parser.add_argument('--output-dir', default='downloaded_rfps', help='Directory to save files')
    args = parser.parse_args()

    print('Fetching RFP listing page:', args.list_url)
    resp = session.get(args.list_url, timeout=30)
    resp.raise_for_status()

    pages = find_listing_pages(resp.text, args.list_url)
    if len(pages) > 1:
        print('Detected listing pages:', pages)

    rfp_numbers = []
    for page_url in pages:
        print('Scanning listing page:', page_url)
        page_resp = session.get(page_url, timeout=30)
        page_resp.raise_for_status()
        page_rfps = extract_rfp_numbers(page_resp.text, page_url)
        if page_rfps:
            rfp_numbers.extend(page_rfps)
        else:
            print('  No RFP numbers found on this page.')

    rfp_numbers = sorted(set(rfp_numbers), key=int)
    if not rfp_numbers:
        print('No RFP numbers found on the listing pages.')
        return

    print(f'Found {len(rfp_numbers)} RFPs across listing pages: {", ".join(rfp_numbers)}')
    output_dir = Path(args.output_dir)
    total_downloaded = 0

    for rfp_number in rfp_numbers:
        total_downloaded += download_rfp_files(rfp_number, output_dir)

    print(f'\nDone. Downloaded {total_downloaded} files total to {output_dir.absolute()}')


if __name__ == '__main__':
    main()
