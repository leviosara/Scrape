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
CURRENT_DATE = datetime.now().date()
# Window: Start from 3 days ago (00:00:00)
START_DATE = datetime.combine(CURRENT_DATE - timedelta(days=3), datetime.min.time())
# Window: End at the start of today (00:00:00) - effectively excluding today
END_DATE = datetime.combine(CURRENT_DATE, datetime.min.time())

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

def is_real_article(url):
    url_lower = url.lower()
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    
    if not path or path == '/': return False
    
    # 1. Extensions
    if any(url_lower.endswith(ext) for ext in ['.jpg', '.png', '.gif', '.pdf', '.css', '.js', '.xml', '.zip']):
        return False

    # 2. STRICT TAG FILTER
    # If the path contains '/tag/' or '/tags/', it is NOT an article.
    # Also blocking 'topic' and 'label' which are common synonyms for tags.
    if any(x in path for x in ['/tag/', '/tags/', '/topic/', '/label/']):
        return False

    # 3. Category Words (Last segment check)
    last_segment = path.split('/')[-1]
    forbidden_slugs = [
        'promo', 'city', 'news', 'sport', 'science', 'politics', 'world', 
        'society', 'economics', 'culture', 'life', 'style', 'video', 'photo',
        'archive', 'archives', 'author', 'category', 'page',
        'search', 'feed', 'rss', 'amp', 'ukraine', 'kyiv', 'contacts', 'about',
        'tag', 'tags', 'topic', 'label'
    ]
    if last_segment in forbidden_slugs: return False

    return True

# --- SCANNING STRATEGIES ---

def check_rss(base_url, status):
    status.write("📡 Step 1: Checking RSS Feeds...")
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
                            if START_DATE <= dt < END_DATE and is_real_article(link):
                                found[link] = {'date': dt, 'category': 'RSS Feed'}
                    if found:
                        status.write(f"✅ RSS: Found {len(found)}.")
                        return found
        except: continue
    return found

def get_sitemaps_to_scan(domain, status):
    status.write("📡 Step 2: Filtering Sitemaps...")
    index_paths = [f"{domain}/sitemap.xml", f"{domain}/sitemap_index.xml"]
    valid_sitemaps = []
    
    for path in index_paths:
        try:
            r = requests.get(path, timeout=4, headers={'User-Agent': USER_AGENT})
            if r.status_code != 200: continue
            
            root = ET.fromstring(r.content)
            
            if 'urlset' in str(root.tag).lower():
                valid_sitemaps.append(path)
                break
            
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    loc = None
                    lastmod = None
                    
                    for x in child:
                        if 'loc' in str(x.tag).lower(): loc = x.text
                        if 'lastmod' in str(x.tag).lower(): lastmod = x.text
                    
                    if not loc: continue
                    
                    skip = False
                    if lastmod:
                        mod_dt = make_naive(dateparser.parse(lastmod))
                        if mod_dt and mod_dt < (datetime.now() - timedelta(days=5)):
                            skip = True
                    
                    if not skip:
                        valid_sitemaps.append(loc)
                break
        except: continue

    if not valid_sitemaps:
        valid_sitemaps.append(f"{domain}/sitemap.xml")
        
    return valid_sitemaps

def scan_sitemaps(sitemap_list, status):
    status.write(f"🔎 Step 3: Scanning {len(sitemap_list)} sitemaps...")
    found = {}
    
    for i, sm_url in enumerate(sitemap_list):
        if i > 25: break 
        
        status.write(f"   Scanning: {sm_url.split('/')[-1]}...")
        
        try:
            r = requests.get(sm_url, timeout=4, headers={'User-Agent': USER_AGENT})
            root = ET.fromstring(r.content)
            
            if 'urlset' not in str(root.tag).lower(): continue

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
                        if START_DATE <= dt < END_DATE: valid = True
                    
                    if not valid:
                        dt_url = find_date_in_url(url)
                        if dt_url:
                            dt_url = make_naive(dt_url)
                            if START_DATE <= dt_url < END_DATE:
                                dt = dt_url
                                valid = True
                    
                    if valid and dt:
                        cat = sm_url.split('/')[-1].replace('.xml', '').replace('-', ' ').title()
                        found[url] = {'date': dt, 'category': cat}
                        
        except: continue
            
    return found

# --- MAIN ---

def run_scan(url):
    status = st.status("🚀 Starting...", expanded=True)
    
    # 1. RSS
    rss_res = check_rss(url, status)
    
    # 2. Sitemaps
    domain = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    sitemaps = get_sitemaps_to_scan(domain, status)
    sitemap_res = scan_sitemaps(sitemaps, status)
    
    # Combine
    final = {**sitemap_res, **rss_res}
    
    if not final:
        status.update(label="❌ No articles found in the last 3 days.", state="error")
        return None
    
    status.update(label=f"✅ Done! Found {len(final)} articles.", state="complete")
    return final

# --- UI ---

st.set_page_config(page_title="3-Day News Scanner", layout="wide")

st.title("📅 3-Day News Scanner")
st.write(f"Scans for articles from **{START_DATE.strftime('%Y-%m-%d')}** to **{(END_DATE - timedelta(seconds=1)).strftime('%Y-%m-%d')}**. Strictly ignores Tags.")

url_input = st.text_input("Website URL", placeholder="https://lb.ua/")

if st.button("Scan"):
    if url_input:
        try:
            res = run_scan(clean_url(url_input))
            if res:
                df = pd.DataFrame.from_dict(res, orient='index').reset_index()
                df.columns = ['url', 'date', 'category']
                df['date'] = pd.to_datetime(df['date'])
                df['day'] = df['date'].dt.date
                
                # Calculate Dates
                day1 = CURRENT_DATE - timedelta(days=1)
                day2 = CURRENT_DATE - timedelta(days=2)
                day3 = CURRENT_DATE - timedelta(days=3)
                
                c1, c2, c3, c4 = st.columns(4)
                c1.metric(f"{day1.strftime('%b %d')}", len(df[df['day'] == day1]))
                c2.metric(f"{day2.strftime('%b %d')}", len(df[df['day'] == day2]))
                c3.metric(f"{day3.strftime('%b %d')}", len(df[df['day'] == day3]))
                c4.metric("Total", len(df))
                
                with st.expander("View List"):
                    st.dataframe(df.sort_values('date', ascending=False))
                    st.download_button("Download", df.to_csv(index=False).encode('utf-8'), "news.csv", "text/csv")
                    
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.warning("Enter URL.")
