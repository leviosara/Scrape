import requests
from urllib.parse import urlparse, urljoin
import trafilatura
import dateparser
import pandas as pd
from datetime import datetime, date, timedelta
import xml.etree.ElementTree as ET
import re
import time

# --- CONFIGURATION ---
TARGETS = [
    "https://most.ks.ua/",
    "https://v-variant.com.ua/",
    "https://realgazeta.com.ua/",
    "https://cukr.city/"
]
DAYS_TO_SCAN = 7

# --- CORE FUNCTIONS ---

def get_sitemap_urls(base_url):
    """
    Tries to find sitemap.xml or sitemap index.
    This is the most reliable way to find 'everything'.
    """
    parsed = urlparse(base_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    # Common sitemap locations
    sm_paths = [
        "/sitemap.xml", 
        "/sitemap_index.xml", 
        "/sitemap-1.xml",
        "/news-sitemap.xml",
        "/post-sitemap.xml",
        "/sitemap.xml.gz"
    ]
    
    sitemaps = []
    found_urls = []
    
    # 1. Find sitemap file
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(domain + path)
                break
        except: continue

    if not sitemaps:
        # Try robots.txt
        try:
            r = requests.get(domain + "/robots.txt", timeout=5)
            for line in r.text.split('\n'):
                if 'Sitemap:' in line:
                    sitemaps.append(line.split('Sitemap:')[1].strip())
        except: pass

    # 2. Parse Sitemap (handles indexes and simple maps)
    for sm in sitemaps:
        try:
            r = requests.get(sm, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            # Handle Sitemap Index (list of other sitemaps)
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    loc = [c.text for c in child if 'loc' in c.tag.lower()]
                    if loc:
                        # Recursively fetch child sitemap
                        try:
                            r2 = requests.get(loc[0], timeout=5)
                            root2 = ET.fromstring(r2.content)
                            parse_sitemap_xml(root2, found_urls)
                        except: pass
            else:
                # Direct URL list
                parse_sitemap_xml(root, found_urls)
                
        except Exception as e:
            continue
            
    return found_urls

def parse_sitemap_xml(root, url_list):
    """Extracts URLs and LastMod dates from XML"""
    today = date.today()
    cutoff = today - timedelta(days=DAYS_TO_SCAN)
    
    for child in root:
        loc = None
        last_mod = None
        
        for c in child:
            if 'loc' in c.tag.lower(): loc = c.text
            if 'lastmod' in c.tag.lower(): last_mod = c.text
        
        if loc:
            # Filter Date
            if last_mod:
                try:
                    dt = dateparser.parse(last_mod).date()
                    if dt >= cutoff:
                        url_list.append({'url': loc, 'date': dt})
                except:
                    # If date is weird, include it but mark unknown
                    url_list.append({'url': loc, 'date': None})
            else:
                # No date? We might skip, or keep and check header.
                # For "Everything", let's check header later if needed, 
                # but for speed we skip sitemap items without dates unless it's recent.
                pass

def analyze_site(site_url):
    print(f"\n{'='*50}")
    print(f"DEEP SCANNING: {site_url}")
    print(f"{'='*50}")
    
    # 1. Get URLs from Sitemaps
    print("[*] Checking Sitemaps...")
    candidates = get_sitemap_urls(site_url)
    
    # 2. Fallback to RSS if Sitemap failed
    if not candidates:
        print("[!] Sitemap empty or not found. Trying RSS...")
        # Simple RSS fetch logic
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

    print(f"[*] Found {len(candidates)} links published in last {DAYS_TO_SCAN} days.")
    
    if not candidates:
        print("[-] No content found.")
        return None

    # 3. Analyze Content (Go over EVERYTHING)
    data = []
    total_links = len(candidates)
    
    for i, item in enumerate(candidates):
        url = item['url']
        
        # Skip attachments
        if any(x in url.lower() for x in ['.jpg', '.png', '.gif', '.pdf', '.zip', '.webp']):
            continue
            
        # Progress
        if i % 5 == 0:
            print(f"    Processing {i+1}/{total_links}...", end='\r')
        
        try:
            # Download
            downloaded = trafilatura.fetch_url(url)
            if not downloaded: continue
            
            # Extract Text
            text = trafilatura.extract(downloaded, favor_precision=True)
            if not text or len(text) < 150: continue # Skip short snippets
            
            # Calculate
            tokens = len(text) // 4
            
            # Verify Date if missing (use sitemap date)
            pub_date = item['date']
            
            data.append({
                'date': pub_date,
                'chars': len(text),
                'tokens': tokens,
                'url': url
            })
        except:
            pass
            
    print(f"\n[+] Successfully analyzed {len(data)} content pages.")
    return pd.DataFrame(data)

# --- EXECUTION ---
final_results = {}

for target in TARGETS:
    df = analyze_site(target)
    if df is not None and not df.empty:
        final_results[target] = df
    time.sleep(2) # Polite pause

# --- FINAL REPORT ---
print("\n\n" + "="*60)
print(" DAILY AVERAGE REPORT (LAST 7 DAYS) ")
print("="*60)

for site, df in final_results.items():
    print(f"\nSITE: {site}")
    
    # Fill missing dates with mode or 'Unknown'
    df['date'] = df['date'].fillna(date.today()) # just in case
    
    # Aggregate by Date
    daily = df.groupby('date').agg(
        Articles=('chars', 'count'),
        Total_Chars=('chars', 'sum'),
        Total_Tokens=('tokens', 'sum')
    ).reset_index()
    
    # Calculate Averages
    avg_articles = daily['Articles'].mean()
    avg_chars = daily['Total_Chars'].mean()
    avg_tokens = daily['Total_Tokens'].mean()
    
    print(f" - Average Articles per Day: {avg_articles:.1f}")
    print(f" - Average Chars per Day:    {int(avg_chars):,}")
    print(f" - Average Tokens per Day:   {int(avg_tokens):,}")
    
    print(" Daily Breakdown:")
    print(daily.sort_values('date', ascending=False).to_string(index=False))
