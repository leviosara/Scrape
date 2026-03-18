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
# We only scan the last 2 days (48 hours)
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
    """Find date in URL like /2024/03/19/"""
    patterns = [r'/(\d{4})/(\d{1,2})/(\d{1,2})/', r'/(\d{4})-(\d{1,2})-(\d{1,2})']
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            try: return dateparser.parse(date_str)
            except: pass
    return None

def is_news_url(url):
    """Filter: Keep only news-like URLs, skip tags/authors"""
    url_lower = url.lower()
    # Blacklist common junk paths
    if any(x in url_lower for x in ['/tag/', '/author/', '/page/', '/category/', '/feed/', '.jpg', '.css']):
        return False
    return True

# --- SCANNING STRATEGIES ---

def check_rss(base_url, status):
    status.update(label="📡 Strategy 1: Checking RSS Feed (Best for News)...")
    found = {}
    paths = [
        f"{base_url}/feed/", f"{base_url}/rss/", f"{base_url}/feed/atom/",
        f"{base_url}/uk/feed/", f"{base_url}/en/feed/", # Language specific
        f"{base_url}/news/feed/"
    ]
    
    for path in paths:
        try:
            r = requests.get(path, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code == 200:
                feed = feedparser.parse(r.content)
                if feed.entries:
                    status.update(label=f"✅ RSS Found at {path}! Parsing entries...")
                    for entry in feed.entries:
                        link = entry.get('link')
                        # Get date from RSS (most reliable)
                        published = entry.get('published_parsed') or entry.get('updated_parsed')
                        if link and published:
                            dt = datetime(*published[:6])
                            dt = make_naive(dt)
                            if dt > TIME_THRESHOLD:
                                found[link] = dt
                    if found:
                        return found
        except: continue
    return found

def check_news_sitemaps(base_url, status):
    status.update(label="📡 Strategy 2: Checking News Sitemaps...")
    domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    
    # Prioritize news specific sitemaps
    maps_to_try = [
        f"{domain}/sitemap-news.xml", f"{domain}/news-sitemap.xml", 
        f"{domain}/post-sitemap.xml", f"{domain}/sitemap.xml"
    ]
    
    found = {}
    
    for map_url in maps_to_try:
        try:
            r = requests.get(map_url, timeout=3, headers={'User-Agent': USER_AGENT})
            if r.status_code != 200: continue
            
            root = ET.fromstring(r.content)
            
            # If it's an index, we only check the first child (usually newest)
            if 'sitemapindex' in str(root.tag).lower():
                # Grab first loc
                for child in root:
                    for x in child:
                        if 'loc' in str(x.tag).lower():
                            # Fetch child sitemap
                            try:
                                r2 = requests.get(x.text, timeout=3, headers={'User-Agent': USER_AGENT})
                                root2 = ET.fromstring(r2.content)
                                # Parse child
                                entries = parse_sitemap_xml(root2)
                                found.update(entries)
                                break # Only check first child for speed
                            except: pass
                    break
            
            elif 'urlset' in str(root.tag).lower():
                entries = parse_sitemap_xml(root)
                found.update(entries)
                
            if found: break # Stop if we found stuff
            
        except: continue
        
    return found

def parse_sitemap_xml(root):
    """Helper to parse URLSet XML"""
    results = {}
    for child in root:
        url = None
        dt = None
        for x in child:
            if 'loc' in str(x.tag).lower(): url = x.text
            if 'lastmod' in str(x.tag).lower() or 'publication_date' in str(x.tag).lower():
                dt = make_naive(dateparser.parse(x.text))
        
        if url and is_news_url(url):
            # Check date
            if dt and dt > TIME_THRESHOLD:
                results[url] = dt
            elif not dt:
                # If no date in XML, check URL
                dt_url = find_date_in_url(url)
                if dt_url and make_naive(dt_url) > TIME_THRESHOLD:
                    results[url] = make_naive(dt_url)
                    
    return results

# --- MAIN ORCHESTRATOR ---

def run_sprint_scan(url):
    status = st.status("🚀 Starting 2-Day Sprint Scan...", expanded=True)
    
    # Step 1: RSS
    rss_results = check_rss(url, status)
    
    # Step 2: Sitemaps (if RSS failed or was empty)
    sitemap_results = {}
    if not rss_results:
        sitemap_results = check_news_sitemaps(url, status)
        
    # Combine
    all_articles = {**sitemap_results, **rss_results} # RSS overwrites sitemap if duplicates
    
    # Final Checks
    if not all_articles:
        status.update(label="❌ Scan Complete: No articles found.", state="error")
        return None, "Found links, but none matched the last 2 days. The site might be inactive or dates are hidden."
    
    # Format for display
    status.update(label=f"✅ Scan Complete! Found {len(all_articles)} recent articles.", state="complete")
    return all_articles, None

# --- UI ---

st.set_page_config(page_title="2-Day News Counter", layout="wide")

st.title("⚡ 2-Day News Counter")
st.write("Counts articles posted **Today** and **Yesterday** only. Fast feedback.")

url_input = st.text_input("Website URL", placeholder="https://most.ks.ua/en/")

if st.button("Count Recent News"):
    if url_input:
        try:
            articles, error_msg = run_sprint_scan(clean_url(url_input))
            
            if error_msg:
                st.warning(error_msg)
            
            if articles:
                df = pd.DataFrame(list(articles.items()), columns=['url', 'date'])
                df['date'] = pd.to_datetime(df['date'])
                
                # Separate Today and Yesterday
                today = datetime.now().date()
                yesterday = (datetime.now() - timedelta(days=1)).date()
                
                df['day'] = df['date'].dt.date
                
                count_today = len(df[df['day'] == today])
                count_yesterday = len(df[df['day'] == yesterday])
                total = len(df)
                
                # Display Metrics
                c1, c2, c3 = st.columns(3)
                c1.metric("Today", count_today)
                c2.metric("Yesterday", count_yesterday)
                c3.metric("Total (48h)", total)
                
                st.divider()
                
                with st.expander("View Article List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Please enter a URL.")
