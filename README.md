# arXiv Daily Digest to GitHub Pages

这个仓库会每天自动执行：

1. 按你配置的主题从 arXiv 拉取最新论文
2. 生成中文摘要（有 `OPENAI_API_KEY` 时用 AI 摘要；没有则使用规则摘要）
3. 更新 `docs/index.md` 并自动提交到仓库
4. 通过 GitHub Pages 展示为个人网页

## 目录结构

- `config/topics.yaml`: 主题与检索表达式配置
- `scripts/fetch_arxiv.py`: 抓取与摘要脚本
- `docs/index.md`: 生成的网页内容
- `.github/workflows/daily-arxiv.yml`: 每日定时工作流

## 1) 配置主题

编辑 `config/topics.yaml`：

```yaml
topics:
  - name: Your Topic Name
    query: all:"your keyword"
```

`query` 使用 arXiv API 的 `search_query` 语法。

## 2) 配置 GitHub Actions Secret（可选）

如果你希望更好的中文摘要，在 GitHub 仓库设置中添加：

- `OPENAI_API_KEY`

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

默认每天 UTC 01:00 执行（北京时间 09:00）。

可在 `.github/workflows/daily-arxiv.yml` 修改 `cron`。
