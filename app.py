import streamlit as st
import feedparser
from datetime import datetime, timezone, timedelta

st.set_page_config(page_title="News Article Counter", layout="wide")

st.title("📰 News Media Article Counter (Last 3 Days)")

st.write("Paste RSS feeds. The app will count how many articles were published in the last 3 days.")

# Input
rss_input = st.text_area(
    "Enter RSS feed URLs:",
    placeholder="https://rss.cnn.com/rss/edition.rss\nhttps://feeds.bbci.co.uk/news/rss.xml",
    height=150
)

def analyze_feed(feed_url):
    try:
        feed = feedparser.parse(feed_url)
        now = datetime.now(timezone.utc)
        three_days_ago = now - timedelta(days=3)

        total_count = 0
        daily_counts = {}

        for i in range(3):
            day = (now - timedelta(days=i)).date()
            daily_counts[str(day)] = 0

        for entry in feed.entries:
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

                if published >= three_days_ago:
                    total_count += 1
                    day_str = str(published.date())

                    if day_str in daily_counts:
                        daily_counts[day_str] += 1

        return total_count, daily_counts, None

    except Exception as e:
        return 0, {}, str(e)


if st.button("Analyze"):
    if not rss_input.strip():
        st.warning("Please enter at least one RSS feed URL.")
    else:
        feeds = [line.strip() for line in rss_input.split("\n") if line.strip()]

        with st.spinner("Fetching data..."):
            for feed in feeds:
                total, daily, error = analyze_feed(feed)

                st.markdown(f"## 🔗 {feed}")

                if error:
                    st.error(f"Error: {error}")
                else:
                    st.success(f"Total articles (last 3 days): **{total}**")

                    st.markdown("### 📅 Breakdown by day:")
                    for day, count in sorted(daily.items(), reverse=True):
                        st.write(f"{day} → {count} articles")

                st.divider()
