import streamlit as st
import requests
import feedparser
import trafilatura
import dateparser
import pandas as pd
from datetime import date, datetime
from urllib.parse import urlparse

# --- CONFIGURATION ---
st.set_page_config(page_title="Universal News Analyzer", layout="wide")

# --- CORE LOGIC ---
def get_today_date():
    return date.today()

def check_rss(url):
    base = urlparse(url)
    paths = ['/feed', '/rss', '/feed.xml', '/?feed=rss2', f"{url}?format=rss"] # Added SQSP
    for path in paths:
        feed_url = f"{base.scheme}://{base.netloc}{path}" if path.startswith('/') else path
        try:
            resp = requests.get(feed_url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if resp.status_code == 200 and ('<rss' in resp.text or '<feed' in resp.text):
                return parse_rss(resp.text)
        except: continue
    return []

def parse_rss(xml_text):
    feed = feedparser.parse(xml_text)
    articles = []
    today = get_today_date()
    for entry in feed.entries:
        dt = None
        if hasattr(entry, 'published_parsed') and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6]).date()
        elif hasattr(entry, 'published'):
            dt = dateparser.parse(entry.published).date()
        
        if dt == today:
            articles.append({'url': entry.link, 'title': entry.title, 'date': dt})
    return articles

def analyze_content(url_list):
    data = []
    progress_bar = st.progress(0)
    for i, item in enumerate(url_list):
        progress_bar.progress((i + 1) / len(url_list))
        try:
            downloaded = trafilatura.fetch_url(item['url'])
            if not downloaded: continue
            text = trafilatura.extract(downloaded, favor_precision=True)
            if not text: continue
            
            tokens = len(text) // 4
            data.append({
                'Title': item['title'][:60] + "...",
                'Chars': len(text),
                'Tokens': tokens,
                'URL': item['url']
            })
        except: pass
    progress_bar.empty()
    return data

# --- UI ---
st.title("📰 Universal News Analyzer")
st.write("Enter a news website URL to see exactly what was posted **today**.")

url = st.text_input("Website URL", placeholder="https://most.ks.ua or https://example.com")

if st.button("Analyze Today's Content"):
    if not url:
        st.warning("Please enter a URL")
    else:
        with st.spinner("Scanning for RSS feeds and analyzing..."):
            # 1. Try RSS
            found = check_rss(url)
            
            # 2. If nothing, inform user
            if not found:
                st.error("No RSS feed found for this site, or no articles posted today.")
            else:
                st.success(f"Found {len(found)} articles published today!")
                
                # 3. Analyze content
                df_data = analyze_content(found)
                
                if df_data:
                    df = pd.DataFrame(df_data)
                    
                    # Metrics
                    col1, col2, col3 = st.columns(3)
                    col1.metric("Articles Today", len(df))
                    col2.metric("Total Chars", df['Chars'].sum())
                    col3.metric("Total Tokens (Est)", df['Tokens'].sum())
                    
                    st.dataframe(df, use_container_width=True)
                    
                    # CSV Export
                    csv = df.to_csv(index=False).encode('utf-8')
                    st.download_button("Download CSV", csv, "report.csv", "text/csv")
