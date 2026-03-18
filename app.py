import streamlit as st
import requests
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import pandas as pd
import trafilatura
import dateparser
import re
from datetime import datetime, timedelta

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7
# Increased limit: check up to 500 individual pages if sitemap lacks dates
MAX_CONTENT_CHECKS = 500 

# --- HELPER FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

def find_date_in_url(url):
    """Attempts to find a date like 2023/10/25 in the URL string."""
    patterns = [
        r'/(\d{4})/(\d{1,2})/(\d{1,2})/', 
        r'/(\d{4})-(\d{1,2})-(\d{1,2})',
    ]
    for pat in patterns:
        match = re.search(pat, url)
        if match:
            date_str = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
            try:
                return dateparser.parse(date_str)
            except:
                pass
    return None

def make_naive(dt):
    """Removes timezone info to prevent comparison errors."""
    if dt is None:
        return None
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def is_likely_article(url):
    """Filter out tags, categories, and other junk to save scan time."""
    url_lower = url.lower()
    # Skip common non-article paths
    skip_patterns = ['/tag/', '/category/', '/author/', '/page/', 
                     '/feed/', '/comment', '/amp/', 'replytocom', 
                     '.jpg', '.png', '.gif', '.pdf']
    for pattern in skip_patterns:
        if pattern in url_lower:
            return False
    return True

# --- MAIN SCRAPER ---

def get_all_articles_aggressive(base_url):
    parsed = urlparse(base_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    sm_paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml", 
                "/news-sitemap.xml", "/sitemap-news.xml"]
    sitemaps = []
    
    # 1. DISCOVER SITEMAPS
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(domain + path)
        except:
            continue

    if not sitemaps:
        try:
            r = requests.get(domain + "/robots.txt", timeout=5)
            for line in r.text.split('\n'):
                if 'Sitemap:' in line:
                    sitemaps.append(line.split('Sitemap:')[1].strip())
        except:
            pass

    if not sitemaps:
        return None

    # 2. GATHER ALL URLS
    processed_sitemaps = set()
    urls_to_process = [] 
    
    progress = st.progress(0, text="Step 1: Gathering all links from sitemap...")
    
    while sitemaps:
        sm = sitemaps.pop(0)
        if sm in processed_sitemaps:
            continue
        processed_sitemaps.add(sm)

        try:
            r = requests.get(sm, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs:
                        if loc: 
                            sitemaps.append(loc)
            
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    loc = None
                    date = None
                    
                    for c in child:
                        if 'loc' in str(c.tag).lower(): 
                            loc = c.text
                        if 'lastmod' in str(c.tag).lower(): 
                            date = c.text
                        # Check for 'news:publication_date' often used in news sitemaps
                        if 'publication_date' in str(c.tag).lower(): 
                            date = c.text
                    
                    if loc:
                        # Filter: only keep URLs that look like articles
                        if is_likely_article(loc):
                            urls_to_process.append((loc, date))
                        
        except Exception:
            continue
    
    # Remove duplicates just in case
    urls_to_process = list(set(urls_to_process))
    progress.progress(100, text=f"Found {len(urls_to_process)} candidate links. Analyzing dates...")

    # 3. DATE FINDING
    cutoff_date = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    
    found_articles = []
    content_checks_done = 0
    
    for i, (url, xml_date) in enumerate(urls_to_process):
        if i % 20 == 0:
            pct = int((i / len(urls_to_process)) * 100)
            progress.progress(pct, text=f"Analyzing {i}/{len(urls_to_process)}...")
        
        final_date = None
        
        # Strategy A: Sitemap Date (Fastest)
        if xml_date:
            try:
                final_date = dateparser.parse(xml_date)
            except:
                pass
        
        # Strategy B: URL Date
        if not final_date:
            final_date = find_date_in_url(url)
            
        # Strategy C: Page Content (Slowest - only if needed)
        if not final_date and content_checks_done < MAX_CONTENT_CHECKS:
            try:
                html = trafilatura.fetch_url(url)
                if html:
                    metadata = trafilatura.extract_metadata(html)
                    if metadata and metadata.date:
                        final_date = dateparser.parse(metadata.date)
                    
                    # Fallback: Search for JSON-LD date if metadata failed
                    if not final_date:
                        # Simple regex to find "datePublished" in HTML source
                        match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
                        if match:
                            final_date = dateparser.parse(match.group(1))
                            
                    content_checks_done += 1
            except:
                pass
        
        # FINAL CHECK
        if final_date:
            final_date = make_naive(final_date)
            
            if final_date > cutoff_date:
                found_articles.append({
                    'url': url,
                    'date': final_date
                })

    progress.empty()
    return found_articles

# --- STREAMLIT UI ---

st.set_page_config(page_title="Deep Article Finder", layout="wide")

st.title("📰 Deep Article Finder")
st.write(f"Scans deeply to find articles from the last **{DAYS_TO_SCAN} days**.")

url_input = st.text_input("Website URL", placeholder="example.com")

if st.button("Find Articles"):
    if url_input:
        clean_input = clean_url(url_input)
        st.info(f"Scanning: `{clean_input}` ... (This may take a minute for large sites)")
        
        try:
            results = get_all_articles_aggressive(clean_input)
            
            if results:
                df = pd.DataFrame(results)
                df['day'] = df['date'].dt.date
                
                # STATS
                st.subheader("📊 Statistics")
                
                total_articles = len(df)
                average = total_articles / DAYS_TO_SCAN
                
                col1, col2 = st.columns(2)
                col1.metric("Total Articles Found", total_articles)
                col2.metric("Average Per Day", f"{average:.2f}")
                
                # COUNT PER DAY
                st.subheader("📅 Count Per Day")
                counts = df['day'].value_counts().sort_index(ascending=False).rename_axis('Date').reset_index(name='Count')
                st.dataframe(counts, use_container_width=True)
                
                # RAW DATA
                with st.expander("See Full List of Articles"):
                    df_display = df.sort_values('date', ascending=False)
                    st.dataframe(df_display[['date', 'url']], use_container_width=True)
                    
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Download CSV", csv, "articles.csv", "text/csv")
            else:
                st.error("Scanned everything but found 0 articles in the last 7 days.")
        
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.error("Please enter a URL.")
