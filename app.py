import streamlit as st
import requests
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
import pandas as pd

# --- CONFIGURATION ---
# Limit results to prevent the app from freezing on huge sites
MAX_URLS = 200 

# --- CORE FUNCTIONS ---

def get_sitemap_urls(base_url):
    parsed = urlparse(base_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    # Common sitemap paths
    sm_paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml"]
    sitemaps = []
    found_urls = []
    
    # 1. Find sitemap
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(domain + path)
                break
        except: 
            continue

    # Fallback to robots.txt
    if not sitemaps:
        try:
            r = requests.get(domain + "/robots.txt", timeout=5)
            for line in r.text.split('\n'):
                if 'Sitemap:' in line:
                    sitemaps.append(line.split('Sitemap:')[1].strip())
        except: 
            pass

    # 2. Parse Sitemap
    processed_sitemaps = set()

    while sitemaps:
        # Stop if we hit the limit (prevents app timeout)
        if len(found_urls) >= MAX_URLS:
            break
            
        sm = sitemaps.pop(0)
        if sm in processed_sitemaps:
            continue
        processed_sitemaps.add(sm)

        try:
            r = requests.get(sm, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            root = ET.fromstring(r.content)
            
            # Check if it is a Sitemap Index (points to other sitemaps)
            if 'sitemapindex' in str(root.tag).lower():
                for child in root:
                    # Fixed Syntax Error here: properly closed brackets
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs:
                        if loc:
                            sitemaps.append(loc)
            
            # Check if it is a URL Set (points to actual pages)
            elif 'urlset' in str(root.tag).lower():
                for child in root:
                    if len(found_urls) >= MAX_URLS:
                        break
                    # Fixed Syntax Error here
                    locs = [c.text for c in child if 'loc' in str(c.tag).lower()]
                    for loc in locs:
                        if loc:
                            found_urls.append(loc)
                            
        except Exception:
            # Ignore errors for individual files
            continue

    return found_urls

# --- STREAMLIT UI ---

st.set_page_config(page_title="Sitemap Scraper", layout="wide")

st.title("🕷️ Sitemap URL Scraper")
st.write("Enter a website URL to extract links from its sitemap.")

url_input = st.text_input("Website URL", placeholder="https://example.com")

if st.button("Scan"):
    if url_input:
        with st.spinner("Scanning sitemap..."):
            try:
                urls = get_sitemap_urls(url_input)
                
                if urls:
                    st.success(f"Found {len(urls)} URLs!")
                    df = pd.DataFrame(urls, columns=["URL"])
                    st.dataframe(df, use_container_width=True)
                else:
                    st.warning("No URLs found. Check if the site has a valid sitemap.")
            except Exception as e:
                st.error(f"An error occurred: {e}")
    else:
        st.error("Please enter a URL.")
