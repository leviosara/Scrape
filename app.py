import streamlit as st
import requests
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import pandas as pd
import trafilatura
import dateparser
from datetime import datetime, timedelta
from collections import defaultdict

# --- CONFIGURATION ---
DAYS_TO_SCAN = 7
MAX_URLS_TO_CHECK_MANUALLY = 50  # Limit for slow content checks

# --- CORE FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url

def get_articles_counts(base_url):
    parsed = urlparse(base_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    sm_paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml"]
    sitemaps = []
    
    # 1. Find Sitemaps
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(domain + path)
                break
        except: continue

    if not sitemaps:
        try:
            r = requests.get(domain + "/robots.txt", timeout=5)
            for line in r.text.split('\n'):
                if 'Sitemap:' in line:
                    sitemaps.append(line.split('Sitemap:')[1].strip())
        except: pass

    if not sitemaps:
        return None

    # 2. Parse and Extract Dates
    processed_sitemaps = set()
    articles_data = []
    manual_check_queue = []
    
    cutoff_date = datetime.now() - timedelta(days=DAYS_TO_SCAN)
    
    progress_bar = st.progress(0, text="Scanning sitemaps...")
    urls_processed = 0

    while sitemaps:
        sm = sitemaps.pop(0)
        if sm in processed_sitemaps: continue
        processed_sitemaps.add(sm)

        try:
            r = requests.get(sm, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            # Sitemap Index (links to other sitemaps)
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs:
                        if loc: sitemaps.append(loc)
            
            # URL Set (actual pages)
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    loc = None
                    lastmod = None
                    
                    for c in child:
                        if 'loc' in str(c.tag).lower(): loc = c.text
                        if 'lastmod' in str(c.tag).lower(): lastmod = c.text
                    
                    if not loc: continue
                    
                    # Strategy 1: Fast path (Sitemap Date)
                    if lastmod:
                        try:
                            art_date = dateparser.parse(lastmod)
                            if art_date and art_date > cutoff_date:
                                articles_data.append({'url': loc, 'date': art_date})
                        except: pass
                    # Strategy 2: Slow path (Check page content) - Limited
                    elif len(manual_check_queue) < MAX_URLS_TO_CHECK_MANUALLY:
                        manual_check_queue.append(loc)
                        
                    urls_processed += 1
                    if urls_processed % 50 == 0:
                        progress_bar.progress(min(urls_processed / 500, 0.9), text=f"Scanned {urls_processed} links...")

        except: continue

    progress_bar.progress(95, text="Checking content dates for recent links...")
    
    # Process manual checks (if needed)
    for loc in manual_check_queue:
        try:
            html = trafilatura.fetch_url(loc)
            if html:
                metadata = trafilatura.extract_metadata(html)
                if metadata and metadata.date:
                    art_date = dateparser.parse(metadata.date)
                    if art_date and art_date > cutoff_date:
                        articles_data.append({'url': loc, 'date': art_date})
        except: pass

    progress_bar.progress(100, text="Done.")
    return articles_data

# --- STREAMLIT UI ---

st.set_page_config(page_title="7-Day Article Counter", layout="wide")

st.title("📊 7-Day Article Analyzer")
st.write(f"Scans a website to find articles posted in the last **{DAYS_TO_SCAN} days**, counts them per day, and calculates the average.")

url_input = st.text_input("Website URL", placeholder="example.com")

if st.button("Analyze Website"):
    if url_input:
        clean_input = clean_url(url_input)
        st.info(f"Analyzing: `{clean_input}`...")
        
        try:
            results = get_articles_counts(clean_input)
            
            if results:
                # Create DataFrame
                df = pd.DataFrame(results)
                
                # Normalize dates (remove time) to group by day
                df['day'] = df['date'].dt.date
                
                # 1. COUNT PER DAY
                st.subheader("📅 Articles Per Day")
                counts = df['day'].value_counts().sort_index(ascending=False).rename_axis('Date').reset_index(name='Articles Found')
                
                # Fill missing days with 0
                today = datetime.now().date()
                all_days = [today - timedelta(days=i) for i in range(DAYS_TO_SCAN)]
                full_counts = pd.DataFrame({'Date': all_days})
                full_counts = full_counts.merge(counts, on='Date', how='left').fillna(0)
                full_counts['Articles Found'] = full_counts['Articles Found'].astype(int)
                
                st.dataframe(full_counts, use_container_width=True)
                
                # 2. CALCULATE AVERAGE
                total_articles = len(df)
                average_articles = total_articles / DAYS_TO_SCAN
                
                col1, col2 = st.columns(2)
                col1.metric("Total Articles (7 Days)", total_articles)
                col2.metric("Average Per Day", f"{average_articles:.2f}")
                
                # 3. SHOW RAW DATA
                with st.expander("View Article List"):
                    st.dataframe(df[['date', 'url']], use_container_width=True)

                # Download
                csv = df.to_csv(index=False).encode('utf-8')
                st.download_button("Download Article List", csv, "articles.csv", "text/csv")
                
            else:
                st.warning("No articles found in the last 7 days. The site might not provide dates or hasn't posted recently.")
                
        except Exception as e:
            st.error(f"An error occurred: {e}")
    else:
        st.error("Please enter a URL.")
