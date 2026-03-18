import streamlit as st
import requests
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET
import pandas as pd
import dateparser
import re
from datetime import datetime, timedelta
import feedparser

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7
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
    # Matches YYYY/MM/DD or YYYY-MM-DD
    patterns = [r'/(\d{4})/(\d{1,2})/(\d{1,2})/', r'/(\d{4})-(\d{1,2})-(\d{1,2})']
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            try: return dateparser.parse(date_str)
            except: pass
    return None

# --- SCANNING STRATEGIES (NO SLOW DOWNLOADS) ---

def scan_rss_feeds(base_url, log_box):
    log_box.text("⏳ Step 1: Checking RSS Feeds...")
    found = {}
    paths = [f"{base_url}/feed/", f"{base_url}/rss/", f"{base_url}/en/feed/", f"{base_url}/uk/feed/"]
    
    for path in paths:
        try:
            r = requests.get(path, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code == 200:
                feed = feedparser.parse(r.content)
                if feed.entries:
                    for entry in feed.entries:
                        link = entry.get('link')
                        published = entry.get('published_parsed') or entry.get('updated_parsed')
                        if link and published:
                            dt = datetime(*published[:6])
                            found[link] = dt
                    if found:
                        log_box.text(f"✅ RSS: Found {len(found)} articles.")
                        return found
        except: continue
    return found

def scan_sitemaps_fast(base_url, log_box):
    log_box.text("⏳ Step 2: Scanning Sitemaps (Fast Mode)...")
    domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    
    # We prioritize specific sitemaps that usually have dates
    priority_paths = [
        "/post-sitemap.xml", "/news-sitemap.xml", "/sitemap-news.xml",
        "/sitemap.xml", "/sitemap_index.xml"
    ]
    
    found = {}
    
    for path in priority_paths:
        try:
            r = requests.get(domain + path, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code != 200: continue
            
            root = ET.fromstring(r.content)
            
            # If it's an index, we just grab the first few links to other sitemaps
            if 'sitemapindex' in str(root.tag).lower():
                # Quick check: does the index list other sitemaps?
                # We will just check the FIRST 3 child sitemaps found in the index
                count = 0
                for child in root:
                    if count >= 3: break # Strict limit for speed
                    loc = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    if loc:
                        # Fetch the child sitemap
                        try:
                            r2 = requests.get(loc[0], timeout=3, headers={'User-Agent': USER_AGENT})
                            if r2.status_code == 200:
                                root2 = ET.fromstring(r2.content)
                                if 'urlset' in str(root2.tag).lower():
                                    for c2 in root2:
                                        url = None; dt = None
                                        for x in c2:
                                            if 'loc' in str(x.tag).lower(): url = x.text
                                            if 'lastmod' in str(x.tag).lower(): dt = dateparser.parse(x.text)
                                        if url: found[url] = dt
                        except: pass
                    count += 1
            
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    url = None; dt = None
                    for x in child:
                        if 'loc' in str(x.tag).lower(): url = x.text
                        if 'lastmod' in str(x.tag).lower(): dt = dateparser.parse(x.text)
                    if url: found[url] = dt
            
            # If we found items, we can stop checking other paths
            if found:
                break
                
        except: continue
        
    log_box.text(f"✅ Sitemap: Found {len(found)} potential URLs.")
    return found

def scan_homepage(base_url, log_box):
    log_box.text("⏳ Step 3: Scanning Homepage...")
    found = {}
    try:
        r = requests.get(base_url, timeout=5, headers={'User-Agent': USER_AGENT})
        links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
        domain_netloc = urlparse(base_url).netloc
        for link in links:
            full = urljoin(base_url, link)
            if urlparse(full).netloc == domain_netloc:
                # Quick filter: Skip obvious non-articles
                if not any(x in full.lower() for x in ['.jpg', '.png', '/tag/', '/category/', '/page/']):
                    # We DO NOT download this link to check date. Too slow.
                    # We only keep it if the URL has a date pattern.
                    if find_date_in_url(full):
                         found[full] = None
                         
        log_box.text(f"✅ Homepage: Found {len(found)} links with dates in URL.")
    except: pass
    return found

# --- MAIN ORCHESTRATOR ---

def run_fast_analysis(url):
    cutoff = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    
    # 1. Gather Candidates
    log_box = st.empty()
    rss_articles = scan_rss_feeds(url, log_box)
    sitemap_articles = scan_sitemaps_fast(url, log_box)
    homepage_articles = scan_homepage(url, log_box)
    
    # Merge: RSS > Sitemap > Homepage
    all_candidates = homepage_articles.copy()
    all_candidates.update(sitemap_articles)
    all_candidates.update(rss_articles)
    
    candidates_list = list(all_candidates.items())
    
    # 2. Verify Dates (Instant)
    log_box.text(f"🔎 Filtering {len(candidates_list)} links...")
    
    results = []
    
    for link, known_date in candidates_list:
        final_date = known_date
        
        # If no date, try URL pattern
        if not final_date:
            final_date = find_date_in_url(link)
        
        # Filter
        if final_date:
            final_date = make_naive(final_date)
            if final_date > cutoff:
                results.append({'url': link, 'date': final_date})
                
    log_box.text(f"✅ Done. Found {len(results)} recent articles.")
    return results

# --- UI ---

st.set_page_config(page_title="Ultra Fast Scanner", layout="wide")

st.title("⚡ Ultra Fast Article Scanner")
st.write("**Speed Mode:** No slow content downloads. Uses RSS, Sitemaps, and URL patterns only.")

url_input = st.text_input("Website URL", placeholder="https://rayon.in.ua/")

if st.button("Run Fast Scan"):
    if url_input:
        try:
            results = run_fast_analysis(clean_url(url_input))
            
            if results:
                df = pd.DataFrame(results)
                df['day'] = df['date'].dt.date
                
                total = len(df)
                avg = total / DAYS_TO_SCAN
                
                st.success(f"Success! Found {total} articles.")
                
                c1, c2 = st.columns(2)
                c1.metric("Total Articles", total)
                c2.metric("Daily Average", f"{avg:.1f}")
                
                st.subheader("📅 Daily Counts")
                counts = df['day'].value_counts().sort_index(ascending=False).rename_axis('Date').reset_index(name='Articles')
                st.dataframe(counts, use_container_width=True)
                
                with st.expander("View Article List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Download CSV", csv, "articles.csv", "text/csv")
            else:
                st.error("No articles found. (Try providing a specific RSS feed link if this fails)")
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.warning("Enter a URL.")
