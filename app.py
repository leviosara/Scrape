import requests
from urllib.parse import urlparse
import trafilatura
import dateparser
import pandas as pd
from datetime import datetime, date, timedelta
import xml.etree.ElementTree as ET

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7

# --- CORE FUNCTIONS ---

def get_sitemap_urls(base_url):
    parsed = urlparse(base_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    sm_paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml"]
    sitemaps = []
    found_urls = []
    
    # 1. Find sitemap
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(domain + path)
                break
        except: continue

    # Fallback to robots.txt
    if not sitemaps:
        try:
            r = requests.get(domain + "/robots.txt", timeout=5)
            for line in r.text.split('\n'):
                if 'Sitemap:' in line:
                    sitemaps.append(line.split('Sitemap:')[1].strip())
        except: pass

    # 2. Parse Sitemap
    for sm in sitemaps:
        try:
            r = requests.get(sm, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            # Handle Index (list of sitemaps)
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    loc = [c.text for c in child if 'loc' in c.tag.lower()]
                    if loc:
                        try:
                            r2 = requests.get(loc[0], timeout=5)
                            root2 = ET.fromstring(r2.content)
                            parse_sitemap_xml(root2, found_urls)
                        except: pass
            else:
                parse_sitemap_xml(root, found_urls)
        except: continue
            
    return found_urls

def parse_sitemap_xml(root, url_list):
    cutoff = date.today() - timedelta(days=DAYS_TO_SCAN)
    for child in root:
        loc = None
        last_mod = None
        for c in child:
            if 'loc' in c.tag.lower(): loc = c.text
            if 'lastmod' in c.tag.lower(): last_mod = c.text
        
        if loc and last_mod:
            try:
                dt = dateparser.parse(last_mod).date()
                if dt >= cutoff:
                    url_list.append({'url': loc, 'date': dt})
            except: pass

def analyze_site(site_url):
    print(f"\n[*] Scanning: {site_url}")
    
    # 1. Get URLs
    candidates = get_sitemap_urls(site_url)
    
    # Fallback RSS
    if not candidates:
        print("[*] Trying RSS...")
        parsed = urlparse(site_url)
        paths = ['/feed', '/rss', '/feed.xml']
        for p in paths:
            try:
                r = requests.get(f"{parsed.scheme}://{parsed.netloc}{p}", timeout=5)
                if '<rss' in r.text:
                    import feedparser
                    feed = feedparser.parse(r.text)
                    cutoff = date.today() - timedelta(days=DAYS_TO_SCAN)
                    for e in feed.entries:
                        if hasattr(e, 'published_parsed'):
                            dt = datetime(*e.published_parsed[:6]).date()
                            if dt >= cutoff:
                                candidates.append({'url': e.link, 'date': dt})
                    break
            except: pass

    print(f"[*] Found {len(candidates)} links from last {DAYS_TO_SCAN} days.")

    # 2. Analyze Content
    data = []
    for i, item in enumerate(candidates[:50]): # Limit 50 for speed
        if any(x in item['url'].lower() for x in ['.jpg', '.png', '.pdf']):
            continue
        
        print(f"    Processing {i+1}...", end='\r')
        try:
            downloaded = trafilatura.fetch_url(item['url'])
            if downloaded:
                text = trafilatura.extract(downloaded, favor_precision=True)
                if text and len(text) > 150:
                    data.append({
                        'date': item['date'],
                        'chars': len(text),
                        'tokens': len(text) // 4
                    })
        except: pass
        
    return pd.DataFrame(data)

# --- MAIN INPUT LOOP ---
print("="*40)
print(" UNIVERSAL WEBSITE ANALYZER ")
print("="*40)

# This part asks YOU for the link
url_input = input("Enter the website URL to analyze: ").strip()

if url_input:
    df = analyze_site(url_input)
    if not df.empty:
        # Report
        daily = df.groupby('date').agg(
            Articles=('chars', 'count'),
            Total_Chars=('chars', 'sum'),
            Total_Tokens=('tokens', 'sum')
        ).reset_index()
        
        avg_art = daily['Articles'].mean()
        avg_tok = daily['Total_Tokens'].mean()
        
        print(f"\nRESULTS FOR: {url_input}")
        print(f"Average Articles/Day: {avg_art:.1f}")
        print(f"Average Tokens/Day:   {int(avg_tok):,}")
        print("\nDaily Breakdown:")
        print(daily.sort_values('date', ascending=False))
    else:
        print("\n[!] No content found for this site.")
else:
    print("No URL entered.")
