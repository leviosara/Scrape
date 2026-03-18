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
    patterns = [r'/(\d{4})/(\d{1,2})/(\d{1,2})/', r'/(\d{4})-(\d{1,2})-(\d{1,2})']
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            try: return dateparser.parse(date_str)
            except: pass
    return None

# --- SCANNING STRATEGIES ---

def scan_rss_feeds(base_url, status_text, progress_bar):
    status_text.text("⏳ Step 1/3: Checking RSS Feeds...")
    progress_bar.progress(10)
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
                        status_text.text(f"✅ RSS: Found {len(found)} articles.")
                        return found
        except: continue
    return found

def scan_sitemaps_fast(base_url, status_text, progress_bar):
    status_text.text("⏳ Step 2/3: Scanning Sitemaps...")
    progress_bar.progress(30)
    domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    
    priority_paths = ["/post-sitemap.xml", "/news-sitemap.xml", "/sitemap.xml", "/sitemap_index.xml"]
    found = {}
    
    for i, path in enumerate(priority_paths):
        # Update UI to show we are trying different files
        status_text.text(f"⏳ Step 2/3: Trying sitemap {i+1}/{len(priority_paths)}...")
        
        try:
            r = requests.get(domain + path, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code != 200: continue
            
            root = ET.fromstring(r.content)
            
            if 'sitemapindex' in str(root.tag).lower():
                # If it's an index, we check the first 3 children
                child_count = 0
                for child in root:
                    if child_count >= 3: break
                    loc = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    if loc:
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
                    child_count += 1
            
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    url = None; dt = None
                    for x in child:
                        if 'loc' in str(x.tag).lower(): url = x.text
                        if 'lastmod' in str(x.tag).lower(): dt = dateparser.parse(x.text)
                    if url: found[url] = dt
            
            # If we found stuff, stop early
            if found: break
                
        except: continue
        
    status_text.text(f"✅ Sitemap: Found {len(found)} potential URLs.")
    progress_bar.progress(60)
    return found

def scan_homepage(base_url, status_text, progress_bar):
    status_text.text("⏳ Step 3/3: Scanning Homepage...")
    progress_bar.progress(80)
    found = {}
    try:
        r = requests.get(base_url, timeout=5, headers={'User-Agent': USER_AGENT})
        links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
        domain_netloc = urlparse(base_url).netloc
        for link in links:
            full = urljoin(base_url, link)
            if urlparse(full).netloc == domain_netloc:
                if not any(x in full.lower() for x in ['.jpg', '.png', '/tag/', '/category/']):
                    if find_date_in_url(full):
                         found[full] = None
                         
        status_text.text(f"✅ Homepage: Found {len(found)} links with dates in URL.")
    except: pass
    return found

# --- MAIN ORCHESTRATOR ---

def run_fast_analysis(url):
    cutoff = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    
    # Create UI elements
    status_text = st.empty()
    progress_bar = st.progress(0)
    
    # 1. Gather Candidates
    rss_articles = scan_rss_feeds(url, status_text, progress_bar)
    sitemap_articles = scan_sitemaps_fast(url, status_text, progress_bar)
    homepage_articles = scan_homepage(url, status_text, progress_bar)
    
    # Merge
    all_candidates = homepage_articles.copy()
    all_candidates.update(sitemap_articles)
    all_candidates.update(rss_articles)
    
    candidates_list = list(all_candidates.items())
    
    # 2. Verify Dates
    status_text.text(f"🔎 Filtering {len(candidates_list)} links...")
    progress_bar.progress(90)
    
    results = []
    
    for i, (link, known_date) in enumerate(candidates_list):
        # Mini progress update inside loop if huge list
        if len(candidates_list) > 100 and i % 20 == 0:
            pass # Avoid flickering too much, 90% is enough indicator
        
        final_date = known_date
        
        if not final_date:
            final_date = find_date_in_url(link)
        
        if final_date:
            final_date = make_naive(final_date)
            if final_date > cutoff:
                results.append({'url': link, 'date': final_date})
                
    progress_bar.progress(100)
    status_text.text(f"✅ Done. Found {len(results)} recent articles.")
    return results

# --- UI ---

st.set_page_config(page_title="Ultra Fast Scanner", layout="wide")

st.title("⚡ Ultra Fast Article Scanner")
st.write("**Speed Mode:** Includes Progress Bar. Uses RSS, Sitemaps, and URL patterns only.")

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
