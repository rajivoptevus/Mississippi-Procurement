"""Test scraping the ITS Mississippi RFP page."""
import asyncio
import re
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        print("Loading page...")
        await page.goto(
            'https://www.its.ms.gov/procurement/RFPs_and_sole_sources_advertised',
            wait_until='domcontentloaded',
            timeout=20000
        )
        await asyncio.sleep(5)

        # Get all links
        links = await page.eval_on_selector_all(
            'a[href]',
            'els => els.map(e => ({text: e.textContent.trim(), href: e.href}))'
        )
        print(f'Total links: {len(links)}')

        # Find PDF links
        pdfs = [l for l in links if '.pdf' in l['href'].lower()]
        print(f'PDF links: {len(pdfs)}')
        for l in pdfs[:15]:
            print(f"  PDF: {repr(l['text'][:60])} -> {l['href'][:100]}")

        # Find links with RFP numbers (like "No. 3850", "3850.pdf")
        rfp_links = [l for l in links if re.search(
            r'No\.\s*\d{3,5}|RFP\s*\d{3,5}|\d{4}\.pdf|rfp.*\d{3,5}',
            l['text'] + l['href'], re.I
        )]
        print(f'\nRFP-specific links: {len(rfp_links)}')
        for l in rfp_links[:15]:
            print(f"  RFP: {repr(l['text'][:60])} -> {l['href'][:100]}")

        # Get page title and first 2000 chars of text
        title = await page.title()
        print(f'\nPage title: {title}')

        body_text = await page.eval_on_selector('body', 'el => el.innerText')
        print(f'\nPage text (first 1000 chars):\n{body_text[:1000]}')

        await browser.close()

asyncio.run(test())
