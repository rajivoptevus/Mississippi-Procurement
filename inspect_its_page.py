import requests
from bs4 import BeautifulSoup

URL = 'https://www.its.ms.gov/procurement/RFPs_and_sole_sources_advertised'

resp = requests.get(URL, timeout=30)
resp.raise_for_status()
print('status', resp.status_code)
print('url', URL)

soup = BeautifulSoup(resp.text, 'html.parser')

# Print all RFP number-like links and matching 4733 items.
items = []
for a in soup.find_all('a', href=True):
    text = a.get_text(strip=True)
    if '4733' in text or '4733' in a['href']:
        items.append((text, a['href']))

print('4733 link count:', len(items))
for text, href in items:
    print(text, href)
