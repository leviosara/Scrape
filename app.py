import streamlit as st
import requests
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET
import pandas as pd
import trafilatura
import dateparser
import re
from datetime import datetime, timedelta

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7
# Increased limits to catch more articles
MAX_CONTENT_CHECKS = 500 
MAX_SITEMAP_URLS = 2000

# --- HELPER FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

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

def is_likely_article(url):
    url_lower = url.lower()
    skip = ['/tag/', '/category/', '/author/', '/page/', '/feed/', '.jpg', '.png', '.gif', '.pdf', '#respond']
    return not any(s in url_lower for s in skip)

def extract_date_from_html(html):
    """Aggressively search for a date in HTML metadata."""
    if not html: return None
    
    # 1. Trafilatura metadata
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata and metadata.date:
            return dateparser.parse(metadata.date)
    except: pass

    # 2. JSON-LD (Schema.org)
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if match:
        try: return dateparser.parse(match.group(1))
        except: pass
        
    # 3. Open Graph / Meta tags
    match = re.search(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']', html)
    if match:
        try: return dateparser.parse(match.group(1))
        except: pass

    match = re.search(r'<meta[^>]+itemprop=["\']datePublished["\'][^>]+content=["\']([^"\']+)["\']', html)
    if match:
        try: return dateparser.parse(match.group(1))
        except: pass
        
    return None

# --- SCRAPER STRATEGIES ---

def get_urls_from_sitemap(base_url, progress):
    """Strategy 1: Traditional Sitemap Parsing"""
    domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    sm_paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml", "/news-sitemap.xml"]
    sitemaps = []
    found_urls = set()
    
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200: sitemaps.append(domain + path)
        except: continue
            
    if not sitemaps:
        try:
            r = requests.get(domain + "/robots.txt", timeout=5)
            for line in r.text.split('\n'):
                if 'Sitemap:' in line: sitemaps.append(line.split('Sitemap:')[1].strip())
        except: pass

    processed = set()
    while sitemaps:
        sm = sitemaps.pop(0)
        if sm in processed: continue
        processed.add(sm)
        
        try:
            r = requests.get(sm, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs: 
                        if loc: sitemaps.append(loc)
            
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    loc = None
                    date = None
                    for c in child:
                        if 'loc' in str(c.tag).lower(): loc = c.text
                        if 'lastmod' in str(c.tag).lower() or 'publication_date' in str(c.tag).lower(): date = c.text
                    
                    if loc and is_likely_article(loc):
                        found_urls.add((loc, date))
                        
                    if len(found_urls) > MAX_SITEMAP_URLS: break
        except: continue

    return list(found_urls)

def get_urls_from_homepage(base_url, progress):
    """Strategy 2: Scan Homepage for links (Catches recent news not yet in sitemap)"""
    progress.progress(10, text="Scanning Homepage for recent links...")
    found_urls = set()
    try:
        r = requests.get(base_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
        # Extract all hrefs using regex (fast and lightweight)
        links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
        
        domain_netloc = urlparse(base_url).netloc
        for link in links:
            # Resolve relative links
            full_link = urljoin(base_url, link)
            # Filter: only internal links that look like articles
            if urlparse(full_link).netloc == domain_netloc and is_likely_article(full_link):
                # No date from homepage scan, will need to be checked
                found_urls.add((full_link, None)) 
                
    except Exception as e:
        print(f"Homepage scan error: {e}")
        
    return list(found_urls)

# --- MAIN ORCHESTRATOR ---

def analyze_website(url):
    cutoff_date = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    
    # 1. Get candidates from both sources
    sitemap_candidates = get_urls_from_sitemap(url, st.progress(0.2, text="Checking Sitemap..."))
    homepage_candidates = get_urls_from_homepage(url, st.progress(0.4, text="Checking Homepage..."))
    
    # Combine and deduplicate (dictionary keeps last value, but we want set of unique URLs)
    # Using dict to merge: URL -> Date
    combined = {}
    for u, d in sitemap_candidates: combined[u] = d
    for u, d in homepage_candidates: 
        if u not in combined: combined[u] = d # Prefer sitemap date if available
            
    candidates = [(u, d) for u, d in combined.items()]
    
    # 2. Analyze Dates
    progress = st.progress(0.5, text=f"Found {len(candidates)} links. Checking dates..."))
    articles = []
    checks_done = 0
    
    for i, (url, known_date) in enumerate(candidates):
        if i % 20 == 0:
            progress.progress(0.5 + (i/len(candidates)*0.5), text=f"Checking {i}/{len(candidates)}...")
            
        final_date = None
        
        # Fast path: Date known from sitemap
        if known_date:
            final_date = dateparser.parse(known_date)
        
        # Medium path: Date in URL
        if not final_date:
            final_date = find_date_in_url(url)
            
        # Slow path: Date in Content (only if we have quota)
        if not final_date and checks_done < MAX_CONTENT_CHECKS:
            checks_done += 1
            try:
                html = trafilatura.fetch_url(url)
                final_date = extract_date_from_html(html)
            except: pass
        
        # Filter
        if final_date:
            final_date = make_naive(final_date)
            if final_date > cutoff_date:
                articles.append({'url': url, 'date': final_date})

    return articles

# --- STREAMLIT UI ---

st.set_page_config(page_title="Complete Article Finder", layout="wide")

st.title("📡 Complete Article Finder")
st.write(f"Scans both **Sitemap** and **Homepage** to find all articles from the last **{DAYS_TO_SCAN} days**.")

url_input = st.text_input("Website URL", placeholder="https://most.ks.ua/en/")

if st.button("Deep Scan"):
    if url_input:
        try:
            results = analyze_website(clean_url(url_input))
            
            if results:
                df = pd.DataFrame(results)
                df['day'] = df['date'].dt.date
                
                # STATS
                total = len(df)
                avg = total / DAYS_TO_SCAN
                
                st.subheader("📊 Results")
                c1, c2 = st.columns(2)
                c1.metric("Total Found", total)
                c2.metric("Daily Average", f"{avg:.1f}")
                
                st.subheader("📅 Daily Counts")
                counts = df['day'].value_counts().sort_index(ascending=False).rename_axis('Date').reset_index(name='Articles')
                st.bar_chart(counts.set_index('Date'))
                
                with st.expander("View Full List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("Download CSV", csv, "results.csv", "text/csv")
            else:
                st.error("No articles found. The site may be blocking requests or has no recent content.")
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.warning("Enter a URL.")
