# arXiv Daily Digest to GitHub Pages

这个仓库会每天自动执行：

1. 按你配置的主题从 arXiv 拉取最新论文
2. 生成中文摘要（优先使用 DeepSeek API，默认约 150 字；没有 Key 则使用规则摘要）
3. 更新 `docs/index.md` 并自动提交到仓库
4. 通过 GitHub Pages 展示为个人网页
5. 使用 `docs/data/summary_cache.json` 按 arXiv ID 缓存摘要，避免重复消耗 token
6. 页面支持主题筛选、关键词检索、原文链接与 PDF 在线预览

## 目录结构

- `config/topics.yaml`: 主题与检索表达式配置
- `scripts/fetch_arxiv.py`: 抓取与摘要脚本
- `docs/index.html`: 交互式网页（推荐访问）
- `docs/index.md`: 每日生成的 Markdown 备份
- `docs/data/summary_cache.json`: 摘要缓存（去重）
- `.github/workflows/daily-arxiv.yml`: 每日定时工作流

## 1) 配置主题

编辑 `config/topics.yaml`：

```yaml
topics:
  - name: Your Topic Name
    query: all:"your keyword"
```

`query` 使用 arXiv API 的 `search_query` 语法。

## 2) 配置 API Secret（可选）

如果你希望自动生成更高质量中文摘要，在 GitHub 仓库设置中添加：

- `DEEPSEEK_API_KEY`（推荐）
- `OPENAI_API_KEY`（可选，作为兼容）

不配置也可以运行，只是摘要会退化为抽取式摘要。

## 3) 开启 GitHub Pages

在仓库设置中打开 GitHub Pages：

- Source: `Deploy from a branch`
- Branch: `main`（或你的默认分支）
- Folder: `/docs`

然后访问：

- `https://<你的GitHub用户名>.github.io/<仓库名>/`

## 4) 本地运行

```bash
pip install -r requirements.txt
python scripts/fetch_arxiv.py --config config/topics.yaml --output docs/index.md --json-output docs/data/latest.json
```

## 5) 定时

默认每个工作日 UTC 02:30 执行（北京时间 10:30）。

可在 `.github/workflows/daily-arxiv.yml` 修改 `cron`。

## 6) 低成本建议

- `lookback_days: 1`（每天抓取通常足够）
- `max_display_per_topic` 按需下调（例如 5）
- 保留摘要缓存文件，可显著减少重复调用 LLM
