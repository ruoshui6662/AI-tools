"""
中国新闻源专用抓取器
直接从各媒体官网抓取，不依赖第三方RSSHub。
"""

import json
import re
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
CLIENT = {"verify": False, "timeout": 15, "follow_redirects": True}
NOW = datetime.now()
TODAY = NOW.strftime("%Y-%m-%d")


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def extract_links(soup, selectors, source, base_url="", category="综合"):
    """通用链接提取器"""
    items = []
    seen = set()
    for sel in selectors:
        for a in soup.select(sel):
            title = clean(a.get_text())
            if not title or len(title) < 8 or title in seen:
                continue
            href = a.get("href", "")
            if not href:
                continue
            if not href.startswith("http"):
                href = base_url.rstrip("/") + "/" + href.lstrip("/")
            # 过滤非新闻链接
            if any(x in href for x in [".css", ".js", ".apk", "javascript:", "#", "licence"]):
                continue
            seen.add(title)
            items.append({
                "title": title,
                "link": href,
                "source": source,
                "tier": 1,
                "lang": "zh",
                "datetime": TODAY,
                "summary": "",
                "category_hint": category,
            })
    return items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  各源专用抓取器
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scrape_xinhua():
    """新华网 — 首页 + 时政 + 国际 + 财经"""
    items = []
    pages = [
        ("http://www.news.cn/politics/", "政治"),
        ("http://www.news.cn/world/", "国际"),
        ("http://www.news.cn/fortune/", "经济"),
        ("http://www.news.cn/mil/", "军事"),
        ("http://www.news.cn/tech/", "科技"),
    ]
    for url, cat in pages:
        try:
            r = httpx.get(url, headers=HEADERS, **CLIENT)
            soup = BeautifulSoup(r.text, "html.parser")
            found = extract_links(soup, ["h3 a", "h2 a", "li a[href*='/202']", ".news-item a"],
                                  "新华社", "http://www.news.cn", cat)
            items.extend(found[:15])
        except Exception:
            pass
    return items


def scrape_cctv():
    """央视新闻 — 首页"""
    items = []
    try:
        r = httpx.get("https://news.cctv.com/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, ["h3 a", ".news_title a", "a[href*='ARTI']"],
                              "央视新闻", "https://news.cctv.com")
    except Exception:
        pass
    return items[:20]


def scrape_people():
    """人民网 — 首页 + 国际 + 财经"""
    items = []
    pages = [
        ("http://www.people.com.cn/", "综合"),
        ("http://world.people.com.cn/", "国际"),
        ("http://finance.people.com.cn/", "经济"),
        ("http://military.people.com.cn/", "军事"),
    ]
    for url, cat in pages:
        try:
            r = httpx.get(url, headers=HEADERS, **CLIENT)
            soup = BeautifulSoup(r.text, "html.parser")
            found = extract_links(soup, ["h1 a", "h2 a", "h3 a", ".ej_list_box a"],
                                  "人民网", "", cat)
            items.extend(found[:10])
        except Exception:
            pass
    return items


def scrape_huanqiu():
    """环球网 — 首页"""
    items = []
    try:
        r = httpx.get("https://www.huanqiu.com/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, [
            "h1 a[href*='article']",
            "h2 a[href*='article']",
            "h3 a[href*='article']",
            "a[href*='/article/']",
        ], "环球网", "https://www.huanqiu.com")
    except Exception:
        pass
    return items[:20]


def scrape_thepaper():
    """澎湃新闻 — 首页"""
    items = []
    try:
        r = httpx.get("https://www.thepaper.cn/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, [
            "a[href*='newsDetail_forward']",
            "h2 a", "h3 a", ".news_li a",
        ], "澎湃新闻", "https://www.thepaper.cn")
    except Exception:
        pass
    return items[:20]


def scrape_guancha():
    """观察者网 — 首页"""
    items = []
    try:
        r = httpx.get("https://www.guancha.cn/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, [
            "h3 a[href*='.shtml']",
            "a[href*='guancha.cn/'][href*='.shtml']",
            ".news-list a", ".list-item a",
        ], "观察者网", "https://www.guancha.cn")
    except Exception:
        pass
    return items[:20]


def scrape_jiemian():
    """界面新闻 — 首页"""
    items = []
    try:
        r = httpx.get("https://www.jiemian.com/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, [
            "a[href*='/article/']",
            "h2 a", "h3 a",
        ], "界面新闻", "https://www.jiemian.com")
    except Exception:
        pass
    return items[:20]


def scrape_caixin():
    """财新 — 首页"""
    items = []
    try:
        r = httpx.get("https://www.caixin.com/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, [
            "a[href*='caixin.com/'][href*='-']",
            "h2 a", "h3 a", ".news_item a",
        ], "财新", "https://www.caixin.com")
    except Exception:
        pass
    return items[:15]


def scrape_sina():
    """新浪新闻 — 首页"""
    items = []
    try:
        r = httpx.get("https://news.sina.com.cn/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, [
            "h1 a[href*='.shtml']",
            "h2 a[href*='.shtml']",
            "h3 a[href*='.shtml']",
            "a[href*='news.sina.com.cn/'][href*='.shtml']",
        ], "新浪新闻", "https://news.sina.com.cn")
    except Exception:
        pass
    return items[:20]


def scrape_chinanews():
    """中国新闻网 — 首页"""
    items = []
    try:
        r = httpx.get("https://www.chinanews.com.cn/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, [
            "a[href*='chinanews.com.cn/'][href*='.shtml']",
            "h2 a", "h3 a", ".content_list a",
        ], "中新网", "https://www.chinanews.com.cn")
    except Exception:
        pass
    return items[:20]


def scrape_cankaoxiaoxi():
    """参考消息 — 首页（国际新闻为主）"""
    items = []
    try:
        r = httpx.get("https://www.cankaoxiaoxi.com/", headers=HEADERS, **CLIENT)
        soup = BeautifulSoup(r.text, "html.parser")
        items = extract_links(soup, [
            "a[href*='cankaoxiaoxi.com/']",
            "h2 a", "h3 a",
        ], "参考消息", "https://www.cankaoxiaoxi.com")
    except Exception:
        pass
    return items[:15]


def scrape_weibo_hot():
    """微博热搜（通过API）"""
    items = []
    try:
        r = httpx.get("https://weibo.com/ajax/side/hotSearch",
                      headers=HEADERS, verify=False, timeout=10)
        data = r.json()
        for item in data.get("data", {}).get("realtime", [])[:20]:
            word = item.get("word", "")
            if not word:
                continue
            items.append({
                "title": f"[微博热搜] {word}",
                "link": f"https://s.weibo.com/weibo?q=%23{word}%23",
                "source": "微博热搜",
                "tier": 2,
                "lang": "zh",
                "datetime": TODAY,
                "summary": item.get("note", ""),
                "category_hint": "综合",
            })
    except Exception:
        pass
    return items


def scrape_zhihu_hot():
    """知乎热榜"""
    items = []
    try:
        r = httpx.get("https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total",
                      headers={**HEADERS, "Referer": "https://www.zhihu.com/hot"},
                      verify=False, timeout=10)
        data = r.json()
        for item in data.get("data", [])[:20]:
            target = item.get("target", {})
            title = target.get("title", "")
            if not title:
                continue
            items.append({
                "title": f"[知乎热榜] {title}",
                "link": f"https://www.zhihu.com/question/{target.get('id', '')}",
                "source": "知乎热榜",
                "tier": 2,
                "lang": "zh",
                "datetime": TODAY,
                "summary": target.get("excerpt", "")[:200],
                "category_hint": "综合",
            })
    except Exception:
        pass
    return items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  统一入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ALL_SCRAPERS = [
    ("新华社", scrape_xinhua),
    ("央视新闻", scrape_cctv),
    ("人民网", scrape_people),
    ("环球网", scrape_huanqiu),
    ("澎湃新闻", scrape_thepaper),
    ("观察者网", scrape_guancha),
    ("界面新闻", scrape_jiemian),
    ("财新", scrape_caixin),
    ("新浪新闻", scrape_sina),
    ("中新网", scrape_chinanews),
    ("参考消息", scrape_cankaoxiaoxi),
    ("微博热搜", scrape_weibo_hot),
    ("知乎热榜", scrape_zhihu_hot),
]


def fetch_all_cn():
    """并行抓取所有中国新闻源"""
    all_items = []
    print(f"  🇨🇳 并行抓取 {len(ALL_SCRAPERS)} 个中国信源...")

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fn): name for name, fn in ALL_SCRAPERS}
        for future in as_completed(futures):
            name = futures[future]
            try:
                items = future.result()
                if items:
                    print(f"    ✅ {name}: {len(items)}条")
                    all_items.extend(items)
                else:
                    print(f"    ⚠ {name}: 0条")
            except Exception as e:
                print(f"    ❌ {name}: {str(e)[:60]}")

    return all_items


if __name__ == "__main__":
    items = fetch_all_cn()
    print(f"\n  📦 中国信源总量: {len(items)}条")
    for i, item in enumerate(items[:10], 1):
        print(f"  {i}. [{item['source']}] {item['title'][:50]}")
