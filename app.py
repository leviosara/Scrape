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
# Strict Cutoff: Yesterday at Midnight (Ensures exactly 2 calendar days)
TODAY = datetime.now().date()
YESTERDAY_START = datetime.combine(TODAY - timedelta(days=1), datetime.min.time())
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

def is_real_article(url):
    """
    Strict filter to remove Category/Tag pages and keep only actual articles.
    """
    url_lower = url.lower()
    
    # 1. Blacklist obvious junk
    blacklist = ['/tag/', '/author/', '/page/', '/category/', '/feed/', 
                 '.jpg', '.png', '.gif', '.pdf', '.css', '.js', 
                 'replytocom', '/edit', '/amp/']
    if any(x in url_lower for x in blacklist):
        return False
        
    # 2. Depth Check (Crucial)
    # Real articles usually have longer URLs: site.com/category/article-title (4 slashes)
    # Category pages look like: site.com/category/ (3 slashes)
    # https:// = 2 slashes.
    # So we require AT LEAST 4 slashes total to be considered an article.
    if url_lower.count('/') < 4:
        return False
        
    return True

def guess_category_from_url(sitemap_url):
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
                            # Strict check: Must be after Yesterday Midnight
                            if dt > YESTERDAY_START and is_real_article(link):
                                found[link] = {'date': dt, 'category': 'RSS Feed'}
                    if found:
                        return found
        except: continue
    return found

def analyze_sitemap_index(index_url, status):
    status.update(label="🔎 Analyzing Sitemap Index...")
    try:
        r = requests.get(index_url, timeout=5, headers={'User-Agent': USER_AGENT})
        root = ET.fromstring(r.content)
    except: return []

    sitemaps_to_scan = []
    
    if 'urlset' in str(root.tag).lower():
        return [(index_url, 'Main')]

    if 'sitemapindex' in str(root.tag).lower():
        for child in root:
            loc = None
            lastmod = None
            for x in child:
                if 'loc' in str(x.tag).lower(): loc = x.text
                if 'lastmod' in str(x.tag).lower(): lastmod = x.text
            
            if not loc: continue

            # Filter: Keep if Priority Name OR Recently Updated
            is_priority = any(x in loc.lower() for x in ['news', 'post', 'sport', 'history', 'investig'])
            is_recent = False
            
            if lastmod:
                mod_date = make_naive(dateparser.parse(lastmod))
                # Check if sitemap was updated in the last 3 days
                if mod_date and mod_date > (datetime.now() - timedelta(days=3)): 
                    is_recent = True
            
            if is_priority or is_recent:
                category = guess_category_from_url(loc)
                sitemaps_to_scan.append((loc, category))

    return sitemaps_to_scan

def scan_sitemap_file(sm_url, category, status):
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
            
            # STRICT FILTERING
            if url and is_real_article(url):
                valid = False
                
                # A. Sitemap Date
                if dt:
                    dt = make_naive(dt)
                    if dt > YESTERDAY_START: valid = True
                
                # B. URL Date (Fallback)
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

def run_accurate_scan(url):
    status = st.status("🚀 Starting Accurate Scan...", expanded=True)
    
    # 1. RSS
    rss_res = check_rss(url, status)
    
    # 2. Sitemaps
    domain = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    index_paths = [f"{domain}/sitemap.xml", f"{domain}/sitemap_index.xml"]
    
    all_sitemaps = []
    for path in index_paths:
        found = analyze_sitemap_index(path, status)
        if found:
            all_sitemaps = found
            break
            
    # 3. Deep Scan
    sitemap_res = {}
    if all_sitemaps:
        status.update(label=f"🔎 Scanning {len(all_sitemaps)} Sitemap Sections...")
        
        for i, (sm_url, cat) in enumerate(all_sitemaps):
            status.update(label=f"📄 Scanning [{cat}] ({i+1}/{len(all_sitemaps)})...")
            part_res = scan_sitemap_file(sm_url, cat, status)
            sitemap_res.update(part_res)
            
    # Combine
    final_articles = {**sitemap_res, **rss_res}
    
    # Feedback
    if not final_articles:
        status.update(label="❌ Scan Complete: No articles found.", state="error")
        return None, "No articles found for Today or Yesterday."
        
    status.update(label=f"✅ Success! Found {len(final_articles)} articles.", state="complete")
    return final_articles, None

# --- UI ---

st.set_page_config(page_title="Accurate 2-Day Scanner", layout="wide")

st.title("🎯 Accurate 2-Day Scanner")
st.write(f"Strictly counts articles from **Yesterday** and **Today**. Filters out category pages.")

url_input = st.text_input("Website URL", placeholder="https://rayon.in.ua/")

if st.button("Scan Now"):
    if url_input:
        try:
            articles, error = run_accurate_scan(clean_url(url_input))
            
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
                
                st.subheader("📂 By Category")
                cat_counts = df['category'].value_counts().reset_index()
                cat_counts.columns = ['Category', 'Count']
                st.dataframe(cat_counts, use_container_width=True)
                
                with st.expander("View Full Article List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Enter a URL.")
