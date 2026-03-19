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

# --- HELPER FUNCTIONS ---

def clean_url(url):
    url = url.strip()
    # Keep http if user typed it, otherwise assume https
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

    # 2. Category Words
    last_segment = path.split('/')[-1]
    forbidden_slugs = [
        'promo', 'city', 'news', 'sport', 'science', 'politics', 'world', 
        'society', 'economics', 'culture', 'life', 'style', 'video', 'photo',
        'archive', 'archives', 'author', 'tags', 'tag', 'category', 'page',
        'search', 'feed', 'rss', 'amp', 'ukraine', 'kyiv', 'contacts', 'about'
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
                            if dt > YESTERDAY_START and is_real_article(link):
                                found[link] = {'date': dt, 'category': 'RSS Feed'}
                    if found:
                        status.write(f"✅ RSS: Found {len(found)}.")
                        return found
        except: continue
    return found

def get_sitemaps_to_scan(domain, status):
    """Finds sitemap files, but ONLY keeps those updated recently."""
    status.write("📡 Step 2: Filtering Sitemaps (Speed Boost)...")
    index_paths = [f"{domain}/sitemap.xml", f"{domain}/sitemap_index.xml"]
    valid_sitemaps = []
    
    for path in index_paths:
        try:
            r = requests.get(path, timeout=4, headers={'User-Agent': USER_AGENT})
            if r.status_code != 200: continue
            
            root = ET.fromstring(r.content)
            
            #
