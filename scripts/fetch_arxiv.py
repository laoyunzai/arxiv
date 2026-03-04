#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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


def fallback_summary(title: str, abstract: str) -> str:
    clean = normalize_text(abstract)
    if not clean:
        return "摘要缺失。"

    sentences = re.split(r"(?<=[。！？.!?])\s+", clean)
    summary = " ".join(sentences[:2]).strip()
    if not summary:
        summary = clean[:220]

    if len(summary) > 240:
        summary = summary[:237].rstrip() + "..."

    return summary


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
) -> str:
    if client is None:
        return fallback_summary(title, abstract)

    prompt = (
        "请用中文总结这篇 arXiv 论文，输出 1-2 句，重点说明方法和贡献，"
        "不要使用序号，不要超过 90 个汉字。\n\n"
        f"标题: {normalize_text(title)}\n"
        f"摘要: {normalize_text(abstract)}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            temperature=0.2,
            max_tokens=220,
            messages=[
                {
                    "role": "system",
                    "content": "你是科研助理，擅长快速提炼论文核心贡献并用中文简洁表达。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        text = ""
        if response.choices and response.choices[0].message:
            text = normalize_text(response.choices[0].message.content or "")
        return text if text else fallback_summary(title, abstract)
    except Exception:
        return fallback_summary(title, abstract)


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


def get_cached_summary(cache: dict[str, Any], paper_id: str) -> str | None:
    item = cache.get("items", {}).get(paper_id, {})
    summary = item.get("summary", "")
    if isinstance(summary, str) and summary.strip():
        return summary.strip()
    return None


def set_cached_summary(cache: dict[str, Any], paper_id: str, summary: str, now_utc: datetime) -> None:
    cache.setdefault("items", {})
    cache["items"][paper_id] = {
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


def fetch_topic_entries(topic: TopicConfig, max_results: int) -> list[dict[str, Any]]:
    params = {
        "search_query": topic.query,
        "start": 0,
        "max_results": max_results,
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

    papers: list[dict[str, Any]] = []
    for entry in feed.entries:
        links = [l.get("href", "") for l in entry.get("links", []) if l.get("href")]
        html_link = entry.get("link", "")
        pdf_link = ""
        for link in links:
            if link.endswith(".pdf") or "/pdf/" in link:
                pdf_link = link
                break

        papers.append(
            {
                "id": extract_arxiv_id(entry.get("id", "")),
                "title": normalize_text(entry.get("title", "")),
                "authors": [normalize_text(a.get("name", "")) for a in entry.get("authors", [])],
                "abstract": normalize_text(entry.get("summary", "")),
                "published": entry.get("published", ""),
                "updated": entry.get("updated", ""),
                "html_url": html_link,
                "pdf_url": pdf_link,
            }
        )

    return papers


def within_lookback(published: str, now_utc: datetime, lookback_days: int) -> bool:
    if not published:
        return False
    try:
        pub_dt = date_parser.parse(published).astimezone(timezone.utc)
    except Exception:
        return False
    cutoff = now_utc - timedelta(days=lookback_days)
    return pub_dt >= cutoff


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
    max_results = int(config.get("max_results_per_topic", 20))
    max_display = args.max_display or int(config.get("max_display_per_topic", 10))
    cache_retention_days = int(config.get("summary_cache_retention_days", 180))
    cache_max_entries = int(config.get("summary_cache_max_entries", 8000))

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
        entries = fetch_topic_entries(topic, max_results=max_results)

        filtered = [
            e for e in entries if within_lookback(e.get("published", ""), now_utc, lookback_days)
        ]

        filtered = filtered[:max_display]
        for paper in filtered:
            paper_id = paper.get("id", "")
            cached = get_cached_summary(summary_cache, paper_id)
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
            )
            paper["summary"] = summary
            if paper_id:
                set_cached_summary(summary_cache, paper_id, summary, now_utc)

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
    parser.add_argument("--max-display", type=int, default=None, help="Override max papers per topic")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
