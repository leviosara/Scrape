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

def guess_category_from_url(sitemap_url):
    """Extracts the category name from the sitemap filename (e.g., sport-sitemap.xml -> Sport)"""
    filename = sitemap_url.split('/')[-1].lower()
    if 'news' in filename: return 'News'
    if 'sport' in filename: return 'Sport'
    if 'investig' in filename: return 'Investigation'
    if 'history' in filename: return 'History'
    if 'post' in filename: return 'Posts'
    return 'Main'

# --- SCANNING STRATEGIES ---

def check_rss(base_url, status):
    status.update(label="📡 Step 1: Checking RSS Feeds...")
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
                            dt = make_naive(dt)
                            if dt > TIME_THRESHOLD:
                                found[link] = {'date': dt, 'category': 'RSS Feed'}
                    if found:
                        return found
        except: continue
    return found

def analyze_sitemap_index(index_url, status):
    """Finds ALL relevant sitemap files from the index."""
    status.update(label="🔎 Analyzing Sitemap Index...")
    try:
        r = requests.get(index_url, timeout=5, headers={'User-Agent': USER_AGENT})
        root = ET.fromstring(r.content)
    except: return []

    sitemaps_to_scan = []
    
    # If it's just a single URL set, return it
    if 'urlset' in str(root.tag).lower():
        return [(index_url, 'Main')]

    # It is an index, let's filter the children
    if 'sitemapindex' in str(root.tag).lower():
        for child in root:
            loc = None
            lastmod = None
            for x in child:
                if 'loc' in str(x.tag).lower(): loc = x.text
                if 'lastmod' in str(x.tag).lower(): lastmod = x.text
            
            if not loc: continue

            # FILTERING LOGIC
            # 1. Check Name (Prioritize News/Post/Category names)
            is_priority = any(x in loc.lower() for x in ['news', 'post', 'sport', 'history', 'investig'])
            
            # 2. Check Date (Was this sitemap file updated recently?)
            is_recent = False
            if lastmod:
                mod_date = make_naive(dateparser.parse(lastmod))
                if mod_date and mod_date > (datetime.now() - timedelta(days=14)): # Check if sitemap itself was updated in last 2 weeks
                    is_recent = True
            
            # Decision: Keep if it has a good name OR if it was updated recently
            if is_priority or is_recent:
                category = guess_category_from_url(loc)
                sitemaps_to_scan.append((loc, category))

    return sitemaps_to_scan

def scan_sitemap_file(sm_url, category, status):
    """Scans a specific sitemap file for articles."""
    results = {}
    try:
        r = requests.get(sm_url, timeout=5, headers={'User-Agent': USER_AGENT})
        root = ET.fromstring(r.content)
        
        if 'urlset' not in str(root.tag).lower(): return {}

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
                
                # B. URL Date (Fallback)
                if not valid:
                    dt_url = find_date_in_url(url)
                    if dt_url:
                        dt_url = make_naive(dt_url)
                        if dt_url > TIME_THRESHOLD:
                            dt = dt_url
                            valid = True
                
                if valid and dt:
                    results[url] = {'date': dt, 'category': category}
                    
    except: pass
    return results

# --- MAIN ORCHESTRATOR ---

def run_deep_scan(url):
    status = st.status("🚀 Starting Deep Scan...", expanded=True)
    
    # 1. RSS
    rss_res = check_rss(url, status)
    
    # 2. Sitemap Discovery
    domain = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    index_paths = [f"{domain}/sitemap.xml", f"{domain}/sitemap_index.xml"]
    
    all_sitemaps = []
    for path in index_paths:
        found = analyze_sitemap_index(path, status)
        if found:
            all_sitemaps = found
            break
            
    # 3. Deep Scan (No Limits)
    sitemap_res = {}
    if all_sitemaps:
        status.update(label=f"🔎 Deep Scanning {len(all_sitemaps)} Sitemap Sections...")
        
        for i, (sm_url, cat) in enumerate(all_sitemaps):
            status.update(label=f"📄 Scanning [{cat}] ({i+1}/{len(all_sitemaps)})...")
            part_res = scan_sitemap_file(sm_url, cat, status)
            sitemap_res.update(part_res)
            
    # Combine
    final_articles = {**sitemap_res, **rss_res}
    
    # Feedback
    if not final_articles:
        status.update(label="❌ Scan Complete: No articles found.", state="error")
        return None, "Scanned deeply but found no articles matching the 2-day criteria."
        
    status.update(label=f"✅ Deep Scan Complete. Found {len(final_articles)} articles.", state="complete")
    return final_articles, None

# --- UI ---

st.set_page_config(page_title="Deep 2-Day Scanner", layout="wide")

st.title("🦈 Deep 2-Day Scanner")
st.write("Scans **ALL** sitemap categories (News, Sport, History) without arbitrary limits.")

url_input = st.text_input("Website URL", placeholder="https://rayon.in.ua/")

if st.button("Run Deep Scan"):
    if url_input:
        try:
            articles, error = run_deep_scan(clean_url(url_input))
            
            if error:
                st.warning(error)
            
            if articles:
                # Format Data
                df = pd.DataFrame.from_dict(articles, orient='index')
                df.reset_index(inplace=True)
                df.columns = ['url', 'date', 'category']
                df['date'] = pd.to_datetime(df['date'])
                
                today = datetime.now().date()
                yesterday = (datetime.now() - timedelta(days=1)).date()
                df['day'] = df['date'].dt.date
                
                # Stats
                c1, c2, c3 = st.columns(3)
                c1.metric("Today", len(df[df['day'] == today]))
                c2.metric("Yesterday", len(df[df['day'] == yesterday]))
                c3.metric("Total (48h)", len(df))
                
                # Category Breakdown (Proof of Deep Scan)
                st.subheader("📂 Articles by Category")
                cat_counts = df['category'].value_counts().reset_index()
                cat_counts.columns = ['Category', 'Count']
                st.dataframe(cat_counts, use_container_width=True)
                
                st.divider()
                
                with st.expander("View Full Article List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Download CSV", csv, "deep_scan.csv", "text/csv")
                    
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Enter a URL.")
