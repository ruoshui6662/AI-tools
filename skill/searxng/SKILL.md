---
name: searxng
description: Privacy-respecting metasearch using your local SearXNG instance. Search the web, images, news, and more without external API dependencies.
author: Avinash Venkatswamy
version: 1.0.1
homepage: https://searxng.org
triggers:
- "search for"
- "search web"
- "find information"
- "look up"
metadata: {"source":"https://clawhub.ai/abk234/searxng","license":"MIT-0"}
runtime:
  bins:
  - python3
  deps:
  - httpx
  - rich
---

# SearXNG Search

Search the web using your local SearXNG instance - a privacy-respecting metasearch engine.

## Commands

### Web Search
```bash
uv run {baseDir}/scripts/searxng.py search "query"
# Top 10 results

uv run {baseDir}/scripts/searxng.py search "query" -n 20
# Top 20 results

uv run {baseDir}/scripts/searxng.py search "query" --format json
# JSON output
```

### Category Search
```bash
uv run {baseDir}/scripts/searxng.py search "query" --category images
uv run {baseDir}/scripts/searxng.py search "query" --category news
uv run {baseDir}/scripts/searxng.py search "query" --category videos
```

### Advanced Options
```bash
uv run {baseDir}/scripts/searxng.py search "query" --language en
uv run {baseDir}/scripts/searxng.py search "query" --time-range day
```

## Configuration

**Required:** Set the `SEARXNG_URL` environment variable to your SearXNG instance URL.

**Default Configuration (已配置):**
- URL: `http://192.168.68.112:8889`
- 配置文件: `skills/searxng/.env`

**使用方式:**
```bash
# 使用默认配置（无需指定 URL）
uv run {baseDir}/scripts/searxng.py search "query"

# 或者使用环境变量
export SEARXNG_URL="http://192.168.68.112:8889"
uv run {baseDir}/scripts/searxng.py search "query"

# 或者直接指定 URL
uv run {baseDir}/scripts/searxng.py search "query" --url http://192.168.68.112:8889
```

## Features

- **Privacy-focused**: No external API calls, uses your own SearXNG instance
- **Multiple categories**: Web, images, news, videos, files, IT, science, social media
- **Language support**: Search in specific languages
- **Time filtering**: Filter by day, week, month, year
- **JSON output**: Machine-readable output format
- **Rich display**: Beautiful terminal output with tables and panels