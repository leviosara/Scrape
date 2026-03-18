import streamlit as st
import requests
from urllib.parse import urlparse, urljoin
import xml.etree.ElementTree as ET
import pandas as pd
import trafilatura
import dateparser
import re
from datetime import datetime, timedelta

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7
MAX_CONTENT_CHECKS = 1000  # Increased significantly
MAX_SITEMAP_URLS = 3000

# --- HELPER FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    # Ensure trailing slash for path detection consistency
    if not url.endswith('/'):
        # Check if it's a path or just domain
        parsed = urlparse(url)
        if parsed.path: # It has a path like /en
            url += '/'
    return url

def make_naive(dt):
    if dt is None: return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def find_date_in_url(url):
    # Regex for dates like 2023/10/25 or 2023-10-25
    patterns = [r'/(\d{4})/(\d{1,2})/(\d{1,2})/', r'/(\d{4})-(\d{1,2})-(\d{1,2})']
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            try: return dateparser.parse(date_str)
            except: pass
    return None

def is_likely_article(url):
    url_lower = url.lower()
    skip = ['/tag/', '/category/', '/author/', '/page/', '/feed/', '.jpg', '.png', '.gif', '.pdf', '#respond', 'amp']
    return not any(s in url_lower for s in skip)

def extract_date_from_html(html):
    if not html: return None
    # 1. Trafilatura metadata (Best for content)
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata and metadata.date:
            return dateparser.parse(metadata.date)
    except: pass

    # 2. JSON-LD (Schema.org)
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if match:
        try: return dateparser.parse(match.group(1))
        except: pass
        
    # 3. Open Graph
    match = re.search(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']', html)
    if match:
        try: return dateparser.parse(match.group(1))
        except: pass
        
    return None

# --- MAIN SCRAPER ---

def get_articles(base_url):
    parsed_input = urlparse(base_url)
    domain_root = f"{parsed_input.scheme}://{parsed_input.netloc}"
    
    # We construct sitemap paths based on WHERE the user pointed
    # If user said site.com/en/, we check site.com/en/sitemap.xml AND site.com/sitemap.xml
    specific_sitemap_paths = [
        f"{base_url}sitemap.xml", 
        f"{base_url}sitemap_index.xml",
        f"{base_url}post-sitemap.xml"
    ]
    root_sitemap_paths = [
        f"{domain_root}/sitemap.xml",
        f"{domain_root}/sitemap_index.xml"
    ]
    
    # Combine and deduplicate paths to check
    all_paths = list(set(specific_sitemap_paths + root_sitemap_paths))
    sitemaps = []
    
    # 1. Find Initial Sitemaps
    for path in all_paths:
        try:
            r = requests.get(path, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(path)
        except: continue

    # Fallback: Robots.txt
    try:
        r = requests.get(f"{domain_root}/robots.txt", timeout=5)
        for line in r.text.split('\n'):
            if 'Sitemap:' in line:
                sitemaps.append(line.split('Sitemap:')[1].strip())
    except: pass

    if not sitemaps:
        st.error("No sitemaps found at root or provided path.")
        return []

    # 2. Crawl Sitemaps
    progress = st.progress(0, text="Step 1: Gathering links from sitemaps...")
    processed_sitemaps = set()
    raw_urls = [] # List of (url, date_str)
    
    while sitemaps:
        sm = sitemaps.pop(0)
        if sm in processed_sitemaps: continue
        processed_sitemaps.add(sm)
        
        try:
            r = requests.get(sm, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs: 
                        if loc: sitemaps.append(loc)
            
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    url = None
                    date_str = None
                    for c in child:
                        if 'loc' in str(c.tag).lower(): url = c.text
                        if 'lastmod' in str(c.tag).lower(): date_str = c.text
                        if 'publication_date' in str(c.tag).lower(): date_str = c.text
                    
                    if url and is_likely_article(url):
                        raw_urls.append((url, date_str))
                        
            # Limit check
            if len(raw_urls) > MAX_SITEMAP_URLS: break

        except: continue

    progress.progress(100, text=f"Found {len(raw_urls)} total links.")

    # 3. Analyze Dates
    cutoff_date = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    articles = []
    checked_content = 0
    
    # Sort: Put URLs with dates at the top to process fast ones first
    raw_urls.sort(key=lambda x: x[1] is None)
    
    status_text = st.empty()
    
    for i, (url, sitemap_date) in enumerate(raw_urls):
        # Update UI
        if i % 20 == 0:
            pct = int((i / len(raw_urls)) * 100)
            status_text.text(f"Analyzing {i}/{len(raw_urls)}... | Found so far: {len(articles)}")

        final_date = None
        
        # Method 1: Sitemap Date (Fastest)
        if sitemap_date:
            final_date = dateparser.parse(sitemap_date)
        
        # Method 2: URL Date (Fast)
        if not final_date:
            final_date = find_date_in_url(url)
            
        # Method 3: Content Check (Slow - only if we haven't hit limit)
        if not final_date and checked_content < MAX_CONTENT_CHECKS:
            checked_content += 1
            try:
                html = trafilatura.fetch_url(url)
                final_date = extract_date_from_html(html)
            except: pass
        
        # Filter & Save
        if final_date:
            final_date = make_naive(final_date)
            if final_date > cutoff_date:
                articles.append({'url': url, 'date': final_date})

    status_text.text("Analysis Complete.")
    return articles

# --- STREAMLIT UI ---

st.set_page_config(page_title="Deep Scanner", layout="wide")

st.title("🔎 Deep Article Scanner")
st.write(f"Scans sitemaps deeply to find articles from the last **{DAYS_TO_SCAN} days**.")
st.info("Tip: Enter the specific section URL (e.g., `site.com/en/`) for better accuracy.")

url_input = st.text_input("Website URL", placeholder="https://most.ks.ua/en/")

if st.button("Scan Now"):
    if url_input:
        try:
            results = get_articles(clean_url(url_input))
            
            if results:
                df = pd.DataFrame(results)
                df['day'] = df['date'].dt.date
                
                # Calculate Stats
                total = len(df)
                avg = total / DAYS_TO_SCAN
                
                st.success(f"Success! Found {total} articles.")
                
                c1, c2 = st.columns(2)
                c1.metric("Total Articles (7 Days)", total)
                c2.metric("Average Per Day", f"{avg:.1f}")
                
                st.subheader("📅 Daily Volume")
                counts = df['day'].value_counts().sort_index(ascending=False).rename_axis('Date').reset_index(name='Articles')
                st.dataframe(counts, use_container_width=True)
                
                with st.expander("View Full Article List"):
                    st.dataframe(df.sort_values('date', ascending=False), use_container_width=True)
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Download CSV", csv, "articles.csv", "text/csv")
            else:
                st.error("No articles found in the date range. Try the main domain URL if the sub-link fails.")
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Please enter a URL.")
