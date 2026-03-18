import streamlit as st
import requests
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import pandas as pd
import dateparser
import re
from datetime import datetime, timedelta
import feedparser

# --- CONFIGURATION ---
TIME_THRESHOLD = datetime.now() - timedelta(days=2)
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

def is_news_url(url):
    url_lower = url.lower()
    if any(x in url_lower for x in ['/tag/', '/author/', '/page/', '/category/', '/feed/', '.jpg', '.css']):
        return False
    return True

# --- SCANNING STRATEGIES ---

def check_rss(base_url, status):
    status.update(label="📡 Step 1: Checking RSS Feeds (Fastest)...")
    found = {}
    paths = [
        f"{base_url}/feed/", f"{base_url}/rss/", f"{base_url}/feed/atom/",
        f"{base_url}/uk/feed/", f"{base_url}/en/feed/", f"{base_url}/news/feed/"
    ]
    
    for path in paths:
        try:
            r = requests.get(path, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code == 200:
                feed = feedparser.parse(r.content)
                if feed.entries:
                    status.update(label=f"✅ RSS Found! Parsing {len(feed.entries)} entries...")
                    for entry in feed.entries:
                        link = entry.get('link')
                        published = entry.get('published_parsed') or entry.get('updated_parsed')
                        if link and published:
                            dt = datetime(*published[:6])
                            dt = make_naive(dt)
                            if dt > TIME_THRESHOLD:
                                found[link] = dt
                    if found:
                        status.update(label=f"✅ RSS Success: {len(found)} recent articles.")
                        return found
        except: continue
    status.update(label="⚠️ RSS not found or empty. Moving to Sitemaps...")
    return found

def check_sitemaps_smart(base_url, status):
    status.update(label="📡 Step 2: Analyzing Sitemap Index...")
    domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    
    # 1. Find the Index
    index_paths = ["/sitemap.xml", "/sitemap_index.xml"]
    sitemap_files = [] # List of (url, lastmod)
    
    for path in index_paths:
        try:
            r = requests.get(domain + path, timeout=4, headers={'User-Agent': USER_AGENT})
            if r.status_code == 200:
                root = ET.fromstring(r.content)
                
                # If it's an index, get all children
                if 'sitemapindex' in str(root.tag).lower():
                    for child in root:
                        loc = None
                        lastmod = None
                        for x in child:
                            if 'loc' in str(x.tag).lower(): loc = x.text
                            if 'lastmod' in str(x.tag).lower(): lastmod = x.text
                        
                        if loc:
                            # Filter: Only keep sitemaps updated in the last 7 days (safe buffer)
                            # If no lastmod, we keep it to be safe
                            if lastmod:
                                mod_date = dateparser.parse(lastmod)
                                if mod_date and make_naive(mod_date) > (datetime.now() - timedelta(days=7)):
                                    sitemap_files.append(loc)
                            else:
                                sitemap_files.append(loc)
                                
                elif 'urlset' in str(root.tag).lower():
                    # It was a single sitemap, not an index
                    sitemap_files.append(domain + path)
                    
                break # Found sitemap
        except: continue

    if not sitemap_files:
        status.update(label="❌ No Sitemaps found.")
        return {}

    # 2. Scan the relevant sitemap files
    status.update(label=f"🔎 Scanning {len(sitemap_files)} relevant sitemap sections...")
    found = {}
    
    for i, sm_url in enumerate(sitemap_files):
        # Limit to 15 sub-sitemaps to prevent hanging on huge sites
        if i >= 15: break 
        
        status.update(label=f"📄 Scanning section {i+1}/{len(sitemap_files)}...")
        
        try:
            r = requests.get(sm_url, timeout=4, headers={'User-Agent': USER_AGENT})
            root = ET.fromstring(r.content)
            
            if 'urlset' in str(root.tag).lower():
                for child in root:
                    url = None
                    dt = None
                    
                    for x in child:
                        if 'loc' in str(x.tag).lower(): url = x.text
                        if 'lastmod' in str(x.tag).lower(): dt = dateparser.parse(x.text)
                    
                    if url and is_news_url(url):
                        # Check Date
                        valid = False
                        
                        # A. Sitemap Date
                        if dt:
                            dt = make_naive(dt)
                            if dt > TIME_THRESHOLD: valid = True
                        
                        # B. URL Date (fallback)
                        if not valid:
                            dt_url = find_date_in_url(url)
                            if dt_url:
                                dt_url = make_naive(dt_url)
                                if dt_url > TIME_THRESHOLD:
                                    dt = dt_url
                                    valid = True
                        
                        if valid and dt:
                            found[url] = dt
                            
        except: continue

    return found

# --- MAIN ORCHESTRATOR ---

def run_full_check(url):
    status = st.status("🚀 Starting Scan...", expanded=True)
    
    # 1. RSS
    rss_res = check_rss(url, status)
    
    # 2. Sitemaps
    sitemap_res = {}
    if not rss_res:
        sitemap_res = check_sitemaps_smart(url, status)
        
    # Combine
    all_articles = {**sitemap_res, **rss_res}
    
    # Feedback Logic
    if not all_articles:
        status.update(label="❌ Scan Complete: No articles found.", state="error")
        return None, "No articles found in the last 2 days. The site might not provide dates or has no recent content."
    
    status.update(label=f"✅ Success! Found {len(all_articles)} articles.", state="complete")
    return all_articles, None

# --- UI ---

st.set_page_config(page_title="Full 2-Day Scanner", layout="wide")

st.title("🚀 Full 2-Day News Scanner")
st.write("Scans **All Categories** but skips old sitemap files to ensure speed.")

url_input = st.text_input("Website URL", placeholder="https://rayon.in.ua/")

if st.button("Scan Now"):
    if url_input:
        try:
            articles, error = run_full_check(clean_url(url_input))
            
            if error:
                st.warning(error)
            
            if articles:
                df = pd.DataFrame(list(articles.items()), columns=['url', 'date'])
                df['date'] = pd.to_datetime(df['date'])
                
                today = datetime.now().date()
                yesterday = (datetime.now() - timedelta(days=1)).date()
                df['day'] = df['date'].dt.date
                
                count_today = len(df[df['day'] == today])
                count_yesterday = len(df[df['day'] == yesterday])
                
                c1, c2, c3 = st.columns(3)
                c1.metric("Today", count_today)
                c2.metric("Yesterday", count_yesterday)
                c3.metric("Total (48h)", len(df))
                
                st.divider()
                
                st.subheader("📅 Daily Breakdown")
                counts = df['day'].value_counts().sort_index(ascending=False).rename_axis('Date').reset_index(name='Articles')
                st.dataframe(counts, use_container_width=True)
                
                with st.expander("View Full Article List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Enter a URL.")
