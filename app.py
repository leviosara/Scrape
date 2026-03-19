import streamlit as st
import requests
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import pandas as pd
import dateparser
import re
from datetime import datetime, timedelta
import feedparser
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
TODAY = datetime.now().date()
YESTERDAY_START = datetime.combine(TODAY - timedelta(days=1), datetime.min.time())
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
MAX_SITEMAPS = 30 # Limit to prevent overload
MAX_WORKERS = 10  # Number of parallel threads

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

def is_real_article(url):
    url_lower = url.lower()
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    
    if not path or path == '/': return False
    
    # Block extensions
    if any(url_lower.endswith(ext) for ext in ['.jpg', '.png', '.gif', '.pdf', '.css', '.js', '.xml', '.zip']):
        return False

    # Block Category Slugs (Optimized List)
    last_segment = path.split('/')[-1]
    forbidden_slugs = [
        'promo', 'city', 'news', 'sport', 'science', 'politics', 'world', 
        'society', 'economics', 'culture', 'life', 'style', 'video', 'photo',
        'archive', 'archives', 'author', 'tags', 'tag', 'category', 'page',
        'search', 'feed', 'rss', 'amp', 'ukraine', 'kyiv', 'contacts', 'about'
    ]
    if last_segment in forbidden_slugs: return False

    return True

# --- SCANNING STRATEGIES ---

def check_rss(base_url):
    """Fast RSS check."""
    paths = [f"{base_url}/feed/", f"{base_url}/rss/", f"{base_url}/en/feed/", f"{base_url}/uk/feed/"]
    for path in paths:
        try:
            r = requests.get(path, timeout=2, headers={'User-Agent': USER_AGENT})
            if r.status_code == 200:
                feed = feedparser.parse(r.content)
                found = {}
                if feed.entries:
                    for entry in feed.entries:
                        link = entry.get('link')
                        published = entry.get('published_parsed') or entry.get('updated_parsed')
                        if link and published:
                            dt = datetime(*published[:6])
                            dt = make_naive(dt)
                            if dt > YESTERDAY_START and is_real_article(link):
                                found[link] = {'date': dt, 'category': 'RSS Feed'}
                    if found: return found
        except: continue
    return None

def fetch_sitemap_urls(domain):
    """Step 1: Get the list of sitemap files quickly."""
    index_paths = [f"{domain}/sitemap.xml", f"{domain}/sitemap_index.xml"]
    sitemap_files = []
    
    for path in index_paths:
        try:
            r = requests.get(path, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code != 200: continue
            
            root = ET.fromstring(r.content)
            
            # If it's a single sitemap
            if 'urlset' in str(root.tag).lower():
                sitemap_files.append(path)
                break
            
            # If it's an index
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    loc = None
                    lastmod = None
                    for x in child:
                        if 'loc' in str(x.tag).lower(): loc = x.text
                        if 'lastmod' in str(x.tag).lower(): lastmod = x.text
                    
                    if loc:
                        # Basic filter: Keep if priority or updated recently
                        is_prio = any(x in loc.lower() for x in ['news', 'post', 'sport', 'article'])
                        is_recent = False
                        if lastmod:
                            m_dt = make_naive(dateparser.parse(lastmod))
                            if m_dt and m_dt > (datetime.now() - timedelta(days=14)): is_recent = True
                        
                        if is_prio or is_recent or not lastmod:
                            sitemap_files.append(loc)
                break
        except: continue
        
    return sitemap_files[:MAX_SITEMAPS]

def parse_single_sitemap(sm_url):
    """Worker function: Downloads and parses ONE sitemap."""
    results = {}
    category = sm_url.split('/')[-1].split('-')[0].capitalize()
    
    try:
        r = requests.get(sm_url, timeout=4, headers={'User-Agent': USER_AGENT})
        root = ET.fromstring(r.content)
        
        if 'urlset' not in str(root.tag).lower(): return results

        for child in root:
            url = None
            dt = None
            
            for x in child:
                if 'loc' in str(x.tag).lower(): url = x.text
                if 'lastmod' in str(x.tag).lower(): dt = dateparser.parse(x.text)
            
            if url and is_real_article(url):
                valid = False
                if dt:
                    dt = make_naive(dt)
                    if dt > YESTERDAY_START: valid = True
                
                if not valid:
                    dt_url = find_date_in_url(url)
                    if dt_url:
                        dt_url = make_naive(dt_url)
                        if dt_url > YESTERDAY_START:
                            dt = dt_url
                            valid = True
                
                if valid and dt:
                    results[url] = {'date': dt, 'category': category}
                    
    except: pass
    return results

# --- MAIN ORCHESTRATOR ---

def run_fast_scan(url):
    status = st.status("🚀 Initializing Fast Scan...", expanded=True)
    final_articles = {}
    
    # 1. RSS (Try first)
    status.update(label="📡 Step 1: Checking RSS...")
    rss_res = check_rss(url)
    if rss_res:
        final_articles.update(rss_res)
        status.update(label=f"✅ RSS Found {len(rss_res)} articles. Checking Sitemaps for more...")
    
    # 2. Sitemap Discovery
    domain = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    status.update(label="📡 Step 2: Finding Sitemaps...")
    sitemap_urls = fetch_sitemap_urls(domain)
    
    if not sitemap_urls:
        if not final_articles:
            status.update(label="❌ No RSS or Sitemaps found.", state="error")
            return None, "No data sources found."
        else:
            return final_articles, None

    # 3. Parallel Sitemap Scan (The Speed Boost)
    status.update(label=f"⚡ Step 3: Parallel Scanning {len(sitemap_urls)} Sitemaps...")
    
    # Create a thread pool
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        future_to_url = {executor.submit(parse_single_sitemap, sm_url): sm_url for sm_url in sitemap_urls}
        
        # Process as they complete
        for future in as_completed(future_to_url):
            try:
                data = future.result()
                if data:
                    final_articles.update(data)
            except:
                pass
                
    if not final_articles:
        status.update(label="❌ Scan Complete: No articles found.", state="error")
        return None, "No articles found in the date range."
        
    status.update(label=f"✅ Done! Found {len(final_articles)} articles.", state="complete")
    return final_articles, None

# --- UI ---

st.set_page_config(page_title="Turbo News Scanner", layout="wide")

st.title("⚡ Turbo News Scanner")
st.write("Uses **Parallel Processing** to scan 10+ sitemaps at once. Filters categories.")

url_input = st.text_input("Website URL", placeholder="https://lb.ua/")

if st.button("Run Turbo Scan"):
    if url_input:
        try:
            articles, error = run_fast_scan(clean_url(url_input))
            
            if error:
                st.warning(error)
            
            if articles:
                df = pd.DataFrame.from_dict(articles, orient='index')
                df.reset_index(inplace=True)
                df.columns = ['url', 'date', 'category']
                df['date'] = pd.to_datetime(df['date'])
                
                df['day'] = df['date'].dt.date
                
                count_today = len(df[df['day'] == TODAY])
                count_yesterday = len(df[df['day'] == (TODAY - timedelta(days=1))])
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Today", count_today)
                c2.metric("Yesterday", count_yesterday)
                c3.metric("Total", len(df))
                
                st.divider()
                
                with st.expander("View Article List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Download CSV", csv, "turbo_scan.csv", "text/csv")
                    
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Enter a URL.")
