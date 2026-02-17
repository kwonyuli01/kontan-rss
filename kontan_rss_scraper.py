#!/usr/bin/env python3
"""
Kontan.co.id Keuangan RSS Feed Scraper with Full Article Content
Dijalankan otomatis via GitHub Actions + publish ke GitHub Pages
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import time
import re
import os
import html
import hashlib

# ============================================================
# KONFIGURASI
# ============================================================

SCRAPE_URLS = [
    "https://keuangan.kontan.co.id/",
]

MAX_ARTICLES = 20
FEED_TITLE = "Kontan.co.id - Keuangan"
FEED_DESCRIPTION = "RSS Feed dari keuangan.kontan.co.id dengan konten artikel lengkap"
FEED_LINK = "https://keuangan.kontan.co.id"
OUTPUT_FILE = "docs/feed.xml"
REQUEST_DELAY = 2
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
})


def fetch_page(url, retries=3):
    for attempt in range(retries):
        try:
            response = session.get(url, timeout=30)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except requests.RequestException as e:
            print(f"  [!] Gagal fetch {url} (percobaan {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(REQUEST_DELAY * 2)
    return None


def parse_list_page(url):
    """Parse halaman keuangan kontan untuk mendapatkan daftar artikel."""
    print(f"\n[*] Scraping halaman list: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, 'lxml')
    articles = []

    # Kontan: artikel link pattern keuangan.kontan.co.id/news/slug
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        title = link.get_text(strip=True)

        if not href or not title:
            continue

        # Filter hanya link artikel kontan keuangan
        if '/news/' not in href:
            continue

        # Pastikan URL lengkap
        if href.startswith('//'):
            href = 'https:' + href
        elif href.startswith('/'):
            href = 'https://keuangan.kontan.co.id' + href

        # Hanya artikel dari keuangan.kontan.co.id
        if 'keuangan.kontan.co.id/news/' not in href:
            continue

        # Skip judul pendek (kemungkinan navigasi)
        if len(title) < 20:
            continue

        # Skip judul yang hanya "#"
        if title.startswith('#'):
            title = title.lstrip('# ').strip()
            if len(title) < 20:
                continue

        # Hindari duplikat
        if any(a['link'] == href for a in articles):
            continue

        articles.append({'title': title, 'link': href})
        if len(articles) >= MAX_ARTICLES:
            break

    print(f"  [+] Ditemukan {len(articles)} artikel")
    return articles


def parse_article_page(url):
    """Parse halaman artikel kontan untuk mendapatkan konten lengkap."""
    print(f"  [>] Mengambil artikel: {url}")
    html_content = fetch_page(url)
    if not html_content:
        return None

    soup = BeautifulSoup(html_content, 'lxml')
    article_data = {}

    # === JUDUL ===
    h1 = soup.find('h1')
    article_data['title'] = h1.get_text(strip=True) if h1 else ''

    # === TANGGAL ===
    # Format kontan: "Selasa, 17 Februari 2026 / 17:41 WIB"
    date_text = ''
    bulan_map = {
        'Januari': '01', 'Februari': '02', 'Maret': '03', 'April': '04',
        'Mei': '05', 'Juni': '06', 'Juli': '07', 'Agustus': '08',
        'September': '09', 'Oktober': '10', 'November': '11', 'Desember': '12',
    }

    for text_node in soup.find_all(string=re.compile(r'\d{1,2}\s+(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember)\s+\d{4}')):
        date_text = text_node.strip()
        break

    article_data['date_text'] = date_text
    article_data['pub_date'] = parse_date(date_text, bulan_map)

    # === REPORTER & EDITOR ===
    reporter = ''
    editor = ''

    # Kontan: "Reporter: **Lydia Tesaloni** | Editor: **Tri Sulistiowati**"
    for tag in soup.find_all(['p', 'span', 'div']):
        text = tag.get_text()
        if 'Reporter:' in text or 'Penulis:' in text:
            bold = tag.find('b') or tag.find('strong')
            if bold:
                reporter = bold.get_text(strip=True)
            else:
                match = re.search(r'Reporter:\s*\**(.+?)(?:\||$)', text)
                if not match:
                    match = re.search(r'Penulis:\s*\**(.+?)(?:\||$)', text)
                if match:
                    reporter = match.group(1).strip().strip('*')
        if 'Editor:' in text:
            # Cari bold kedua atau setelah |
            bolds = tag.find_all(['b', 'strong'])
            if len(bolds) >= 2:
                editor = bolds[1].get_text(strip=True)
            elif len(bolds) == 1 and 'Editor:' in text:
                match = re.search(r'Editor:\s*\**(.+?)(?:\||$)', text)
                if match:
                    editor = match.group(1).strip().strip('*')

    article_data['reporter'] = reporter
    article_data['editor'] = editor

    # === GAMBAR UTAMA ===
    main_image = ''
    for img in soup.find_all('img'):
        src = img.get('src', '')
        if 'foto.kontan.co.id' in src:
            main_image = src
            break
    article_data['image'] = main_image

    # === CAPTION GAMBAR ===
    caption = ''
    if main_image:
        img_tag = soup.find('img', src=main_image)
        if img_tag:
            alt_text = img_tag.get('alt', '')
            if alt_text and len(alt_text) > 10:
                caption = alt_text
            # Juga cek figcaption atau teks setelah gambar
            if not caption:
                next_elem = img_tag.find_next(['figcaption', 'p', 'span'])
                if next_elem and len(next_elem.get_text(strip=True)) < 300:
                    potential = next_elem.get_text(strip=True)
                    if potential and not potential.startswith('KONTAN'):
                        caption = potential
    article_data['caption'] = caption

    # === KONTEN ARTIKEL ===
    content_parts = []
    found_content = False

    for element in soup.find_all(['p', 'h2', 'h3', 'h4', 'li']):
        text = element.get_text(strip=True)
        if not text:
            continue

        # Skip elemen navigasi, sidebar
        parent_classes = ' '.join(element.parent.get('class', []) if element.parent else [])
        grandparent_classes = ''
        if element.parent and element.parent.parent:
            grandparent_classes = ' '.join(element.parent.parent.get('class', []))

        skip_classes = ['sidebar', 'footer', 'nav', 'menu', 'comment', 'trending',
                        'terkait', 'related', 'populer', 'terpopuler']
        if any(skip in parent_classes.lower() for skip in skip_classes):
            continue
        if any(skip in grandparent_classes.lower() for skip in skip_classes):
            continue

        # Skip metadata dan navigasi
        if any(skip in text for skip in ['Reporter:', 'Editor:', 'Penulis:',
                                          'Google News', 'WhatsApp Channel',
                                          'Cek Berita dan Artikel',
                                          'Berita Terkait', 'INDEKS BERITA']):
            continue

        # Skip "Baca Juga:", "Selanjutnya:", "Menarik Dibaca:"
        if re.match(r'^(Baca Juga|Selanjutnya|Menarik Dibaca)\s*:', text):
            continue

        # Skip teks pendek
        if len(text) < 15 and element.name == 'p':
            continue

        # Skip caption
        if text == caption:
            continue

        # Deteksi awal konten artikel (biasanya dimulai KONTAN.CO.ID)
        if re.match(r'^(KONTAN\.CO\.ID|KONTAN\s)', text):
            found_content = True

        if not found_content and len(text) > 40:
            found_content = True

        if found_content:
            clean_text = text.replace('\xa0', ' ').strip()
            if clean_text:
                if element.name in ['h2', 'h3', 'h4']:
                    # Skip heading navigasi
                    if any(skip in text for skip in ['Selanjutnya', 'Menarik Dibaca',
                                                      'Berita Terkait', 'Terpopuler']):
                        continue
                    content_parts.append(f"\n### {clean_text}\n")
                elif element.name == 'li':
                    content_parts.append(f"• {clean_text}")
                else:
                    content_parts.append(clean_text)

    article_data['content'] = '\n\n'.join(content_parts)

    # === TAG ===
    tags = []
    for tag_link in soup.find_all('a', href=re.compile(r'kontan\.co\.id/tag/')):
        tag_text = tag_link.get_text(strip=True)
        if tag_text and len(tag_text) > 1 and tag_text not in ['Tags', 'Tag']:
            clean_tag = tag_text.replace('#', '').strip()
            if clean_tag and clean_tag not in tags:
                tags.append(clean_tag)
    article_data['tags'] = tags

    # === KATEGORI ===
    category = ''
    # Kontan: breadcrumb "KEUANGAN / BANK"
    for a in soup.find_all('a', href=True):
        href = a.get('href', '')
        text = a.get_text(strip=True)
        # Rubrik link: keuangan.kontan.co.id/rubrik/6/Bank
        if '/rubrik/' in href and text:
            category = text
            break
    # Fallback: kategori dari subdomain
    if not category:
        category = 'Keuangan'
    article_data['category'] = category

    return article_data


def parse_date(date_text, bulan_map):
    """Parse tanggal Indonesia ke format RFC 822."""
    if not date_text:
        return datetime.now(timezone(timedelta(hours=7))).strftime('%a, %d %b %Y %H:%M:%S +0700')

    # Format: "Selasa, 17 Februari 2026 / 17:41 WIB"
    match = re.search(r'(\d{1,2})\s+(\w+)\s+(\d{4})\s*/?\s*(\d{2}):(\d{2})', date_text)
    if match:
        day, bulan, year, hour, minute = match.groups()
        month_num = bulan_map.get(bulan, None)
        if month_num:
            try:
                dt = datetime(int(year), int(month_num), int(day), int(hour), int(minute))
                days = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
                months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                          'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
                return f"{days[dt.weekday()]}, {dt.day:02d} {months[dt.month-1]} {dt.year} {dt.hour:02d}:{dt.minute:02d}:00 +0700"
            except ValueError:
                pass

    return datetime.now(timezone(timedelta(hours=7))).strftime('%a, %d %b %Y %H:%M:%S +0700')


def generate_rss(articles_data):
    """Generate file RSS XML dari data artikel."""
    print(f"\n[*] Generating RSS XML...")
    now = datetime.now(timezone(timedelta(hours=7))).strftime('%a, %d %b %Y %H:%M:%S +0700')

    rss_items = []
    for article in articles_data:
        if not article:
            continue

        content_html = ''
        if article.get('image'):
            content_html += f'<p><img src="{html.escape(article["image"])}" alt="{html.escape(article.get("title", ""))}" style="max-width:100%;" /></p>\n'
        if article.get('caption'):
            content_html += f'<p><em>{html.escape(article["caption"])}</em></p>\n'
        if article.get('reporter'):
            content_html += f'<p><strong>Reporter:</strong> {html.escape(article["reporter"])}'
            if article.get('editor'):
                content_html += f' | <strong>Editor:</strong> {html.escape(article["editor"])}'
            content_html += '</p>\n'
        if article.get('content'):
            paragraphs = article['content'].split('\n\n')
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                if para.startswith('### '):
                    content_html += f'<h3>{html.escape(para[4:])}</h3>\n'
                elif para.startswith('• '):
                    content_html += f'<li>{html.escape(para[2:])}</li>\n'
                else:
                    content_html += f'<p>{html.escape(para)}</p>\n'
        if article.get('tags'):
            tags_str = ', '.join(article['tags'])
            content_html += f'<p><strong>Tags:</strong> {html.escape(tags_str)}</p>\n'

        guid = article.get('link', hashlib.md5(article.get('title', '').encode()).hexdigest())

        rss_items.append({
            'title': article.get('title', 'Tanpa Judul'),
            'link': article.get('link', ''),
            'description': content_html,
            'pubDate': article.get('pub_date', now),
            'category': article.get('category', ''),
            'tags': article.get('tags', []),
            'guid': guid,
            'image': article.get('image', ''),
        })

    rss_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:content="http://purl.org/rss/1.0/modules/content/"
     xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>{html.escape(FEED_TITLE)}</title>
    <description>{html.escape(FEED_DESCRIPTION)}</description>
    <link>{html.escape(FEED_LINK)}</link>
    <language>id</language>
    <lastBuildDate>{now}</lastBuildDate>
    <generator>Kontan Keuangan RSS Scraper (GitHub Actions)</generator>
'''

    for item in rss_items:
        rss_xml += f'''    <item>
      <title><![CDATA[{item['title']}]]></title>
      <link>{html.escape(item['link'])}</link>
      <guid isPermaLink="true">{html.escape(item['guid'])}</guid>
      <pubDate>{item['pubDate']}</pubDate>
'''
        if item['category']:
            rss_xml += f'      <category><![CDATA[{item["category"]}]]></category>\n'
        for tag in item.get('tags', []):
            rss_xml += f'      <category><![CDATA[{tag}]]></category>\n'
        if item['image']:
            rss_xml += f'      <media:content url="{html.escape(item["image"])}" medium="image" />\n'
        rss_xml += f'      <description><![CDATA[{item["description"]}]]></description>\n'
        rss_xml += f'      <content:encoded><![CDATA[{item["description"]}]]></content:encoded>\n'
        rss_xml += '    </item>\n'

    rss_xml += '''  </channel>
</rss>'''
    return rss_xml


def main():
    print("=" * 60)
    print("  Kontan.co.id Keuangan RSS Scraper - Full Content")
    print("=" * 60)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    all_articles = []
    for url in SCRAPE_URLS:
        articles = parse_list_page(url)
        all_articles.extend(articles)
        time.sleep(REQUEST_DELAY)

    if not all_articles:
        print("\n[!] Tidak ada artikel ditemukan.")
        return

    # Hapus duplikat
    seen = set()
    unique_articles = []
    for article in all_articles:
        if article['link'] not in seen:
            seen.add(article['link'])
            unique_articles.append(article)

    print(f"\n[*] Total {len(unique_articles)} artikel unik")

    # Fetch konten lengkap
    articles_data = []
    for i, article in enumerate(unique_articles):
        print(f"\n--- Artikel {i+1}/{len(unique_articles)} ---")
        article_data = parse_article_page(article['link'])
        if article_data:
            if not article_data.get('title'):
                article_data['title'] = article['title']
            article_data['link'] = article['link']
            articles_data.append(article_data)
        else:
            articles_data.append({
                'title': article['title'],
                'link': article['link'],
                'content': '(Konten tidak dapat diambil)',
                'pub_date': datetime.now(timezone(timedelta(hours=7))).strftime('%a, %d %b %Y %H:%M:%S +0700'),
                'image': '', 'reporter': '', 'editor': '',
                'tags': [], 'category': '', 'caption': '',
            })
        time.sleep(REQUEST_DELAY)

    # Generate & simpan RSS
    rss_xml = generate_rss(articles_data)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(rss_xml)

    print(f"\n{'=' * 60}")
    print(f"  SELESAI! File: {OUTPUT_FILE}")
    print(f"  Total artikel: {len(articles_data)}")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
