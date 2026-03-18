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
MAX_CONTENT_CHECKS = 1000  # Increased limit to check more pages for dates
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'

# --- HELPER FUNCTIONS ---

def clean_url(url):
    """Ensures URL has a scheme."""
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

def make_naive(dt):
    """Removes timezone info to prevent comparison errors."""
    if dt is None:
        return None
    # If timezone aware, remove timezone info (convert to naive)
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

def find_date_in_url(url):
    """Attempts to find a date like 2023/10/25 in the URL string."""
    patterns = [
        r'/(\d{4})/(\d{1,2})/(\d{1,2})/', 
        r'/(\d{4})-(\d{1,2})-(\d{1,2})'
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

def extract_date_from_html(html):
    """Aggressively search for a date in HTML metadata."""
    if not html:
        return None
    
    # 1. Trafilatura metadata (Best for content)
    try:
        metadata = trafilatura.extract_metadata(html)
        if metadata and metadata.date:
            return dateparser.parse(metadata.date)
    except:
        pass

    # 2. JSON-LD (Schema.org)
    match = re.search(r'"datePublished"\s*:\s*"([^"]+)"', html)
    if match:
        try:
            return dateparser.parse(match.group(1))
        except:
            pass
        
    # 3. Open Graph / Meta tags
    match = re.search(r'<meta[^>]+property=["\']article:published_time["\'][^>]+content=["\']([^"\']+)["\']', html)
    if match:
        try:
            return dateparser.parse(match.group(1))
        except:
            pass
        
    return None

# --- SCANNING STRATEGIES ---

def scan_sitemaps(base_url, status_placeholder):
    """Strategy 1: Scan sitemaps."""
    status_placeholder.text("Strategy 1: Scanning Sitemaps...")
    domain = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    
    # Potential sitemap locations
    paths = [
        f"{base_url}sitemap.xml", 
        f"{base_url}sitemap_index.xml",
        f"{base_url}post-sitemap.xml", 
        f"{base_url}news-sitemap.xml",
        f"{domain}/sitemap.xml", 
        f"{domain}/sitemap_index.xml"
    ]
    
    sitemaps = set()
    found_urls = {} # Dict: url -> date
    
    # Check robots.txt
    try:
        r = requests.get(f"{domain}/robots.txt", timeout=5, headers={'User-Agent': USER_AGENT})
        if r.status_code == 200:
            for line in r.text.split('\n'):
                if 'Sitemap:' in line:
                    sitemaps.add(line.split('Sitemap:')[1].strip())
    except:
        pass

    # Check common paths
    for p in paths:
        try:
            r = requests.get(p, timeout=5, headers={'User-Agent': USER_AGENT})
            if r.status_code == 200:
                sitemaps.add(p)
        except:
            pass

    processed = set()
    while sitemaps:
        sm = sitemaps.pop()
        if sm in processed:
            continue
        processed.add(sm)
        
        try:
            r = requests.get(sm, timeout=5, headers={'User-Agent': USER_AGENT})
            root = ET.fromstring(r.content)
            
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs:
                        if loc:
                            sitemaps.add(loc)
            
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    url = None
                    date_str = None
                    for c in child:
                        if 'loc' in str(c.tag).lower():
                            url = c.text
                        if 'lastmod' in str(c.tag).lower() or 'publication' in str(c.tag).lower():
                            date_str = c.text
                    if url:
                        found_urls[url] = date_str
        except:
            continue

    return found_urls

def scan_homepage(base_url, status_placeholder):
    """Strategy 2: Scan Homepage for links."""
    status_placeholder.text("Strategy 2: Scanning Homepage Links...")
    found_urls = {}
    try:
        r = requests.get(base_url, timeout=10, headers={'User-Agent': USER_AGENT})
        links = re.findall(r'href=["\']([^"\']+)["\']', r.text)
        
        domain_netloc = urlparse(base_url).netloc
        for link in links:
            full_link = urljoin(base_url, link)
            # Filter: keep only internal links, skip obvious junk
            if urlparse(full_link).netloc == domain_netloc:
                if not any(x in full_link.lower() for x in ['.jpg', '.png', '.css', '.js', '/tag/', '/category/', '/page/']):
                    found_urls[full_link] = None # No date from homepage
    except:
        pass
    return found_urls

# --- MAIN ORCHESTRATOR ---

def run_analysis(url):
    cutoff = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    
    # 1. Gather Candidates
    status = st.empty()
    sitemap_candidates = scan_sitemaps(url, status)
    homepage_candidates = scan_homepage(url, status)
    
    # Combine: Start with sitemap data, add homepage data if not present
    all_candidates = sitemap_candidates.copy()
    for u, d in homepage_candidates.items():
        if u not in all_candidates:
            all_candidates[u] = d
            
    candidates_list = list(all_candidates.items()) # [(url, date), ...]
    
    st.info(f"Found {len(candidates_list)} total unique links. Checking dates...")
    
    # 2. Filter by Date
    results = []
    progress = st.progress(0)
    content_checks = 0
    
    for i, (link, sitemap_date) in enumerate(candidates_list):
        # Update UI progress
        if i % 20 == 0:
            progress.progress(int((i / len(candidates_list)) * 100), text=f"Processing {i}/{len(candidates_list)}...")
        
        final_date = None
        
        # Method 1: Sitemap Date (Fastest)
        if sitemap_date:
            final_date = dateparser.parse(sitemap_date)
        
        # Method 2: URL Date (Fast)
        if not final_date:
            final_date = find_date_in_url(link)
            
        # Method 3: Content Check (Slow - only if needed and within limit)
        if not final_date and content_checks < MAX_CONTENT_CHECKS:
            content_checks += 1
            try:
                html = trafilatura.fetch_url(link)
                final_date = extract_date_from_html(html)
            except:
                pass
        
        # Final Check: Is it within our range?
        if final_date:
            final_date = make_naive(final_date) # Fix timezone errors
            if final_date > cutoff:
                results.append({
                    'url': link,
                    'date': final_date
                })

    progress.empty()
    status.empty()
    return results

# --- STREAMLIT UI ---

st.set_page_config(page_title="Complete Article Finder", layout="wide")

st.title("📡 Complete Article Finder")
st.write(f"Scans both **Sitemap** and **Homepage** to find all articles from the last **{DAYS_TO_SCAN} days**.")

url_input = st.text_input("Website URL", placeholder="https://most.ks.ua/en/")

if st.button("Deep Scan"):
    if url_input:
        try:
            results = run_analysis(clean_url(url_input))
            
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
                st.error("No articles found. The site might use a format we can't read, or has no recent content.")
        except Exception as e:
            st.error(f"Critical Error: {e}")
    else:
        st.warning("Please enter a URL.")
