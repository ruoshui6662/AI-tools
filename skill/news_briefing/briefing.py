#!/usr/bin/env python3
"""
全球新闻简报 — News Briefing Skill v2.1
每隔2小时抓取全球重要新闻，分类整理后推送到飞书。

v2.1 新增：
- 中国信源直连抓取（新华社/央视/人民日报/环球网/澎湃/财新等13源）
- 不再依赖RSSHub抓取中国媒体

v2.0：
- 海外信源自动翻译为中文
- 多源交叉验证，标注核实状态
- 存疑信息标注提醒
"""

import json
import os
import re
import subprocess
import hashlib
import warnings
from datetime import datetime, timedelta
from pathlib import Path
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed

import httpx
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

# 中国信源直连抓取模块
from cn_sources import fetch_all_cn

warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
CACHE_FILE = BASE_DIR / "news_cache.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
}
CLIENT = {"verify": False, "timeout": 20, "follow_redirects": True}

TIME_CUTOFF = (datetime.now() - timedelta(hours=24)).isoformat()[:16]
NOW = datetime.now()


def load_config():
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_cache():
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
            return {k: v for k, v in data.items() if v.get("time", "") > cutoff}
        except Exception:
            return {}
    return {}


def save_cache(cache):
    save_json(CACHE_FILE, cache)


def news_fingerprint(title, source=""):
    clean = re.sub(r'[^\w\s]', '', title.lower().strip())
    clean = re.sub(r'\s+', ' ', clean)
    return hashlib.md5(f"{source}:{clean}".encode()).hexdigest()[:12]


def title_similarity(a, b):
    a_clean = re.sub(r'[^\w\s]', '', a.lower())
    b_clean = re.sub(r'[^\w\s]', '', b.lower())
    return SequenceMatcher(None, a_clean, b_clean).ratio()


def clean_html(text):
    return re.sub(r'<[^>]+>', '', text).strip()


def parse_time(dt_str):
    if not dt_str:
        return None
    for fmt in [
        "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S", "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT", "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ]:
        try:
            return datetime.strptime(dt_str.strip(), fmt)
        except ValueError:
            continue
    return None


def is_recent(dt_str, hours=24):
    if not dt_str:
        return True
    dt = parse_time(dt_str)
    if not dt:
        return True
    if dt.tzinfo:
        dt = dt.replace(tzinfo=None)
    return dt >= (datetime.now() - timedelta(hours=hours))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  分类引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def classify_news(title, summary="", config=None):
    if not config:
        config = load_config()
    text = f"{title} {summary}".lower()
    categories = config.get("categories", {})
    scores = {}
    for cat_id, cat_info in categories.items():
        keywords = cat_info.get("keywords", [])
        hits = sum(1 for kw in keywords if kw.lower() in text)
        if hits > 0:
            scores[cat_id] = hits
    if not scores:
        return "other", 0, 0
    best_cat = max(scores, key=scores.get)
    return best_cat, scores[best_cat], scores[best_cat]


def is_china_related(title, summary=""):
    china_kw = ["中国", "china", "北京", "beijing", "上海", "shanghai", "台湾", "taiwan",
                "香港", "hong kong", "华为", "huawei", "小米", "xiaomi", "中共", "习近平",
                "xi jinping", "人民币", "rmb", "台海", "南海", "south china sea"]
    text = f"{title} {summary}".lower()
    return any(kw in text for kw in china_kw)


def is_breaking(title):
    breaking_kw = ["突发", "快讯", "breaking", "just in", "alert", "紧急", "速报",
                   "刚刚", "confirmed", "证实", "爆炸", "坠机", "地震", "暗杀", "assass"]
    return any(kw in title.lower() for kw in breaking_kw)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  RSS 抓取引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_rss(feed_info):
    name = feed_info["name"]
    url = feed_info["url"]
    tier = feed_info.get("tier", 2)
    lang = feed_info.get("lang", "en")
    items = []
    try:
        with httpx.Client(**CLIENT) as client:
            resp = client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "html.parser")
            entries = soup.find_all("item") or soup.find_all("entry")
            for entry in entries[:30]:
                title_tag = entry.find("title")
                title = clean_html(title_tag.get_text()) if title_tag else ""
                if not title:
                    continue
                link = ""
                link_tag = entry.find("link")
                if link_tag:
                    link = link_tag.get("href", "") or link_tag.get_text()
                if not link:
                    guid = entry.find("guid")
                    if guid:
                        link = guid.get_text()
                pub_date = ""
                for tag_name in ["pubdate", "published", "updated", "dc:date", "date"]:
                    dt_tag = entry.find(tag_name)
                    if dt_tag:
                        pub_date = dt_tag.get_text().strip()
                        break
                summary = ""
                for tag_name in ["description", "summary", "content", "content:encoded"]:
                    desc_tag = entry.find(tag_name)
                    if desc_tag:
                        summary = clean_html(desc_tag.get_text())[:500]
                        break
                if not is_recent(pub_date, hours=24):
                    continue
                items.append({
                    "title": title, "link": link, "source": name,
                    "tier": tier, "lang": lang,
                    "datetime": pub_date[:25] if pub_date else "",
                    "summary": summary,
                })
    except Exception as e:
        print(f"  \u26a0 {name}: {e}")
    return items


def fetch_all_rss(config):
    all_feeds = []
    rss_feeds = config.get("rss_feeds", {})
    for category, feeds in rss_feeds.items():
        all_feeds.extend(feeds)
    print(f"[{NOW.strftime('%H:%M:%S')}] \u5e76\u884c\u6293\u53d6 {len(all_feeds)} \u4e2aRSS\u6e90...")
    all_items = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_rss, feed): feed["name"] for feed in all_feeds}
        for future in as_completed(futures):
            name = futures[future]
            try:
                items = future.result()
                if items:
                    print(f"  \u2705 {name}: {len(items)}\u6761")
                    all_items.extend(items)
                else:
                    print(f"  \u26a0 {name}: 0\u6761")
            except Exception as e:
                print(f"  \u274c {name}: {e}")
    return all_items


def fetch_trending():
    items = []
    trending_sources = [
        ("\u5fae\u535a\u70ed\u641c", "https://rsshub.app/weibo/search/hot"),
        ("\u77e5\u4e4e\u70ed\u699c", "https://rsshub.app/zhihu/hotlist"),
        ("HackerNews", "https://rsshub.app/hackernews/best"),
        ("36\u6c2a", "https://rsshub.app/36kr/newsflashes"),
        ("IT\u4e4b\u5bb6", "https://rsshub.app/ithome/ranking"),
    ]
    for name, url in trending_sources:
        try:
            with httpx.Client(**CLIENT) as client:
                resp = client.get(url, headers=HEADERS, timeout=15)
                if resp.status_code != 200:
                    continue
                soup = BeautifulSoup(resp.text, "html.parser")
                entries = soup.find_all("item") or soup.find_all("entry")
                for entry in entries[:15]:
                    title_tag = entry.find("title")
                    title = clean_html(title_tag.get_text()) if title_tag else ""
                    if not title or len(title) < 5:
                        continue
                    link = ""
                    link_tag = entry.find("link")
                    if link_tag:
                        link = link_tag.get("href", "") or link_tag.get_text()
                    pub_date = ""
                    for tag in ["pubdate", "published", "updated"]:
                        dt = entry.find(tag)
                        if dt:
                            pub_date = dt.get_text().strip()
                            break
                    items.append({
                        "title": title, "link": link, "source": name,
                        "tier": 3, "lang": "zh" if "\u5fae\u535a" in name or "\u77e5\u4e4e" in name else "en",
                        "datetime": pub_date[:25] if pub_date else "", "summary": "",
                    })
        except Exception as e:
            print(f"  \u26a0 {name}: {e}")
    return items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LLM 翻译引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def translate_with_llm(items):
    """用 LLM 批量翻译英文新闻标题和摘要为中文"""
    en_items = [item for item in items if item.get("lang") == "en"
                and not has_chinese(item["title"])]
    if not en_items:
        return items

    print(f"  📤 翻译 {len(en_items)} 条英文新闻...")

    batch_size = 12
    for i in range(0, len(en_items), batch_size):
        batch = en_items[i:i + batch_size]
        prompt_lines = [
            "将以下英文新闻标题翻译为中文。",
            "要求：简洁准确，保持新闻标题风格，专业术语用常见中文译法。",
            "只返回 JSON 数组，格式：[{\"id\": 0, \"title\": \"翻译\", \"summary\": \"翻译\"}, ...]",
            "不要任何解释，只返回 JSON。",
            "",
        ]
        for idx, item in enumerate(batch):
            summary_part = f" | {item['summary'][:200]}" if item.get("summary") else ""
            prompt_lines.append(f"[{idx}] {item['title']}{summary_part}")

        prompt = "\n".join(prompt_lines)

        try:
            response_text = _call_llm(prompt)
            if response_text:
                json_match = re.search(r'\[.*\]', response_text, re.DOTALL)
                if json_match:
                    translations = json.loads(json_match.group())
                    trans_map = {t["id"]: t for t in translations if "id" in t}
                    for idx, item in enumerate(batch):
                        if idx in trans_map:
                            t = trans_map[idx]
                            item["title_zh"] = t.get("title", "")
                            item["summary_zh"] = t.get("summary", "")
                            print(f"    ✅ [{idx}] {item['title'][:35]}... → {item['title_zh'][:35]}")
                        else:
                            print(f"    ⚠ [{idx}] 翻译结果缺失")
                else:
                    print(f"  ⚠ 翻译返回格式异常：{response_text[:100]}")
            else:
                print(f"  ⚠ 翻译调用失败")
        except json.JSONDecodeError as e:
            print(f"  ⚠ 翻译JSON解析失败: {e}")
        except Exception as e:
            print(f"  ⚠ 翻译异常: {e}")

    return items


def _call_llm(prompt, timeout=90):
    """调用内部 LLM API，返回文本结果"""
    try:
        r = httpx.post(
            'http://127.0.0.1:8088/api/agent/process',
            json={
                'agent_id': 'default',
                'input': [{'role': 'user', 'type': 'message',
                           'content': [{'type': 'text', 'text': prompt}]}],
                'session_id': f'briefing_tr_{int(datetime.now().timestamp())}'
            },
            timeout=timeout
        )
        if r.status_code != 200:
            return None

        lines = [l for l in r.text.split(chr(10)) if l.startswith('data: ')]
        for line in reversed(lines):
            try:
                d = json.loads(line[6:])
                if d.get('status') == 'completed' and d.get('output'):
                    for msg in d['output']:
                        if msg.get('type') == 'message' and msg.get('content'):
                            for c in msg['content']:
                                if c.get('type') == 'text' and c.get('text'):
                                    return c['text']
            except (json.JSONDecodeError, KeyError):
                continue
        return None
    except Exception as e:
        print(f"  ⚠ LLM API 异常: {e}")
        return None



def has_chinese(text):
    """检测文本是否包含中文"""
    return bool(re.search(r'[\u4e00-\u9fff]', text))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  交叉验证引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cross_verify(items):
    """多源交叉验证：相似报道聚类，标注核实状态"""
    if not items:
        return items

    print(f"  \U0001F50D \u4ea4\u53c9\u9a8c\u8bc1 {len(items)} \u6761\u65b0\u95fb...")

    # 按相似度聚类
    clusters = []  # [{items: [...], sources: set()}]
    for item in items:
        matched = False
        for cluster in clusters:
            # 与聚类中任一成员标题相似即归入
            for existing in cluster["items"]:
                if title_similarity(item["title"], existing["title"]) > 0.5:
                    cluster["items"].append(item)
                    cluster["sources"].add(item["source"])
                    matched = True
                    break
            if matched:
                break
        if not matched:
            clusters.append({"items": [item], "sources": {item["source"]}})

    # 标注核实状态
    verified_count = 0
    caution_count = 0
    for cluster in clusters:
        source_count = len(cluster["sources"])
        for item in cluster["items"]:
            item["source_count"] = source_count
            item["cross_sources"] = list(cluster["sources"])

            if source_count >= 3:
                # 3+ 独立源报道 — 高度可信
                item["verify_status"] = "verified_high"
                item["verify_badge"] = "\u2705 \u5df2\u6838\u5b9e\uff08\u591a\u6e90\u786e\u8ba4\uff09"
                verified_count += 1
            elif source_count >= 2:
                # 2 个独立源 — 已交叉验证
                item["verify_status"] = "verified"
                item["verify_badge"] = "\u2705 \u5df2\u4ea4\u53c9\u9a8c\u8bc1"
                verified_count += 1
            else:
                # 单源 — 需要留意
                item["verify_status"] = "unverified"
                item["verify_badge"] = ""
                caution_count += 1

    print(f"    \u2705 \u5df2\u6838\u5b9e: {verified_count}\u6761  |  \u26a0 \u5355\u6e90: {caution_count}\u6761")
    return items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  存疑标注引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def mark_suspicious(items):
    """对可疑信息标注存疑"""
    # 敏感/易误导关键词
    sensational_kw = [
        "\u7206\u70b8", "\u7ffb\u8f66", "\u5d29\u4e86", "\u5bc6\u63ed", "\u5185\u5e55",
        "\u63ed\u79d8", "\u6df1\u626e", "\u8d77\u5e95", "\u771f\u76f8", "\u9707\u60ca",
        "\u79bb\u8c31", "\u6253\u8138", "\u77e5\u60c5\u4eba\u900f\u9732", "\u6d88\u606f\u4eba\u58eb",
        "shocking", "unconfirmed", "allegedly", "rumor", "unverified",
        "sources say", "reportedly", "claimed", "breaking",
    ]

    caution_count = 0
    for item in items:
        flags = []
        title_lower = item["title"].lower()

        # 1. 单源 + 低可信度信源
        if item.get("source_count", 1) == 1 and item.get("tier", 2) >= 3:
            flags.append("\u5355\u6e90\u672a\u7ecf\u6838\u5b9e")

        # 2. 标题含敏感/煽动性词汇
        hit_kw = [kw for kw in sensational_kw if kw in title_lower]
        if hit_kw and item.get("tier", 2) >= 2:
            flags.append(f"\u542b\u654f\u611f\u8bcd: {', '.join(hit_kw[:2])}")

        # 3. 标题极度夸张（过多感叹号、大写英文）
        if title_lower.count('!') >= 2 or (title_lower.isupper() and len(title_lower) > 20):
            flags.append("\u6807\u9898\u5938\u5f20")

        # 4. 摘要极短或缺失（可能断章取义）
        if not item.get("summary") or len(item.get("summary", "")) < 10:
            if item.get("score", 0) < 65:
                flags.append("\u4fe1\u606f\u4e0d\u5b8c\u6574")

        if flags:
            item["suspicious"] = True
            item["suspicion_reasons"] = flags
            caution_count += 1
        else:
            item["suspicious"] = False

    if caution_count > 0:
        print(f"  \u26a0 \u5b58\u7591\u6807\u6ce8: {caution_count}\u6761")
    return items


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  评分 & 排序
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def score_item(item, config):
    scoring = config.get("scoring", {})
    score = 50

    tier = item.get("tier", 2)
    if tier == 1:
        score += scoring.get("tier1_source_bonus", 15)
    elif tier == 2:
        score += scoring.get("tier2_source_bonus", 8)

    cat, cat_score, kw_hits = classify_news(item["title"], item.get("summary", ""), config)
    score += min(cat_score * scoring.get("keyword_match_base", 10), 30)
    item["category"] = cat

    if is_china_related(item["title"], item.get("summary", "")):
        score += scoring.get("china_related_bonus", 10)

    if is_breaking(item["title"]):
        score += scoring.get("breaking_news_bonus", 20)

    # 多源交叉验证加分
    source_count = item.get("source_count", 1)
    if source_count >= 3:
        score += 15
    elif source_count >= 2:
        score += 8

    # 存疑降权
    if item.get("suspicious"):
        score -= 10

    title_len = len(item["title"])
    if title_len < 10:
        score -= 15
    elif title_len > 100:
        score -= 5

    if item.get("summary") and len(item["summary"]) > 20:
        score += 5

    item["score"] = score
    return item


def dedup(items, threshold=0.6):
    if not items:
        return []
    items.sort(key=lambda x: x.get("score", 0), reverse=True)
    kept = []
    seen_fps = set()
    for item in items:
        fp = news_fingerprint(item["title"], item.get("source", ""))
        if fp in seen_fps:
            continue
        is_dup = False
        for kept_item in kept:
            if title_similarity(item["title"], kept_item["title"]) > threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(item)
            seen_fps.add(fp)
    return kept


def filter_cache(items, cache):
    return [item for item in items
            if news_fingerprint(item["title"], item.get("source", "")) not in cache]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  格式化输出
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_display_title(item):
    """获取显示用标题：有翻译用翻译，否则用原标题"""
    zh = item.get("title_zh", "")
    if zh and has_chinese(zh):
        return zh
    return item["title"]


def format_briefing(items, config):
    now = NOW.strftime("%Y-%m-%d %H:%M")
    categories = config.get("categories", {})
    max_items = config.get("scoring", {}).get("max_items", 25)

    grouped = {}
    for item in items[:max_items]:
        cat = item.get("category", "other")
        if cat not in grouped:
            grouped[cat] = []
        grouped[cat].append(item)

    cat_order = ["politics", "military", "economy", "tech", "society", "other"]
    sorted_cats = sorted(grouped.keys(),
                         key=lambda c: cat_order.index(c) if c in cat_order else 99)

    # 统计
    total = len(items[:max_items])
    verified_n = sum(1 for i in items[:max_items] if i.get("verify_status", "").startswith("verified"))
    suspicious_n = sum(1 for i in items[:max_items] if i.get("suspicious"))
    translated_n = sum(1 for i in items[:max_items] if i.get("title_zh"))

    lines = [
        "\U0001f4f0 \u5168\u7403\u65b0\u95fb\u7b80\u62a5",
        f"\u23f0 {now}",
        f"\u2705 \u5df2\u6838\u5b9e {verified_n}\u6761  |  \U0001f4e4 \u5df2\u7ffb\u8bd1 {translated_n}\u6761  |  \u26a0 \u5b58\u7591 {suspicious_n}\u6761",
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500",
    ]

    total_shown = 0
    for cat in sorted_cats:
        cat_items = grouped[cat]
        cat_info = categories.get(cat, {"emoji": "\U0001f4cb", "name": "\u5176\u4ed6"})
        emoji = cat_info.get("emoji", "\U0001f4cb")
        name = cat_info.get("name", cat)

        lines.append(f"\n{emoji} {name}\uff08{len(cat_items)}\u6761\uff09")

        for i, item in enumerate(cat_items[:8], 1):
            display_title = get_display_title(item)
            source = item.get("source", "")
            score = item.get("score", 0)
            lang_flag = "\U0001F1E8\U0001F1F3" if item.get("lang") == "zh" else "\U0001F310"

            # 热度标识
            if score >= 80:
                heat = "\U0001f525\U0001f525\U0001f525"
            elif score >= 65:
                heat = "\U0001f525\U0001f525"
            elif score >= 55:
                heat = "\U0001f525"
            else:
                heat = "\U0001f4cc"

            # 验证标识
            verify = item.get("verify_badge", "")
            # 存疑标识
            suspicion = ""
            if item.get("suspicious"):
                reasons = ", ".join(item.get("suspicion_reasons", []))
                suspicion = f"  \u26a0\ufe0f \u5b58\u7591\uff1a{reasons}"

            lines.append(f"\n  {heat} {display_title}")

            # 来源行
            src_parts = [f"{lang_flag} {source}"]
            if item.get("title_zh") and item.get("lang") == "en":
                src_parts.append(f"\u539f\u6587: {item['title'][:40]}...")
            src_parts.append(f"{score}\u5206")
            lines.append(f"     {' | '.join(src_parts)}")

            # 验证 + 存疑行
            if verify:
                lines.append(f"     {verify}")
            if suspicion:
                lines.append(f"     {suspicion}")

            # 多源来源
            if item.get("source_count", 1) >= 2:
                other_sources = [s for s in item.get("cross_sources", []) if s != source]
                if other_sources:
                    lines.append(f"     \U0001f4e1 \u5176\u4ed6\u6e90: {', '.join(other_sources[:4])}")

            total_shown += 1

    lines.append("\n\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    lines.append(f"\U0001f4ca \u62e5\u53d6 {len(items)} \u6761  |  \u7cbe\u9009 {total_shown} \u6761")

    # 信源分布
    source_counts = {}
    for item in items:
        src = item.get("source", "\u672a\u77e5")
        source_counts[src] = source_counts.get(src, 0) + 1
    top_sources = sorted(source_counts.items(), key=lambda x: x[1], reverse=True)[:8]
    source_line = "  ".join(f"{s}({c})" for s, c in top_sources)
    lines.append(f"\U0001f4e1 {source_line}")

    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  发送
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def send_feishu(message):
    cfg = load_config()["delivery"]
    cmd = [
        "qwenpaw", "channels", "send",
        "--agent-id", cfg["agent_id"],
        "--channel", cfg["channel"],
        "--target-user", cfg["target_user"],
        "--target-session", cfg["target_session"],
        "--text", message,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            print("[OK] \u5df2\u63a8\u9001\u5230\u98de\u4e66")
            return True
        else:
            print(f"[ERROR] {result.stderr}")
            return False
    except Exception as e:
        print(f"[ERROR] {e}")
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  主流程
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_briefing():
    config = load_config()
    cache = load_cache()

    print(f"[{NOW.strftime('%H:%M:%S')}] \U0001f4f0 全球新闻简报 v2.1 启动")

    # 1. 并行抓取国际 RSS 源
    all_items = fetch_all_rss(config)

    # 2. 中国信源直连抓取（不依赖RSSHub）
    cn_items = fetch_all_cn()
    if cn_items:
        print(f"  🇨🇳 中国信源: {len(cn_items)}条")
    all_items.extend(cn_items)

    # 3. 热搜聚合
    trending = fetch_trending()
    if trending:
        print(f"  📈 热搜聚合: {len(trending)}条")
    all_items.extend(trending)

    print(f"  📦 原始总量: {len(all_items)}条")

    if not all_items:
        print("  ⚠ 没有抓取到新闻，跳过推送")
        return

    # 4. 去重（翻译前先粗去重）
    deduped = dedup(all_items, threshold=config["scoring"]["dedup_threshold"])
    print(f"  🔄 去重后: {len(deduped)}条")

    # 5. 交叉验证（去重后聚类）
    deduped = cross_verify(deduped)

    # 6. 翻译英文新闻
    deduped = translate_with_llm(deduped)

    # 7. 存疑标注
    deduped = mark_suspicious(deduped)

    # 8. 评分（含验证/存疑加减分）
    for item in deduped:
        score_item(item, config)

    # 9. 过滤已推送
    new_items = filter_cache(deduped, cache)
    print(f"  🆕 新增: {len(new_items)}条")

    # 10. 排序
    new_items.sort(key=lambda x: x.get("score", 0), reverse=True)

    if not new_items:
        print("  ⚠ 没有新新闻，跳过推送")
        return

    # 11. 格式化
    report = format_briefing(new_items, config)
    print(report)

    # 12. 推送
    send_feishu(report)

    # 13. 更新缓存
    for item in new_items[:config["scoring"]["max_items"]]:
        fp = news_fingerprint(item["title"], item.get("source", ""))
        cache[fp] = {"time": datetime.now().isoformat(), "title": item["title"]}
    save_cache(cache)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] \u2705 \u5b8c\u6210")


if __name__ == "__main__":
    import sys
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "dry":
        config = load_config()
        all_items = fetch_all_rss(config)
        trending = fetch_trending()
        all_items.extend(trending)
        deduped = dedup(all_items)
        deduped = cross_verify(deduped)
        deduped = translate_with_llm(deduped)
        deduped = mark_suspicious(deduped)
        for item in deduped:
            score_item(item, config)
        deduped.sort(key=lambda x: x.get("score", 0), reverse=True)
        report = format_briefing(deduped, config)
        print(report)
    else:
        run_briefing()
