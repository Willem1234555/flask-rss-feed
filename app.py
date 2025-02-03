from flask import Flask, render_template, url_for, request, Response
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import or_
import feedparser
from datetime import datetime
import time
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from pytz import timezone

app = Flask(__name__)

# Configure SQLite database
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///articles.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)
amsterdam_tz = timezone("Europe/Amsterdam")

# Global variable to store the time of the last feed update.
last_reload = None

# Define the Article model
class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String, nullable=False)
    link = db.Column(db.String, unique=True, nullable=False)
    published = db.Column(db.DateTime, nullable=True)
    summary = db.Column(db.Text, nullable=True)

def get_feed(url):
    """Fetch RSS feed with a browser-like User-Agent to avoid blocking."""
    headers = {
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/91.0.4472.124 Safari/537.36")
    }
    response = requests.get(url, headers=headers)
    return feedparser.parse(response.text)

def update_feed():
    """Fetch multiple RSS feeds and store new articles in the database."""
    global last_reload  # Update the global variable
    with app.app_context():
        RSS_FEEDS = [
            "https://feeds.nos.nl/nosnieuwsalgemeen",  # NOS
            "https://www.nrc.nl/rss/",                 # NRC
            "https://fd.nl/?rss",                      # fd
            "https://www.nu.nl/rss/algemeen",          # nu.nl
            "https://www.ad.nl/home/rss.xml",          # AD
            "https://www.volkskrant.nl/voorpagina/rss.xml",  # Volkskrant vp
            "https://www.volkskrant.nl/nieuws-achtergrond/rss.xml",  # Volkskrant topverhalen
            "https://www.volkskrant.nl/columns-opinie/rss.xml",  # Volkskrant opinie
            "https://www.telegraaf.nl/rss",            # Telegraaf
            "https://www.trouw.nl/voorpagina/rss.xml"   # Trouw
        ]

        for rss_url in RSS_FEEDS:
            print(f"Fetching feed: {rss_url}")  # Debugging step
            feed = get_feed(rss_url)

            if not feed.entries:
                print(f"Warning: No entries found for {rss_url}")
                continue  # Skip to next feed if this one is empty

            for entry in feed.entries:
                # Ensure the article doesn't already exist in DB
                if Article.query.filter_by(link=entry.link).first():
                    continue

                # Handle different date formats
                if 'pubDate' in entry:
                    try:
                        published_dt = datetime.strptime(
                            entry.pubDate, "%a, %d %b %Y %H:%M:%S %z"
                        ).astimezone(amsterdam_tz)
                    except ValueError:
                        published_dt = datetime.utcnow()
                elif 'published_parsed' in entry and entry.published_parsed:
                    published_dt = datetime.fromtimestamp(time.mktime(entry.published_parsed), amsterdam_tz)
                else:
                    published_dt = datetime.utcnow()

                # Use `summary` if available, else `description`
                summary_text = entry.summary if 'summary' in entry else entry.description if 'description' in entry else ''

                new_article = Article(
                    title=entry.title,
                    link=entry.link,
                    published=published_dt,
                    summary=summary_text
                )
                db.session.add(new_article)

        db.session.commit()
        # Update the last_reload time to now (in Amsterdam time)
        last_reload = datetime.now(amsterdam_tz)
        print(f"Feed updated at {last_reload.strftime('%Y-%m-%d %H:%M:%S')} local time")

@app.route('/')
def index():
    # Query the 25 newest articles from the database
    articles = Article.query.order_by(Article.published.desc()).limit(25).all()
    return render_template('index.html', articles=articles, last_reload=last_reload)

@app.route('/rss')
def rss_feed():
    """
    Generate a custom RSS feed containing the last 10 articles that have the search
    term (from the query parameter 'q') in their title or summary.
    """
    search = request.args.get('q', '').strip()
    
    # If a search term is provided, filter articles by title or summary.
    # Otherwise, just return the last 10 articles.
    if search:
        articles = Article.query.filter(
            or_(
                Article.title.ilike(f'%{search}%'),
                Article.summary.ilike(f'%{search}%')
            )
        ).order_by(Article.published.desc()).limit(10).all()
    else:
        articles = Article.query.order_by(Article.published.desc()).limit(10).all()

    # Build the RSS feed items.
    feed_items = []
    for article in articles:
        # Format the publication date in RSS-friendly format.
        # If the article has no published date, you might want to skip or use a default.
        pub_date = article.published.strftime('%a, %d %b %Y %H:%M:%S %z') if article.published else ''
        
        item = f"""
        <item>
            <title>{article.title}</title>
            <link>{article.link}</link>
            <description><![CDATA[{article.summary}]]></description>
            <pubDate>{pub_date}</pubDate>
            <guid>{article.link}</guid>
        </item>
        """
        feed_items.append(item)

    # Create the complete RSS feed XML.
    rss_feed_xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>My Custom RSS Feed</title>
    <link>http://yourdomain.com/rss</link>
    <description>This is a custom RSS feed generated from our articles.</description>
    {''.join(feed_items)}
  </channel>
</rss>
    """
    return Response(rss_feed_xml, mimetype="application/rss+xml")

if __name__ == '__main__':
    # Set up the database and perform an initial feed update
    with app.app_context():
        db.create_all()
        update_feed()

    # Start the background scheduler AFTER the database has been set up.
    scheduler = BackgroundScheduler()
    scheduler.add_job(func=update_feed, trigger="interval", seconds=60)
    scheduler.start()

    try:
        app.run(debug=True, use_reloader=False)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        scheduler.shutdown()

