#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests
import yaml
from dateutil import parser as date_parser
from openai import OpenAI

ARXIV_API_URL = "https://export.arxiv.org/api/query"
DEFAULT_TIMEOUT = 30
DEFAULT_PAGE_SIZE = 200


@dataclass
class TopicConfig:
    name: str
    query: str


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def markdown_escape(text: str) -> str:
    return normalize_text(text).replace("|", "\\|")


def fallback_summary(title: str, abstract: str, target_chars: int = 150) -> str:
    clean = normalize_text(abstract)
    if not clean:
        return "摘要缺失。"

    sentences = re.split(r"(?<=[。！？.!?])\s+", clean)
    selected: list[str] = []
    total = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        selected.append(sentence)
        total += len(sentence)
        if total >= target_chars:
            break

    summary = " ".join(selected).strip()
    if not summary:
        summary = clean[: max(220, target_chars + 60)]

    max_len = max(220, target_chars + 60)
    if len(summary) > max_len:
        summary = summary[: max_len - 3].rstrip() + "..."

    return summary


def is_chinese_dominant(text: str, min_ratio: float = 0.5, min_chars: int = 20) -> bool:
    clean = normalize_text(text)
    if not clean:
        return False
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", clean))
    alpha_num_count = len(re.findall(r"[A-Za-z0-9]", clean))
    if chinese_count < min_chars:
        return False
    total = chinese_count + alpha_num_count
    if total == 0:
        return False
    return (chinese_count / total) >= min_ratio


def chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", normalize_text(text)))


def latin_char_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z]", normalize_text(text)))


def has_terminal_punctuation(text: str) -> bool:
    return normalize_text(text).endswith(("。", "！", "？", ".", "!", "?"))


def is_summary_acceptable(text: str, target_chars: int, require_chinese: bool) -> bool:
    clean = normalize_text(text)
    if not clean:
        return False
    if "自动中文总结失败" in clean:
        return False
    if clean.endswith(("...", "…", "，", ",", "：", ":", "；", ";", "（", "(", "【", "[")):
        return False
    if len(clean) < max(32, int(target_chars * 0.35)):
        return False
    if require_chinese:
        chinese_count = chinese_char_count(clean)
        latin_count = latin_char_count(clean)
        if chinese_count < max(20, int(target_chars * 0.18)):
            return False
        total = chinese_count + latin_count
        if total == 0 or (chinese_count / total) < 0.72:
            return False
        if not has_terminal_punctuation(clean):
            return False
    return True


def force_chinese_placeholder(title: str) -> str:
    clean_title = normalize_text(title)
    if not clean_title:
        clean_title = "该论文"
    return f"论文《{clean_title}》已完成抓取，但自动中文总结失败，请点击原文或 PDF 查看完整内容。"


def to_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = date_parser.parse(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_published_utc(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = date_parser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def extract_arxiv_id(raw_id: str) -> str:
    if not raw_id:
        return ""
    parsed = urlparse(raw_id)
    path = parsed.path.strip("/")
    if path.startswith("abs/"):
        return path.split("/", 1)[1]
    if path:
        return path
    return raw_id.strip()


def maybe_build_llm_client(config: dict[str, Any]) -> tuple[OpenAI | None, str]:
    llm_cfg = config.get("llm")
    if llm_cfg is None:
        llm_cfg = config.get("openai", {})

    if not llm_cfg.get("enabled", True):
        return None, ""

    provider = str(llm_cfg.get("provider", "deepseek")).lower()
    if provider == "openai":
        default_env = "OPENAI_API_KEY"
    else:
        default_env = "DEEPSEEK_API_KEY"

    api_key_env = str(llm_cfg.get("api_key_env", default_env))
    api_key = os.getenv(api_key_env, "").strip()
    if not api_key:
        return None, ""

    model = str(llm_cfg.get("model", "deepseek-chat"))
    base_url = str(llm_cfg.get("base_url", "https://api.deepseek.com/v1")).strip()
    if not base_url:
        base_url = None
    return OpenAI(api_key=api_key, base_url=base_url), model


def llm_summary(
    client: OpenAI | None,
    model: str,
    title: str,
    abstract: str,
    target_chars: int,
) -> str:
    if client is None:
        return fallback_summary(title, abstract, target_chars=target_chars)

    clean_title = normalize_text(title)
    clean_abstract = normalize_text(abstract)

    prompt = (
        "请基于给定标题和摘要，写一段完整的简体中文论文总结。\n"
        f"硬性要求：1）只输出简体中文；2）长度控制在 {target_chars} 字左右（允许 ±30 字）；"
        "3）必须在一段内交代研究问题、核心方法、主要结果和意义；"
        "4）不能使用列表、标题或英文整句；5）必须以句号结尾，不要输出残句或省略号。\n\n"
        f"标题: {clean_title}\n"
        f"摘要: {clean_abstract}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            max_tokens=360,
            messages=[
                {
                    "role": "system",
                    "content": "你是严谨的中文科研摘要助手，只输出一段完整、自然、准确的简体中文总结。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        text = ""
        if response.choices and response.choices[0].message:
            text = normalize_text(response.choices[0].message.content or "")
        if is_summary_acceptable(text, target_chars=target_chars, require_chinese=True):
            return text

        repair_prompt = (
            "上一版输出不合格，请重新生成并严格满足以下要求：\n"
            "1）只输出一段完整的简体中文；\n"
            f"2）长度约 {target_chars} 字（允许 ±30 字）；\n"
            "3）必须明确写出研究问题、方法、主要结果和意义；\n"
            "4）不要出现英文整句、列表、标题、残句或省略号；\n"
            "5）必须以句号结尾。\n\n"
            f"标题: {clean_title}\n"
            f"摘要: {clean_abstract}"
        )
        repair = client.chat.completions.create(
            model=model,
            temperature=0.1,
            max_tokens=420,
            messages=[
                {
                    "role": "system",
                    "content": "你只输出一段完整的简体中文论文总结，不输出任何解释或额外说明。",
                },
                {"role": "user", "content": repair_prompt},
            ],
        )
        repair_text = ""
        if repair.choices and repair.choices[0].message:
            repair_text = normalize_text(repair.choices[0].message.content or "")
        if is_summary_acceptable(repair_text, target_chars=target_chars, require_chinese=True):
            return repair_text
        return force_chinese_placeholder(title)
    except Exception:
        fallback = fallback_summary(title, abstract, target_chars=target_chars)
        if is_chinese_dominant(fallback):
            return fallback
        return force_chinese_placeholder(title)


def load_summary_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "items": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("items", {})
        if isinstance(items, dict):
            return {"version": 1, "items": items}
    except Exception:
        pass
    return {"version": 1, "items": {}}


def build_cache_key(paper_id: str, model: str, target_chars: int) -> str:
    normalized_id = normalize_text(paper_id)
    normalized_model = normalize_text(model) or "fallback"
    return f"{normalized_id}::{normalized_model}::{target_chars}"


def get_cached_summary(
    cache: dict[str, Any],
    cache_key: str,
    *,
    target_chars: int,
    require_chinese: bool,
) -> str | None:
    item = cache.get("items", {}).get(cache_key, {})
    summary = item.get("summary", "")
    if isinstance(summary, str):
        clean = normalize_text(summary)
        if not clean:
            return None
        if require_chinese and not is_summary_acceptable(
            clean, target_chars=target_chars, require_chinese=True
        ):
            return None
        return clean
    return None


def set_cached_summary(cache: dict[str, Any], cache_key: str, summary: str, now_utc: datetime) -> None:
    cache.setdefault("items", {})
    cache["items"][cache_key] = {
        "summary": normalize_text(summary),
        "updated_at_utc": to_iso_utc(now_utc),
    }


def prune_summary_cache(cache: dict[str, Any], now_utc: datetime, retention_days: int, max_entries: int) -> None:
    items = cache.get("items", {})
    if not isinstance(items, dict):
        cache["items"] = {}
        return

    cutoff = now_utc - timedelta(days=retention_days)
    keep: list[tuple[str, str, dict[str, Any]]] = []
    for paper_id, payload in items.items():
        updated_at = parse_iso_datetime(str(payload.get("updated_at_utc", "")))
        if updated_at is None:
            continue
        if updated_at >= cutoff:
            keep.append((paper_id, updated_at.isoformat(), payload))

    keep.sort(key=lambda x: x[1], reverse=True)
    trimmed = keep[:max_entries]
    cache["items"] = {paper_id: payload for paper_id, _, payload in trimmed}


def save_summary_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def lookback_cutoff(now_utc: datetime, lookback_days: int) -> datetime:
    base_day = (now_utc - timedelta(days=lookback_days)).date()
    return datetime.combine(base_day, time.min, tzinfo=timezone.utc)


def parse_entry(entry: dict[str, Any]) -> dict[str, Any]:
    links = [l.get("href", "") for l in entry.get("links", []) if l.get("href")]
    html_link = entry.get("link", "")
    pdf_link = ""
    for link in links:
        if link.endswith(".pdf") or "/pdf/" in link:
            pdf_link = link
            break

    return {
        "id": extract_arxiv_id(entry.get("id", "")),
        "title": normalize_text(entry.get("title", "")),
        "authors": [normalize_text(a.get("name", "")) for a in entry.get("authors", [])],
        "abstract": normalize_text(entry.get("summary", "")),
        "published": entry.get("published", ""),
        "updated": entry.get("updated", ""),
        "html_url": html_link,
        "pdf_url": pdf_link,
    }


def fetch_topic_entries(
    topic: TopicConfig,
    now_utc: datetime,
    lookback_days: int,
    page_size: int,
    max_results: int | None,
) -> list[dict[str, Any]]:
    cutoff = lookback_cutoff(now_utc, lookback_days)
    start = 0
    papers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    while True:
        if max_results is None:
            current_page_size = page_size
        else:
            remaining = max_results - len(papers)
            if remaining <= 0:
                break
            current_page_size = min(page_size, remaining)

        params = {
            "search_query": topic.query,
            "start": start,
            "max_results": current_page_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }

        response = requests.get(
            ARXIV_API_URL,
            params=params,
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": "arxiv-daily-digest/1.0"},
        )
        response.raise_for_status()
        feed = feedparser.parse(response.text)
        entries = list(feed.entries)
        if not entries:
            break

        reached_before_cutoff = False
        for entry in entries:
            parsed = parse_entry(entry)
            paper_id = parsed.get("id", "")
            if paper_id in seen_ids:
                continue
            seen_ids.add(paper_id)

            published_dt = parse_published_utc(parsed.get("published", ""))
            if published_dt is not None and published_dt < cutoff:
                reached_before_cutoff = True
                break

            papers.append(parsed)
            if max_results is not None and len(papers) >= max_results:
                break

        if reached_before_cutoff:
            break
        if max_results is not None and len(papers) >= max_results:
            break
        if len(entries) < current_page_size:
            break

        start += len(entries)

    return papers


def render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# arXiv Daily Digest")
    lines.append("")
    lines.append(f"生成时间（UTC）：{report['generated_at_utc']}")
    lines.append(f"回溯窗口：最近 {report['lookback_days']} 天")
    lines.append("")

    for topic in report["topics"]:
        lines.append(f"## {markdown_escape(topic['name'])}")
        lines.append("")
        lines.append(f"查询：`{topic['query']}`")
        lines.append("")

        papers = topic["papers"]
        if not papers:
            lines.append("今天没有检索到新论文。")
            lines.append("")
            continue

        lines.append("| 日期 | 标题 | 作者 | 摘要 | 链接 |")
        lines.append("|---|---|---|---|---|")
        for paper in papers:
            date_text = paper["published"][:10] if paper["published"] else "-"
            authors = ", ".join(paper["authors"][:4])
            if len(paper["authors"]) > 4:
                authors += " et al."
            links = []
            if paper["html_url"]:
                links.append(f"[abs]({paper['html_url']})")
            if paper["pdf_url"]:
                links.append(f"[pdf]({paper['pdf_url']})")
            link_text = " ".join(links) if links else "-"

            lines.append(
                "| "
                + " | ".join(
                    [
                        markdown_escape(date_text),
                        markdown_escape(paper["title"]),
                        markdown_escape(authors),
                        markdown_escape(paper["summary"]),
                        link_text,
                    ]
                )
                + " |"
            )

        lines.append("")

    lines.append("---")
    lines.append("本页面由 GitHub Actions 每日自动更新。")
    return "\n".join(lines) + "\n"


def parse_topics(raw_topics: list[dict[str, Any]]) -> list[TopicConfig]:
    topics: list[TopicConfig] = []
    for item in raw_topics:
        name = normalize_text(str(item.get("name", "")))
        query = normalize_text(str(item.get("query", "")))
        if name and query:
            topics.append(TopicConfig(name=name, query=query))
    return topics


def run(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    output_path = Path(args.output)
    json_output_path = Path(args.json_output)
    cache_path = Path(args.summary_cache)

    config = load_config(config_path)
    lookback_days = args.lookback_days or int(config.get("lookback_days", 2))
    fetch_page_size = int(config.get("fetch_page_size", DEFAULT_PAGE_SIZE))
    max_results_cfg = args.max_results if args.max_results is not None else int(
        config.get("max_results_per_topic", 0)
    )
    max_results = max_results_cfg if max_results_cfg > 0 else None
    max_display_cfg = args.max_display if args.max_display is not None else int(
        config.get("max_display_per_topic", 0)
    )
    max_display = max_display_cfg if max_display_cfg > 0 else None
    cache_retention_days = int(config.get("summary_cache_retention_days", 180))
    cache_max_entries = int(config.get("summary_cache_max_entries", 8000))
    llm_cfg = config.get("llm", {})
    summary_target_chars = int(llm_cfg.get("summary_target_chars", 150))

    topics = parse_topics(config.get("topics", []))
    if not topics:
        raise ValueError("No topics configured in config file.")

    llm_client, llm_model = maybe_build_llm_client(config)
    summary_cache = load_summary_cache(cache_path)
    cache_hits = 0
    cache_misses = 0

    now_utc = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "generated_at_utc": now_utc.isoformat(timespec="seconds"),
        "lookback_days": lookback_days,
        "cache": {"hits": 0, "misses": 0, "path": str(cache_path)},
        "topics": [],
    }

    for topic in topics:
        entries = fetch_topic_entries(
            topic=topic,
            now_utc=now_utc,
            lookback_days=lookback_days,
            page_size=fetch_page_size,
            max_results=max_results,
        )
        filtered = entries if max_display is None else entries[:max_display]
        for paper in filtered:
            paper_id = paper.get("id", "")
            cache_key = build_cache_key(paper_id=paper_id, model=llm_model, target_chars=summary_target_chars)
            cached = get_cached_summary(
                summary_cache,
                cache_key,
                target_chars=summary_target_chars,
                require_chinese=llm_client is not None,
            )
            if cached:
                cache_hits += 1
                paper["summary"] = cached
                continue

            cache_misses += 1
            summary = llm_summary(
                client=llm_client,
                model=llm_model,
                title=paper.get("title", ""),
                abstract=paper.get("abstract", ""),
                target_chars=summary_target_chars,
            )
            paper["summary"] = summary
            if paper_id and (
                llm_client is None
                or is_summary_acceptable(summary, target_chars=summary_target_chars, require_chinese=True)
            ):
                set_cached_summary(summary_cache, cache_key, summary, now_utc)

        report["topics"].append(
            {
                "name": topic.name,
                "query": topic.query,
                "papers": filtered,
            }
        )

    markdown = render_markdown(report)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    json_output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(markdown, encoding="utf-8")
    report["cache"]["hits"] = cache_hits
    report["cache"]["misses"] = cache_misses
    prune_summary_cache(
        cache=summary_cache,
        now_utc=now_utc,
        retention_days=cache_retention_days,
        max_entries=cache_max_entries,
    )
    save_summary_cache(cache_path, summary_cache)
    json_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote markdown: {output_path}")
    print(f"Wrote JSON: {json_output_path}")
    print(f"Cache hits: {cache_hits}, misses: {cache_misses}, cache file: {cache_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch daily arXiv papers and generate digest pages.")
    parser.add_argument("--config", default="config/topics.yaml", help="Path to topics config YAML")
    parser.add_argument("--output", default="docs/index.md", help="Output markdown path")
    parser.add_argument("--json-output", default="docs/data/latest.json", help="Output JSON path")
    parser.add_argument(
        "--summary-cache",
        default="docs/data/summary_cache.json",
        help="Summary cache JSON path for de-duplicating LLM calls",
    )
    parser.add_argument("--lookback-days", type=int, default=None, help="Override lookback days")
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Override max fetched papers per topic (<=0 means no limit)",
    )
    parser.add_argument(
        "--max-display",
        type=int,
        default=None,
        help="Override max displayed papers per topic (<=0 means no limit)",
    )
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
