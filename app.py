import streamlit as st
import requests
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import pandas as pd
import trafilatura
import dateparser
from datetime import datetime, timedelta

# --- CONFIGURATION ---
DAYS_TO_SCAN = 1  # Only get articles from the last 24 hours
MAX_URLS_TO_CHECK = 50  # Safety limit for checking content manually

# --- HELPER FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

# --- CORE SCRAPER LOGIC ---

def get_recent_articles(base_url):
    parsed = urlparse(base_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    sm_paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml"]
    sitemaps = []
    articles = []
    
    # 1. Find sitemap
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(domain + path)
                break
        except: continue

    if not sitemaps:
        try:
            r = requests.get(domain + "/robots.txt", timeout=5)
            for line in r.text.split('\n'):
                if 'Sitemap:' in line:
                    sitemaps.append(line.split('Sitemap:')[1].strip())
        except: pass

    if not sitemaps:
        return []

    # 2. Parse Sitemap and Extract Dates
    processed_sitemaps = set()
    cutoff_date = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    
    # Progress bar for UI
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    urls_to_check_count = 0

    while sitemaps:
        sm = sitemaps.pop(0)
        if sm in processed_sitemaps: continue
        processed_sitemaps.add(sm)

        try:
            r = requests.get(sm, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            # Handle Sitemap Index (list of other sitemaps)
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs:
                        if loc: sitemaps.append(loc)
            
            # Handle URL Set (list of pages)
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    loc = None
                    lastmod = None
                    
                    # Extract URL and LastMod date
                    for c in child:
                        if 'loc' in str(c.tag).lower():
                            loc = c.text
                        if 'lastmod' in str(c.tag).lower():
                            lastmod = c.text
                    
                    if not loc: continue
                    
                    # STRATEGY 1: Check Date from Sitemap (Fast)
                    if lastmod:
                        try:
                            art_date = dateparser.parse(lastmod)
                            if art_date and art_date > cutoff_date:
                                articles.append({
                                    'url': loc,
                                    'date': art_date,
                                    'source': 'Sitemap Date'
                                })
                        except: pass
                    
                    # STRATEGY 2: If no date in sitemap, queue for content check (Slow)
                    # We limit this to prevent timeouts
                    elif urls_to_check_count < MAX_URLS_TO_CHECK:
                        urls_to_check_count += 1
                        status_text.text(f"Checking content for: {loc[:50]}...")
                        
                        try:
                            html = trafilatura.fetch_url(loc)
                            if html:
                                metadata = trafilatura.extract_metadata(html)
                                if metadata and metadata.date:
                                    art_date = dateparser.parse(metadata.date)
                                    if art_date and art_date > cutoff_date:
                                        articles.append({
                                            'url': loc,
                                            'date': art_date,
                                            'source': 'Page Content'
                                        })
                        except: pass
                    
                    # Update progress bar loosely
                    progress_bar.progress(min(len(articles) / 10, 1.0))

        except Exception as e:
            continue
            
    progress_bar.empty()
    status_text.empty()
    return articles

# --- STREAMLIT UI ---

st.set_page_config(page_title="Daily News Scraper", layout="wide")

st.title("📰 Daily News & Article Finder")
st.write(f"Scans a website's sitemap to find articles posted in the last **{DAYS_TO_SCAN} day(s)**.")

url_input = st.text_input("Website URL", placeholder="example.com")

if st.button("Find Recent Articles"):
    if url_input:
        clean_input = clean_url(url_input)
        st.info(f"Scanning: `{clean_input}`...")
        st.warning("⏳ This may take a moment if the sitemap doesn't provide dates directly...")
        
        try:
            results = get_recent_articles(clean_input)
            
            if results:
                st.success(f"Found {len(results)} recent articles!")
                
                # Sort by date
                results.sort(key=lambda x: x['date'], reverse=True)
                
                # Display results
                for item in results:
                    st.markdown(f"### [{item['url']}]({item['url']})")
                    st.caption(f"📅 {item['date'].strftime('%Y-%m-%d %H:%M')} | Source: {item['source']}")
                    st.divider()
                
                # Download CSV
                df = pd.DataFrame(results)
                df['date'] = df['date'].astype(str)
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("Download Results as CSV", csv, "recent_articles.csv", "text/csv")
                
            else:
                st.warning("No articles found in the last 24 hours. The site might not have posted recently, or the sitemap lacks date information.")
                
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.error("Please enter a URL.")
