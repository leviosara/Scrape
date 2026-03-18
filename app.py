import streamlit as st
import requests
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET
import pandas as pd
import trafilatura
import dateparser
import re
from datetime import datetime, timedelta
import feedparser

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7
MAX_CONTENT_CHECKS = 500
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

# --- HELPER FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url.rstrip('/')

def make_naive(dt):
    if dt is None: return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def find_date_in_url(url):
    patterns = [r'/(\d{4})/(\d{1,2})/(\d{1,2})/', r'/(\d{4})-(\d{1,2})-(\d{1,2})']
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            try: return dateparser.parse(date_str)
            except: pass
    return None

def get_date_from_headers(url):
    """Check HTTP headers for Last-Modified (Fast)"""
    try:
        r = requests.head(url, timeout=5, headers={'User-Agent': USER_AGENT}, allow_redirects=True)
        if 'Last-Modified' in r.headers:
            return dateparser.parse(r.headers['Last-Modified'])
    except: pass
    return None

def get_date_from_html(html):
    if not html: return None
    # 1. Trafilatura
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata and metadata.date:
            return dateparser.parse(metadata.date)
    except: pass
    # 2. JSON-LD
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if match: return dateparser.parse(match.group(1))
    # 3. Meta OpenGraph
    match = re.search(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']', html)
    if match: return dateparser.parse(match.group(1))
    return None

# --- SCANNING STRATEGIES ---

def scan_rss_feeds(base_url, status_placeholder):
    """Strategy 1: Check RSS Feeds (Best for dates)"""
    status_placeholder.text("Strategy 1: Checking RSS Feeds (Most Accurate)...")
    found = {}
    
    # Common RSS paths
    paths = [
        f"{base_url}/feed/", 
        f"{base_url}/rss/", 
        f"{base_url}/feed/atom/",
        f"{base_url}/?feed=rss2",
        f"{base_url}/en/feed/", # Common for WPML
        f"{base_url}/feed"
    ]
    
    for path in paths:
        try:
            response = requests.get(path, timeout=5, headers={'User-Agent': USER_AGENT})
            if response.status_code == 200:
                feed = feedparser.parse(response.content)
                if feed.entries:
                    for entry in feed.entries:
                        link = entry.get('link')
                        published = entry.get('published_parsed') or entry.get('updated_parsed')
                        
                        if link and published:
                            dt = datetime(*published[:6])
                            found[link] = dt
                    if found:
                        status_placeholder.text(f"Found {len(found)} articles in RSS Feed!")
                        return found
        except: continue
    return found

def scan_sitemaps(base_url, status_placeholder):
    """Strategy 2: Sitemaps"""
    status_placeholder.text("Strategy 2: Scanning Sitemaps...")
    domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml", "/news-sitemap.xml"]
    sitemaps = set()
    found = {}
    
    # Check robots.txt
    try:
        r = requests.get(f"{domain}/robots.txt", timeout=5, headers={'User-Agent': USER_AGENT})
        for line in r.text.split('\n'):
            if 'Sitemap:' in line: sitemaps.add(line.split('Sitemap:')[1].strip())
    except: pass
    
    for p in paths:
        try:
            r = requests.get(domain + p, timeout=5); 
            if r.status_code == 200: sitemaps.add(domain + p)
        except: pass

    processed = set()
    while sitemaps:
        sm = sitemaps.pop()
        if sm in processed: continue
        processed.add(sm)
        try:
            r = requests.get(sm, timeout=5, headers={'User-Agent': USER_AGENT})
            root = ET.fromstring(r.content)
            if 'sitemapindex' in str(root.tag).lower():
                for c in root:
                    locs = [x.text for x in c if 'loc' in str(x.tag).lower()]
                    for l in locs: sitemaps.add(l)
            elif 'urlset' in str(root.tag).lower():
                for c in root:
                    url = None; dt = None
                    for x in c:
                        if 'loc' in str(x.tag).lower(): url = x.text
                        if 'lastmod' in str(x.tag).lower(): dt = dateparser.parse(x.text)
                    if url: found[url] = dt
        except: continue
    return found

def scan_homepage(base_url, status_placeholder):
    """Strategy 3: Homepage Links"""
    status_placeholder.text("Strategy 3: Scanning Homepage Links...")
    found = {}
    try:
        r = requests.get(base_url, timeout=10, headers={'User-Agent': USER_AGENT})
        links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
        domain_netloc = urlparse(base_url).netloc
        for link in links:
            full = urljoin(base_url, link)
            if urlparse(full).netloc == domain_netloc:
                if not any(x in full.lower() for x in ['.jpg', '.png', '.css', '/tag/', '/category/']):
                    found[full] = None
    except: pass
    return found

# --- MAIN ---

def run_analysis(url):
    cutoff = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    
    # 1. Gather from all sources
    status = st.empty()
    rss_articles = scan_rss_feeds(url, status)
    sitemap_articles = scan_sitemaps(url, status)
    homepage_articles = scan_homepage(url, status)
    
    # Merge: RSS > Sitemap > Homepage (RSS dates are most reliable)
    all_candidates = homepage_articles.copy()
    all_candidates.update(sitemap_articles) # Sitemap overwrites Homepage if URL exists
    all_candidates.update(rss_articles)     # RSS overwrites everything
    
    candidates_list = list(all_candidates.items()) # [(url, date), ...]
    
    st.info(f"Found {len(candidates_list)} total links. Verifying dates...")
    
    results = []
    progress = st.progress(0)
    checks = 0
    
    for i, (link, known_date) in enumerate(candidates_list):
        if i % 20 == 0: progress.progress(int((i / len(candidates_list)) * 100))
        
        final_date = known_date # Start with date we have (likely from RSS/Sitemap)
        
        # If no date, try to find it
        if not final_date:
            # Try URL pattern
            final_date = find_date_in_url(link)
            
            # Try HTTP Headers (Fast)
            if not final_date:
                final_date = get_date_from_headers(link)
            
            # Try Content (Slow - limited)
            if not final_date and checks < MAX_CONTENT_CHECKS:
                checks += 1
                try:
                    html = trafilatura.fetch_url(link)
                    final_date = get_date_from_html(html)
                except: pass
        
        # Filter
        if final_date:
            final_date = make_naive(final_date)
            if final_date > cutoff:
                results.append({'url': link, 'date': final_date})
                
    progress.empty()
    status.empty()
    return results

# --- UI ---

st.set_page_config(page_title="RSS + Deep Scanner", layout="wide")

st.title("🚀 RSS + Deep Scanner")
st.write(f"Uses **RSS Feeds**, **Sitemaps**, and **Page Headers** to find all articles in the last **{DAYS_TO_SCAN} days**.")

url_input = st.text_input("Website URL", placeholder="https://most.ks.ua/en/")

if st.button("Find Everything"):
    if url_input:
        try:
            results = run_analysis(clean_url(url_input))
            
            if results:
                df = pd.DataFrame(results)
                df['day'] = df['date'].dt.date
                
                total = len(df)
                avg = total / DAYS_TO_SCAN
                
                st.success(f"Found {total} articles!")
                
                c1, c2 = st.columns(2)
                c1.metric("Total Articles", total)
                c2.metric("Daily Average", f"{avg:.1f}")
                
                st.subheader("📅 Daily Counts")
                counts = df['day'].value_counts().sort_index(ascending=False).rename_axis('Date').reset_index(name='Articles')
                st.dataframe(counts, use_container_width=True)
                
                with st.expander("View List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Download CSV", csv, "articles.csv", "text/csv")
            else:
                st.error("No articles found.")
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.warning("Enter a URL.")
