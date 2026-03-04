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


def maybe_build_openai_client(config: dict[str, Any]) -> tuple[OpenAI | None, str]:
    openai_cfg = config.get("openai", {})
    if not openai_cfg.get("enabled", True):
        return None, ""

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None, ""

    model = openai_cfg.get("model", "gpt-4.1-mini")
    return OpenAI(api_key=api_key), model


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
        response = client.responses.create(
            model=model,
            temperature=0.2,
            max_output_tokens=220,
            input=[
                {
                    "role": "system",
                    "content": "你是科研助理，擅长快速提炼机器学习论文核心贡献。",
                },
                {"role": "user", "content": prompt},
            ],
        )
        text = normalize_text(getattr(response, "output_text", ""))
        return text if text else fallback_summary(title, abstract)
    except Exception:
        return fallback_summary(title, abstract)


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
                "id": entry.get("id", ""),
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

    config = load_config(config_path)
    lookback_days = args.lookback_days or int(config.get("lookback_days", 2))
    max_results = int(config.get("max_results_per_topic", 20))
    max_display = args.max_display or int(config.get("max_display_per_topic", 10))

    topics = parse_topics(config.get("topics", []))
    if not topics:
        raise ValueError("No topics configured in config file.")

    openai_client, openai_model = maybe_build_openai_client(config)

    now_utc = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "generated_at_utc": now_utc.isoformat(timespec="seconds"),
        "lookback_days": lookback_days,
        "topics": [],
    }

    for topic in topics:
        entries = fetch_topic_entries(topic, max_results=max_results)

        filtered = [
            e for e in entries if within_lookback(e.get("published", ""), now_utc, lookback_days)
        ]

        filtered = filtered[:max_display]
        for paper in filtered:
            paper["summary"] = llm_summary(
                client=openai_client,
                model=openai_model,
                title=paper.get("title", ""),
                abstract=paper.get("abstract", ""),
            )

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
    json_output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Wrote markdown: {output_path}")
    print(f"Wrote JSON: {json_output_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fetch daily arXiv papers and generate digest pages.")
    parser.add_argument("--config", default="config/topics.yaml", help="Path to topics config YAML")
    parser.add_argument("--output", default="docs/index.md", help="Output markdown path")
    parser.add_argument("--json-output", default="docs/data/latest.json", help="Output JSON path")
    parser.add_argument("--lookback-days", type=int, default=None, help="Override lookback days")
    parser.add_argument("--max-display", type=int, default=None, help="Override max papers per topic")
    return parser


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
