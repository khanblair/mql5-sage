#!/usr/bin/env python3
"""
scripts/crawl.py
────────────────
Crawls https://www.mql5.com/en/docs, extracts clean technical content
from each page, chunks it, and builds the knowledge base.

Design principles:
- Stateful: tracks which pages have been crawled (crawl_state.json)
- Resumable: each run picks up where it left off
- Polite: respects delays between requests
- Prioritised: crawls high-value sections first (trade, indicators, basis)
- Self-journaling: writes a journal entry describing what was learned
"""

import os
import sys
import json
import time
import hashlib
import re
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from groq import Groq
from notify import send_telegram_message

# ─── Paths ───────────────────────────────────────────────────────────────────
KNOWLEDGE_FILE  = Path("knowledge/knowledge_base.json")
JOURNAL_FILE    = Path("journal/JOURNAL.json")
JOURNAL_MD      = Path("JOURNAL.md")
CRAWL_STATE     = Path("crawl_state.json")
CONFIG_FILE     = Path("config/mql5_sections.json")
FAILURE_LOG     = Path("journal/failures.json")

# ─── Settings ────────────────────────────────────────────────────────────────
BASE_URL        = "https://www.mql5.com/en/docs"
DELAY           = 2.5        # seconds between requests — be polite
CHUNK_SIZE      = 1200
CHUNK_OVERLAP   = 150
MAX_PAGES       = int(os.environ.get("MAX_PAGES", "40"))
TARGET_SECTION  = os.environ.get("TARGET_SECTION", "").strip()

# ── Rate limit protection (free tier: 250 req/day, 30 req/min) ───────────────
# Crawl NEVER calls Groq per-page — only ONE call at the very end to write
# a single journal entry summarising the whole run. This keeps crawl runs
# to exactly 1 Groq request regardless of how many pages are crawled.
GROQ_MODEL          = "groq/compound"   # exact model string from your dashboard
GROQ_CALL_DELAY     = 3                 # seconds to wait before the one end-of-run call
_groq_calls_made    = 0                 # counter — never exceed 1 per crawl run

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; mql5-sage/1.0; +github.com educational)",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# ─── Helpers ─────────────────────────────────────────────────────────────────

def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return default
    return default


def save_json(path: Path, data):
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def extract_keywords(text: str) -> list[str]:
    stopwords = {
        'the','a','an','and','or','but','in','on','at','to','for','of','with',
        'by','from','is','was','are','were','be','been','have','has','had',
        'do','does','did','will','would','could','should','may','might','this',
        'that','these','those','it','its','not','as','if','so','can','also',
        'which','who','when','where','what','how','all','more','than','into',
        'about','after','before','each','used','using','function','returns',
        'return','value','type','parameter','example','following','specified',
    }
    words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]{3,}\b', text)
    freq: dict[str, int] = {}
    for w in words:
        wl = w.lower()
        if wl not in stopwords:
            freq[wl] = freq.get(wl, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda x: -x[1])[:25]]


def chunk_text(text: str, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            for sep in ['\n\n', '. ', '.\n', '\n']:
                idx = text.rfind(sep, start + size // 2, end)
                if idx != -1:
                    end = idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


# ─── MQL5 Page Scraper ───────────────────────────────────────────────────────

def fetch_page(url: str, session: requests.Session) -> BeautifulSoup | None:
    """Fetch a page and return BeautifulSoup, or None on failure."""
    try:
        resp = session.get(url, headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            return BeautifulSoup(resp.text, 'lxml')
        elif resp.status_code == 429:
            print(f"  ⚠ Rate limited on {url} — sleeping 30s")
            time.sleep(30)
            return None
        else:
            print(f"  ⚠ HTTP {resp.status_code} for {url}")
            return None
    except Exception as e:
        print(f"  ⚠ Fetch error {url}: {e}")
        log_failure(url, "FETCH_ERROR", str(e))
        return None


def log_failure(url: str, error_type: str, message: str, context: str = ""):
    """Log a parsing or fetch failure for later self-repair analysis."""
    failures = load_json(FAILURE_LOG, [])
    failures.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "error_type": error_type,
        "message": message,
        "context": context[:500]  # truncate to avoid huge logs
    })
    # Keep only last 50 failures
    save_json(FAILURE_LOG, failures[-50:])


def extract_clean_content(soup: BeautifulSoup, url: str) -> dict | None:
    """
    Extract clean, structured content from an MQL5 docs page.
    Returns dict with title, content, code_examples, section_path.
    """
    # Remove nav, header, footer, ads, scripts
    for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer',
                               'aside', 'iframe', '.navigation', '.breadcrumb']):
        tag.decompose()

    # MQL5 docs main content area
    content_area = (
        soup.find('div', class_='documentation') or
        soup.find('div', id='content') or
        soup.find('article') or
        soup.find('main') or
        soup.find('div', class_='topic-body') or
        soup.body
    )

    if not content_area:
        log_failure(url, "CONTENT_NOT_FOUND", "Could not find main content area.")
        return None

    # Extract title
    title_tag = soup.find('h1') or soup.find('title')
    title = title_tag.get_text(strip=True) if title_tag else url.split('/')[-1]

    # Extract code examples separately (they're gold for MQL5)
    code_examples = []
    for code_tag in content_area.find_all(['code', 'pre']):
        code_text = code_tag.get_text(strip=True)
        if len(code_text) > 20:
            code_examples.append(code_text)
        code_tag.replace_with(f"\n[CODE_EXAMPLE]\n{code_text}\n[/CODE_EXAMPLE]\n")

    # Get clean text
    text = content_area.get_text(separator='\n')

    # Clean up whitespace
    text = re.sub(r'\n{4,}', '\n\n', text)
    text = re.sub(r'[ \t]{3,}', ' ', text)
    text = text.strip()

    if len(text) < 100:
        return None

    # Extract section path from URL
    path_parts = urlparse(url).path.strip('/').split('/')
    section = path_parts[2] if len(path_parts) > 2 else path_parts[-1] if path_parts else "unknown"

    return {
        "title": title,
        "content": text,
        "code_examples": code_examples,
        "section": section,
        "url": url,
        "char_count": len(text),
    }


def get_subpage_links(soup: BeautifulSoup, section_url: str) -> list[str]:
    """
    Find all sub-page links within a docs section.
    MQL5 docs have a sidebar/TOC with links to individual function pages.
    """
    links = set()
    base = "https://www.mql5.com"

    # Look for navigation/sidebar links
    for nav in soup.find_all(['nav', 'ul', 'div'], class_=re.compile(r'nav|sidebar|toc|menu|tree', re.I)):
        for a in nav.find_all('a', href=True):
            href = a['href']
            full_url = urljoin(base, href)
            if '/en/docs/' in full_url and full_url != section_url:
                links.add(full_url.split('#')[0])  # Remove anchors

    # Also check all links in content area
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.startswith('/en/docs/') and len(href) > 9:
            full_url = urljoin(base, href).split('#')[0]
            # Only include links that are sub-pages of this section
            if section_url.rstrip('/') in full_url or full_url.startswith(section_url.rstrip('/')):
                links.add(full_url)

    return list(links)


def discover_sections(session: requests.Session, current_config: dict) -> list[dict]:
    """
    Scans the main MQL5 docs index to find new sections automatically.
    """
    print("  🔍 Scanning for new MQL5 sections...")
    soup = fetch_page(BASE_URL, session)
    if not soup:
        return current_config.get("sections", [])

    existing_slugs = {s["slug"] for s in current_config.get("sections", [])}
    found_sections = []
    
    # MQL5 index usually has a clear list of sections
    doc_div = soup.find('div', class_='documentation')
    if not doc_div:
        return current_config.get("sections", [])

    for a in doc_div.find_all('a', href=True):
        href = a['href'].rstrip('/')
        if href.startswith('/en/docs/') and len(href.split('/')) == 4:
            slug = href.split('/')[-1]
            if slug not in existing_slugs:
                title = a.get_text(strip=True)
                if title and slug:
                    found_sections.append({
                        "slug": slug,
                        "title": title,
                        "priority": 3, # default priority for new sections
                        "discovered_at": datetime.now(timezone.utc).isoformat()
                    })
                    print(f"    ✨ Discovered new section: {title} ({slug})")
                    existing_slugs.add(slug)

    if found_sections:
        all_sections = current_config.get("sections", []) + found_sections
        current_config["sections"] = all_sections
        save_json(CONFIG_FILE, current_config)
        print(f"    ✓ Added {len(found_sections)} new sections to {CONFIG_FILE}")
    
    return current_config.get("sections", [])


# ─── Groq Analysis ───────────────────────────────────────────────────────────

def groq_single_call(client: Groq, sections_crawled: list[tuple], total_pages: int, total_chunks: int) -> str:
    """
    ONE Groq call per crawl run — summarises everything crawled this run.
    Free tier: 250 req/day. Crawl uses exactly 1. Ask uses 1. Evolve uses 1.
    That's 3/day maximum — well within the 250 limit.
    """
    section_summaries = []
    for section_title, pages in sections_crawled:
        titles = [p['title'] for p in pages[:6]]
        chunks = sum(p.get('chunk_count', 0) for p in pages)
        section_summaries.append(f"  - {section_title}: {len(pages)} pages, {chunks} chunks. Pages: {', '.join(titles[:4])}")

    prompt = f"""You are mql5-sage. You just finished a crawl session of the MQL5 documentation.

THIS RUN:
- Total pages crawled: {total_pages}
- Total chunks added: {total_chunks}
- Sections covered:
{chr(10).join(section_summaries)}

Write ONE first-person journal entry (4-6 sentences) summarising:
1. What MQL5 knowledge was added (mention actual function names or concepts if you know them)
2. What sections were covered and why they matter for trading algorithms
3. What you're looking forward to learning in future crawls

Be specific and technical. Mention real MQL5 concepts."""

    # Retry up to 3 times with backoff on rate limit
    for attempt in range(3):
        try:
            time.sleep(GROQ_CALL_DELAY)
            resp = client.chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You are mql5-sage, an MQL5 learning agent. Be concise and technical."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=350,
                temperature=0.4,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            err = str(e).lower()
            if "rate" in err or "429" in err:
                wait = 60 * (attempt + 1)
                print(f"  ⚠ Rate limit hit. Waiting {wait}s before retry {attempt+1}/3...")
                time.sleep(wait)
            else:
                print(f"  ⚠ Groq error: {e}")
                break

    # Fallback: write a plain journal entry without Groq
    section_names = [s for s, _ in sections_crawled]
    return (f"Crawl session complete. Added {total_pages} pages and {total_chunks} knowledge chunks "
            f"covering: {', '.join(section_names)}. Knowledge base growing steadily.")


# ─── Main Crawl Logic ─────────────────────────────────────────────────────────

def crawl_section(session: requests.Session, section: dict, crawl_state: dict,
                   kb: dict, max_pages: int) -> list[dict]:
    """
    Crawl a single MQL5 docs section. Returns list of page metadata.
    """
    slug = section["slug"]
    title = section["title"]
    section_url = f"{BASE_URL}/{slug}"
    crawled_pages = []
    pages_this_run = 0

    print(f"\n  📖 Section: {title} ({section_url})")

    # Fetch the section index page
    soup = fetch_page(section_url, session)
    if not soup:
        return []
    time.sleep(DELAY)

    # Extract content from the index page itself
    content = extract_clean_content(soup, section_url)
    if content and sha(content['content']) not in crawl_state.get("crawled_hashes", set()):
        chunks = chunk_text(content['content'])
        page_meta = add_page_to_kb(kb, content, chunks, slug, title)
        crawled_pages.append(page_meta)
        crawl_state.setdefault("crawled_urls", []).append(section_url)
        crawl_state.setdefault("crawled_hashes", set()).add(sha(content['content']))
        pages_this_run += 1
        print(f"    ✓ Index page: {content['title'][:60]} ({len(chunks)} chunks)")

    if pages_this_run >= max_pages:
        return crawled_pages

    # Get sub-page links
    sublinks = get_subpage_links(soup, section_url)
    print(f"    → Found {len(sublinks)} sub-pages")

    # Filter already crawled
    already_crawled = set(crawl_state.get("crawled_urls", []))
    new_links = [l for l in sublinks if l not in already_crawled]
    print(f"    → {len(new_links)} new pages to crawl")

    for url in new_links:
        if pages_this_run >= max_pages:
            print(f"    ⏸ Reached max_pages limit ({max_pages}). Will resume next run.")
            break

        time.sleep(DELAY)
        sub_soup = fetch_page(url, session)
        if not sub_soup:
            continue

        sub_content = extract_clean_content(sub_soup, url)
        if not sub_content:
            continue

        content_hash = sha(sub_content['content'])
        if content_hash in crawl_state.get("crawled_hashes", set()):
            print(f"    ↩ Already seen: {sub_content['title'][:50]}")
            continue

        chunks = chunk_text(sub_content['content'])
        if not chunks:
            continue

        page_meta = add_page_to_kb(kb, sub_content, chunks, slug, title)
        crawled_pages.append(page_meta)
        crawl_state.setdefault("crawled_urls", []).append(url)
        crawl_state.setdefault("crawled_hashes", set()).add(content_hash)
        pages_this_run += 1

        print(f"    ✓ [{pages_this_run}/{max_pages}] {sub_content['title'][:55]} ({len(chunks)} chunks)")

    return crawled_pages


def add_page_to_kb(kb: dict, content: dict, chunks: list[str], section_slug: str, section_title: str) -> dict:
    """Add a crawled page and its chunks to the knowledge base."""
    page_id = sha(content['url'])

    for i, chunk in enumerate(chunks):
        kb["chunks"].append({
            "id": f"{page_id}-{i}",
            "doc_id": page_id,
            "chunk_index": i,
            "text": chunk,
            "keywords": extract_keywords(chunk),
            "source_url": content['url'],
            "section": section_slug,
            "section_title": section_title,
            "page_title": content['title'],
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    kb["pages"][page_id] = {
        "id": page_id,
        "title": content['title'],
        "url": content['url'],
        "section": section_slug,
        "section_title": section_title,
        "chunk_count": len(chunks),
        "char_count": content['char_count'],
        "code_example_count": len(content.get('code_examples', [])),
        "crawled_at": datetime.now(timezone.utc).isoformat(),
    }

    return {"title": content['title'], "url": content['url'], "chunk_count": len(chunks)}


# ─── Journal & README helpers ─────────────────────────────────────────────────

def write_journal_md(journal: list):
    emoji_map = {"LEARNING": "📚", "QUERY": "🔍", "EVOLUTION": "🧬",
                 "CRAWL": "🕷️", "SESSION": "💬", "MILESTONE": "🏆", "FAILURE": "⚠️"}
    lines = [
        "# 📓 mql5-sage Journal\n\n",
        "> Append-only record of every crawl, query, and evolution session.\n\n---\n\n",
    ]
    for entry in reversed(journal):
        ts = entry.get("timestamp", "")[:16].replace("T", " ")
        etype = entry.get("entry_type", "")
        emoji = emoji_map.get(etype, "•")
        lines.append(f"## {emoji} {etype} — {ts} UTC\n\n")
        lines.append(f"**{entry.get('title', '')}**\n\n")
        lines.append(f"{entry.get('body', '')}\n\n")
        tags = entry.get("tags", [])
        if tags:
            lines.append(f"*Tags: {' '.join(f'`{t}`' for t in tags)}*\n\n")
        lines.append("---\n\n")
    JOURNAL_MD.write_text("".join(lines))


def update_readme(kb: dict, crawl_state: dict, journal: list):
    page_count = len(kb.get("pages", {}))
    chunk_count = len(kb.get("chunks", []))
    queries = kb.get("total_queries", 0)
    sections_done = list(set(p.get("section", "") for p in kb.get("pages", {}).values()))
    last_crawl = crawl_state.get("last_crawl", "Never")

    Path("README.md").write_text(f"""# 🧠 mql5-sage

> A self-evolving MQL5 documentation agent — learns from the live mql5.com docs,
> answers your MQL5 questions, and evolves daily. Runs entirely on GitHub Actions.

## 📊 Knowledge Base

| Metric | Value |
|--------|-------|
| 📄 Pages Crawled | {page_count} |
| 🧩 Knowledge Chunks | {chunk_count} |
| 🔍 Queries Answered | {queries} |
| 📓 Journal Entries | {len(journal)} |
| 🕷️ Last Crawled | {last_crawl} |
| 🤖 Model | `groq/compound` |

## 📚 Sections Ingested ({len(sections_done)}/37)

{', '.join(f'`{s}`' for s in sorted(sections_done)) if sections_done else '*Crawl not started yet*'}

## 🚀 How to Use

### Ask a question
**Actions → 🔍 Ask MQL5 Question → Run workflow → type question → Run**

### Start crawling (first time)
**Actions → 🕷️ Crawl MQL5 Docs → Run workflow → Run**
*(Crawls ~40 pages per run. Re-run to continue. Full coverage in ~15 runs.)*

### Crawl one specific section
**Actions → 🕷️ Crawl MQL5 Docs → Run workflow → enter section slug (e.g. `trading`) → Run**

### Watch it evolve
Runs automatically every day at 9am UTC. Nothing needed from you.

## ⚙️ One-Time Setup

1. Fork this repo
2. **Settings → Secrets → Actions → New secret:**
   - `GROQ_API_KEY` → from [console.groq.com](https://console.groq.com) (free)
3. Enable workflow write permissions: **Settings → Actions → General → Read and write permissions**
4. Run the **🕷️ Crawl** workflow to start building knowledge

## 📓 Journal
[JOURNAL.md](./JOURNAL.md) — every crawl, query, and evolution session recorded.

---
*Powered by Groq compound · Source: mql5.com/en/docs*
""")


def update_status_json(kb: dict, journal: list):
    """Generate status.json for GitHub Pages dashboard."""
    Path("docs").mkdir(exist_ok=True)

    page_count = len(kb.get("pages", {}))
    chunk_count = len(kb.get("chunks", []))
    queries = kb.get("total_queries", 0)

    # Calculate actual coverage
    pages_by_section: dict[str, int] = {}
    for p in kb.get("pages", {}).values():
        sec = p.get("section", "")
        pages_by_section[sec] = pages_by_section.get(sec, 0) + 1

    config = load_json(CONFIG_FILE, {"sections": []})
    all_slugs = {s["slug"] for s in config.get("sections", [])}
    max_pages_per_section = 60

    section_progress = []
    for slug in all_slugs:
        pages = pages_by_section.get(slug, 0)
        progress = min(pages / max_pages_per_section * 100, 100)
        section_progress.append(progress)

    coverage_pct = round(sum(section_progress) / len(section_progress)) if section_progress else 0

    status = {
        "page_count": page_count,
        "chunk_count": chunk_count,
        "query_count": queries,
        "journal_count": len(journal),
        "coverage_pct": coverage_pct,
        "total_sections": len(all_slugs),
        "sections_with_pages": len([s for s, c in pages_by_section.items() if c > 0]),
        "sections": pages_by_section,
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "journal": journal[-20:],  # Last 20 entries for dashboard
    }

    # Write to docs/ for GitHub Pages
    Path("docs/status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False))


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("✗ GROQ_API_KEY not set.")
        sys.exit(1)

    # Groq client — will be called ONCE at the end of the entire run
    groq_client = Groq(api_key=api_key)

    # Load state
    Path("knowledge").mkdir(exist_ok=True)
    Path("journal").mkdir(exist_ok=True)

    kb = load_json(KNOWLEDGE_FILE, {
        "pages": {}, "chunks": [], "total_queries": 0,
        "source": "https://www.mql5.com/en/docs", "last_evolved": None
    })
    if "documents" in kb and "pages" not in kb:
        kb["pages"] = kb.pop("documents", {})

    journal      = load_json(JOURNAL_FILE, [])
    crawl_state  = load_json(CRAWL_STATE, {"crawled_urls": [], "crawled_hashes": [], "last_crawl": None})
    crawl_state["crawled_hashes"] = set(crawl_state.get("crawled_hashes", []))
    config       = load_json(CONFIG_FILE, {"sections": []})

    print(f"\n{'═'*55}")
    print(f"🕷️  mql5-sage Crawl Session")
    print(f"   {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'═'*55}")
    print(f"  Already crawled : {len(crawl_state['crawled_urls'])} pages")
    print(f"  Knowledge chunks: {len(kb['chunks'])}")
    print(f"  Max this run    : {MAX_PAGES} pages")
    print(f"  Groq calls      : 1 (end of run only — free tier safe)")

    session = requests.Session()

    # Autonomous discovery of new sections
    sections = discover_sections(session, config)

    if TARGET_SECTION:
        sections = [s for s in sections if s["slug"] == TARGET_SECTION]
        if not sections:
            print(f"✗ Section '{TARGET_SECTION}' not found in config.")
            sys.exit(1)
        print(f"  Target section  : {TARGET_SECTION}")
    else:
        already_done = set(p.get("section", "") for p in kb.get("pages", {}).values())
        sections.sort(key=lambda s: (s.get("priority", 5), s["slug"] in already_done))

    pages_crawled_total = 0
    sections_crawled = []   # (section_title, [page_metas])

    for section in sections:
        remaining = MAX_PAGES - pages_crawled_total
        if remaining <= 0:
            break
        pages = crawl_section(session, section, crawl_state, kb, max_pages=remaining)
        if pages:
            pages_crawled_total += len(pages)
            sections_crawled.append((section["title"], pages))
            print(f"  → {section['title']}: {len(pages)} new pages")

    if pages_crawled_total == 0:
        print("\n✓ No new pages found — knowledge base is up to date.")
        return

    total_chunks_added = sum(
        sum(p.get('chunk_count', 0) for p in pages)
        for _, pages in sections_crawled
    )

    print(f"\n{'─'*55}")
    print(f"✓ Crawled {pages_crawled_total} new pages  |  {total_chunks_added} chunks added")
    print(f"✓ Total KB chunks: {len(kb['chunks'])}")

    # ── ONE Groq call for the entire run ──────────────────────────────────────
    print(f"\n→ Writing journal entry (1 Groq call for the whole run)...")
    journal_body = groq_single_call(groq_client, sections_crawled, pages_crawled_total, total_chunks_added)

    section_tags = [s.lower().replace(" ", "-") for s, _ in sections_crawled]
    journal.append({
        "id": sha(f"crawl{datetime.now().isoformat()}"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "entry_type": "CRAWL",
        "title": f"Crawled {pages_crawled_total} pages across {len(sections_crawled)} sections",
        "body": journal_body,
        "tags": section_tags[:6] + ["crawl", "mql5"],
    })

    # Update crawl state
    crawl_state["last_crawl"] = datetime.now(timezone.utc).isoformat()
    crawl_state["crawled_hashes"] = list(crawl_state["crawled_hashes"])

    # Save everything
    save_json(KNOWLEDGE_FILE, kb)
    save_json(JOURNAL_FILE, journal)
    save_json(CRAWL_STATE, crawl_state)
    write_journal_md(journal)
    update_readme(kb, crawl_state, journal)
    update_status_json(kb, journal)

    print(f"✓ Knowledge base saved")
    print(f"✓ Journal updated  →  JOURNAL.md")
    print(f"✓ README updated with current stats")
    print(f"✓ GitHub Pages status.json updated")
    print(f"\n  💳 Groq requests used this run: 1 / 250 daily limit")

    # Send Telegram Notification
    repo_full = os.getenv('GITHUB_REPOSITORY', 'user/repo')
    owner, name = repo_full.split('/') if '/' in repo_full else (repo_full, 'repo')
    summary_msg = (
        f"🕷️ *MQL5 Crawl Session*\n"
        f"✅ Crawled: {pages_crawled_total} pages\n"
        f"📊 Total Chunks: {len(kb['chunks'])}\n"
        f"🔗 [View Journal](https://{owner}.github.io/{name}/)"
    )
    send_telegram_message(summary_msg)


if __name__ == "__main__":
    main()
