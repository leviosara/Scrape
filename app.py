import streamlit as st
import requests
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import pandas as pd
import dateparser
import re
from datetime import datetime, timedelta
import feedparser
import trafilatura

# --- CONFIGURATION ---
CURRENT_DATE = datetime.now().date()
START_DATE = datetime.combine(CURRENT_DATE - timedelta(days=3), datetime.min.time())
END_DATE = datetime.combine(CURRENT_DATE, datetime.min.time())

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
SAMPLE_SIZE = 5

# --- HELPER FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url.rstrip('/')

def normalize_url(url):
    """
    Advanced Deduplication:
    1. Extracts Article ID (e.g., -12345.html). If found, uses ID as key.
    2. If no ID, removes 'www', language prefixes (/uk/, /en/), and query params.
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        
        path = parsed.path.rstrip('/')

        # STRATEGY 1: Unique ID Extraction (Crucial for NV.ua, etc.)
        # Matches numbers typically found at the end of news URLs (5+ digits)
        match = re.search(r'(\d{5,})', path)
        if match:
            # Return a key based purely on domain + article ID
            return f"{netloc}::id_{match.group(1)}"

        # STRATEGY 2: Language/Path Normalization
        # Remove common language prefixes if no ID found
        path = re.sub(r'^/(uk|ru|en|ua|ukr|rus|eng|pol)/', '/', path)
        
        # Return domain + cleaned path
        return f"{netloc}{path}"
    except:
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

def is_real_article(url):
    url_lower = url.lower()
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    
    if not path or path == '/': return False
    if any(url_lower.endswith(ext) for ext in ['.jpg', '.png', '.gif', '.pdf', '.css', '.js', '.xml', '.zip']):
        return False
    
    if any(x in path for x in ['/tag/', '/tags/', '/topic/', '/label/']):
        return False

    last_segment = path.split('/')[-1]
    forbidden_slugs = [
        'promo', 'city', 'news', 'sport', 'science', 'politics', 'world', 
        'society', 'economics', 'culture', 'life', 'style', 'video', 'photo',
        'archive', 'archives', 'author', 'category', 'page',
        'search', 'feed', 'rss', 'amp', 'ukraine', 'kyiv', 'contacts', 'about'
    ]
    if last_segment in forbidden_slugs: return False

    return True

def analyze_content(urls):
    total_chars = 0
    total_words = 0
    successful_checks = 0
    
    for url in urls:
        try:
            downloaded = trafilatura.fetch_url(url)
            if downloaded:
                text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
                if text:
                    total_chars += len(text)
                    total_words += len(text.split())
                    successful_checks += 1
        except: continue
            
    if successful_checks == 0: return None
    
    return {
        'avg_chars': int(total_chars / successful_checks),
        'avg_words': int(total_words / successful_checks),
        'avg_tokens': int((total_chars / successful_checks) / 4),
        'sample_size': successful_checks
    }

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
                            
                            # NORMALIZE URL HERE
                            link_norm = normalize_url(link)
                            
                            if START_DATE <= dt < END_DATE and is_real_article(link_norm):
                                found[link_norm] = {'date': dt, 'category': 'RSS Feed', 'original_url': link}
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
                
                if url:
                    # NORMALIZE URL HERE
                    url_norm = normalize_url(url)
                
                    if url_norm and is_real_article(url_norm):
                        valid = False
                        
                        if dt:
                            dt = make_naive(dt)
                            if START_DATE <= dt < END_DATE: valid = True
                        
                        if not valid:
                            dt_url = find_date_in_url(url_norm)
                            if dt_url:
                                dt_url = make_naive(dt_url)
                                if START_DATE <= dt_url < END_DATE:
                                    dt = dt_url
                                    valid = True
                        
                        if valid and dt:
                            cat = sm_url.split('/')[-1].replace('.xml', '').replace('-', ' ').title()
                            # Store original URL for display, but key is normalized
                            found[url_norm] = {'date': dt, 'category': cat, 'original_url': url}
                        
        except: continue
            
    return found

# --- MAIN ---

def run_scan(url):
    status = st.status("🚀 Starting Scan...", expanded=True)
    
    rss_res = check_rss(url, status)
    
    domain = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    sitemaps = get_sitemaps_to_scan(domain, status)
    sitemap_res = scan_sitemaps(sitemaps, status)
    
    # Combine (Keys are now smart-normalized IDs)
    final = {**sitemap_res, **rss_res}
    
    if not final:
        status.update(label="❌ No articles found in the last 3 days.", state="error")
        return None
    
    status.update(label=f"✅ Scan Complete. Found {len(final)} unique articles.", state="complete")
    return final

# --- UI ---

st.set_page_config(page_title="3-Day News Analyzer", layout="wide")

st.title("📅 3-Day News Analyzer (Smart Deduplication)")
st.write(f"Detects **Article IDs** to prevent duplicates across languages (e.g., NV.ua).")

url_input = st.text_input("Website URL", placeholder="https://nv.ua")

if st.button("Scan & Analyze"):
    if url_input:
        try:
            res = run_scan(clean_url(url_input))
            
            if res:
                # Prepare DataFrame
                data = []
                for key, val in res.items():
                    data.append({
                        'normalized_key': key,
                        'url': val.get('original_url', key), # Keep one original URL for display
                        'date': val['date'],
                        'category': val['category']
                    })
                
                df = pd.DataFrame(data)
                df['date'] = pd.to_datetime(df['date'])
                df['day'] = df['date'].dt.date
                
                # Calculate Dates
                day1 = CURRENT_DATE - timedelta(days=1)
                day2 = CURRENT_DATE - timedelta(days=2)
                day3 = CURRENT_DATE - timedelta(days=3)
                
                # Calculate Volume
                total_count = len(df)
                avg_articles_day = total_count / 3
                
                # Display Volume Metrics
                st.subheader("📊 Article Volume (Unique)")
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric(f"{day1.strftime('%b %d')}", len(df[df['day'] == day1]))
                c2.metric(f"{day2.strftime('%b %d')}", len(df[df['day'] == day2]))
                c3.metric(f"{day3.strftime('%b %d')}", len(df[df['day'] == day3]))
                c4.metric("Total", total_count)
                c5.metric("Avg / Day", f"{avg_articles_day:.1f}")
                
                st.divider()
                
                # Phase 2: Content Analysis
                st.subheader("📝 Content Analysis")
                with st.spinner(f"Analyzing content of top {SAMPLE_SIZE} articles..."):
                    # Use the preserved original URLs for content analysis
                    sample_urls = df.sort_values('date', ascending=False).head(SAMPLE_SIZE)['url'].tolist()
                    content_stats = analyze_content(sample_urls)
                
                if content_stats:
                    avg_tokens_day = int(avg_articles_day * content_stats['avg_tokens'])
                    
                    col1, col2, col3, col4 = st.columns(4)
                    col1.metric("Avg Article Length", f"{content_stats['avg_words']} words")
                    col2.metric("Avg Tokens / Article", content_stats['avg_tokens'])
                    col3.metric("Avg Tokens / DAY", avg_tokens_day)
                    col4.metric("Sample Size", content_stats['sample_size'])
                    
                    st.caption("Deduplication Logic: IDs extracted (e.g. '50592881') or Language Prefixes stripped.")
                else:
                    st.warning("Could not extract content from the sample articles.")
                
                st.divider()
                
                with st.expander("View Full Article List"):
                    st.dataframe(df[['date', 'url', 'category']].sort_values('date', ascending=False), use_container_width=True)
                    st.download_button("Download CSV", df.to_csv(index=False).encode('utf-8'), "news.csv", "text/csv")
                    
        except Exception as e:
            st.error(f"Error: {e}")
    else:
        st.warning("Enter URL.")
