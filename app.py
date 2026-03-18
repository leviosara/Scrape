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
MAX_CONTENT_CHECKS = 150 

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
    # If it has timezone info, remove it (convert to naive)
    if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

# --- MAIN SCRAPER ---

def get_all_articles_aggressive(base_url):
    parsed = urlparse(base_url)
    domain = f"{parsed.scheme}://{parsed.netloc}"
    
    sm_paths = ["/sitemap.xml", "/sitemap_index.xml", "/post-sitemap.xml", "/news-sitemap.xml"]
    sitemaps = []
    
    # 1. DISCOVER SITEMAPS
    for path in sm_paths:
        try:
            r = requests.get(domain + path, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code == 200:
                sitemaps.append(domain + path)
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

    # 2. GATHER ALL URLS
    processed_sitemaps = set()
    urls_to_process = [] 
    
    progress = st.progress(0, text="Step 1: Gathering all links from sitemap...")
    
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
                    loc = None
                    date = None
                    
                    for c in child:
                        if 'loc' in str(c.tag).lower(): loc = c.text
                        if 'lastmod' in str(c.tag).lower(): date = c.text
                    
                    if loc:
                        urls_to_process
