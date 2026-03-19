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
TODAY = datetime.now().date()
YESTERDAY_START = datetime.combine(TODAY - timedelta(days=1), datetime.min.time())
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
MAX_SITEMAPS = 50 

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
    """
    CALIBRATED FILTER v2:
    1. Check if the URL ends with a 'Category Word' (promo, city, news).
       If yes -> DISCARD (It's a category page).
    2. Check if it's the Homepage -> DISCARD.
    3. Check extensions -> DISCARD.
    """
    url_lower = url.lower()
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    
    # 1. Homepage Check
    if not path or path == '/': 
        return False

    # 2. Extension Check
    bad_extensions = ['.jpg', '.png', '.gif', '.pdf', '.css', '.js', '.xml', '.zip']
    if any(url_lower.endswith(ext) for ext in bad_extensions):
        return False

    # 3. Category Slug Check (The Fix)
    # Get the last part of the URL
    last_segment = path.split('/')[-1]
    
    # List of words that indicate a CATEGORY page, not an article
    # Added 'promo', 'city' based on your feedback, plus standard news categories
    forbidden_slugs = [
        'promo', 'city', 'news', 'sport', 'science', 'politics', 'world', 
        'society', 'economics', 'culture', 'life', 'style', 'video', 'photo',
        'archive', 'archives', 'author', 'tags', 'tag', 'category', 'page',
        'search', 'feed', 'rss', 'amp', 'ukraine', 'kyiv', 'contacts', 'about'
    ]
    
    if last_segment in forbidden_slugs:
        return False

    # 4. Pagination Check
    if 'page/' in url_lower or re.search(r'/page/\d+', url_lower):
        return False

    return True

def guess_category_from_url(sitemap_url):
    filename = sitemap_url.split('/')[-1].lower()
    if 'news' in filename: return 'News'
    if 'sport' in filename: return 'Sport'
    if 'post' in filename: return 'Posts'
    if 'article' in filename: return 'Articles'
    return 'Main'

# --- SCANNING STRATEGIES ---

def check_rss(base_url, status):
    status.update(label="📡 Step 1: Checking RSS Feeds...")
    found = {}
    paths = [
        f"{base_url}/feed/", f"{base_url}/rss/", 
        f"{base_url}/en/feed/", f"{base_url}/uk/feed/",
        f"{base_url}/news/feed/"
    ]
    
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
                            if dt > YESTERDAY_START and is_real_article(link):
                                found[link] = {'date': dt, 'category': 'RSS Feed'}
                    if found:
                        status.update(label=f"✅ RSS: Found {len(found)} articles.")
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

            is_priority = any(x in loc.lower() for x in ['news', 'post', 'sport', 'history', 'investig', 'article'])
            is_recent = False
            
            if lastmod:
                mod_date = make_naive(dateparser.parse(lastmod))
                if mod_date and mod_date > (datetime.now() - timedelta(days=14)): 
                    is_recent = True
            
            if is_priority or is_recent or (not lastmod):
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
            
            # Apply Filter
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

def run_calibrated_scan(url):
    status = st.status("🚀 Starting Calibrated Scan...", expanded=True)
    
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
        all_sitemaps = all_sitemaps[:MAX_SITEMAPS]
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
        return None, "Scan complete but found no articles matching criteria."
        
    status.update(label=f"✅ Success! Found {len(final_articles)} articles.", state="complete")
    return final_articles, None

# --- UI ---

st.set_page_config(page_title="Calibrated News Scanner", layout="wide")

st.title("🎯 Calibrated News Scanner")
st.write(f"Excludes category pages (promo, city, news folders). Strict Today/Yesterday count.")

url_input = st.text_input("Website URL", placeholder="https://cukr.city/")

if st.button("Run Calibrated Scan"):
    if url_input:
        try:
            articles, error = run_calibrated_scan(clean_url(url_input))
            
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
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Download CSV", csv, "calibrated_scan.csv", "text/csv")
                    
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Enter a URL.")
