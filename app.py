import streamlit as st
import feedparser
from datetime import datetime, timezone

st.set_page_config(page_title="News Article Counter", layout="wide")

st.title("📰 News Media Daily Article Counter")

st.write("Paste news website RSS feeds (one per line). The app will count how many articles were published today.")

# Input box
rss_input = st.text_area(
    "Enter RSS feed URLs:",
    placeholder="https://rss.cnn.com/rss/edition.rss\nhttps://feeds.bbci.co.uk/news/rss.xml",
    height=150
)

def count_today_articles(feed_url):
    try:
        feed = feedparser.parse(feed_url)
        today = datetime.now(timezone.utc).date()
        count = 0

        for entry in feed.entries:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published_date = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc).date()
                if published_date == today:
                    count += 1

        return count, None
    except Exception as e:
        return 0, str(e)

if st.button("Analyze"):
    if not rss_input.strip():
        st.warning("Please enter at least one RSS feed URL.")
    else:
        feeds = [line.strip() for line in rss_input.split("\n") if line.strip()]

        results = []

        with st.spinner("Fetching data..."):
            for feed in feeds:
                count, error = count_today_articles(feed)

                results.append({
                    "Feed": feed,
                    "Articles Today": count,
                    "Error": error
                })

        st.subheader("📊 Results")

        for res in results:
            st.markdown(f"### 🔗 {res['Feed']}")
            if res["Error"]:
                st.error(f"Error: {res['Error']}")
            else:
                st.success(f"Articles published today: **{res['Articles Today']}**")
