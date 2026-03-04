const DATA_URL = "data/latest.json";

const state = {
  report: null,
  activeTopic: "all",
  keyword: "",
  flatPapers: [],
};

const dom = {
  metaStrip: document.getElementById("metaStrip"),
  topicTabs: document.getElementById("topicTabs"),
  cards: document.getElementById("cards"),
  emptyState: document.getElementById("emptyState"),
  keywordInput: document.getElementById("keywordInput"),
  pdfModal: document.getElementById("pdfModal"),
  pdfFrame: document.getElementById("pdfFrame"),
  pdfTitle: document.getElementById("pdfTitle"),
  pdfOpenAbs: document.getElementById("pdfOpenAbs"),
  pdfOpenPdf: document.getElementById("pdfOpenPdf"),
  closePdf: document.getElementById("closePdf"),
};

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function toDateLabel(input) {
  if (!input) return "-";
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) return String(input).slice(0, 10);
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function formatTimeWithZone(input) {
  if (!input) return "-";
  const date = new Date(input);
  if (Number.isNaN(date.getTime())) return input;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    timeZoneName: "short",
  }).format(date);
}

function parseDateTime(input) {
  const parsed = new Date(input || "");
  return Number.isNaN(parsed.getTime()) ? 0 : parsed.getTime();
}

function setMetaStrip(report) {
  const topicCount = report.topics.length;
  const paperCount = report.topics.reduce((count, topic) => count + topic.papers.length, 0);
  const cacheHits = report.cache?.hits ?? 0;
  const cacheMisses = report.cache?.misses ?? 0;
  dom.metaStrip.innerHTML = `
    <span class="pill">更新时间: ${escapeHtml(formatTimeWithZone(report.generated_at_utc))}</span>
    <span class="pill">主题数: ${topicCount}</span>
    <span class="pill">论文数: ${paperCount}</span>
    <span class="pill">回溯窗口: ${report.lookback_days} 天</span>
    <span class="pill">缓存命中: ${cacheHits} / ${cacheMisses}</span>
  `;
}

function flattenPapers(report) {
  const output = [];
  report.topics.forEach((topic) => {
    (topic.papers || []).forEach((paper) => {
      output.push({
        ...paper,
        topic_name: topic.name,
        topic_query: topic.query,
      });
    });
  });

  output.sort((a, b) => parseDateTime(b.published) - parseDateTime(a.published));
  return output;
}

function renderTopicTabs(report) {
  const topics = ["all", ...report.topics.map((topic) => topic.name)];
  dom.topicTabs.innerHTML = "";

  topics.forEach((name) => {
    const button = document.createElement("button");
    button.className = "topic-btn";
    button.textContent = name === "all" ? "全部" : name;
    button.dataset.topic = name;
    if (state.activeTopic === name) {
      button.classList.add("active");
    }

    button.addEventListener("click", () => {
      state.activeTopic = name;
      [...dom.topicTabs.querySelectorAll(".topic-btn")].forEach((btn) => {
        btn.classList.toggle("active", btn.dataset.topic === name);
      });
      renderCards();
    });

    dom.topicTabs.appendChild(button);
  });
}

function currentFilteredPapers() {
  const keyword = state.keyword.trim().toLowerCase();
  return state.flatPapers.filter((paper) => {
    if (state.activeTopic !== "all" && paper.topic_name !== state.activeTopic) {
      return false;
    }

    if (!keyword) {
      return true;
    }

    const haystack = [
      paper.title,
      paper.summary,
      paper.abstract,
      (paper.authors || []).join(" "),
      paper.id,
      paper.topic_name,
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(keyword);
  });
}

function openPdfModal(paper) {
  if (!paper.pdf_url) return;
  dom.pdfTitle.textContent = paper.title || "PDF 预览";
  dom.pdfFrame.src = paper.pdf_url;
  dom.pdfOpenAbs.href = paper.html_url || paper.pdf_url;
  dom.pdfOpenPdf.href = paper.pdf_url;
  dom.pdfModal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closePdfModal() {
  dom.pdfModal.classList.add("hidden");
  dom.pdfFrame.src = "about:blank";
  document.body.style.overflow = "";
}

function renderCards() {
  const papers = currentFilteredPapers();
  dom.cards.innerHTML = "";

  if (papers.length === 0) {
    dom.emptyState.classList.remove("hidden");
    return;
  }

  dom.emptyState.classList.add("hidden");

  papers.forEach((paper, index) => {
    const card = document.createElement("article");
    card.className = "card";
    card.style.animationDelay = `${Math.min(index * 30, 320)}ms`;

    const authors = (paper.authors || []).join(", ") || "-";
    const absUrl = paper.html_url || "";
    const pdfUrl = paper.pdf_url || "";

    const actions = [
      absUrl
        ? `<a class="link-btn abs" target="_blank" rel="noopener" href="${escapeHtml(absUrl)}">原文链接</a>`
        : "",
      pdfUrl
        ? `<a class="link-btn pdf" target="_blank" rel="noopener" href="${escapeHtml(pdfUrl)}">PDF 下载</a>`
        : "",
      pdfUrl
        ? `<button class="link-btn preview" data-preview="1" type="button">PDF 预览</button>`
        : "",
    ].join("");

    card.innerHTML = `
      <div class="card-head">
        <span class="topic-tag">${escapeHtml(paper.topic_name)}</span>
        <span class="date-tag">${escapeHtml(toDateLabel(paper.published))}</span>
      </div>
      <h3>${escapeHtml(paper.title || "Untitled")}</h3>
      <p class="authors">作者: ${escapeHtml(authors)}</p>
      <p class="summary">${escapeHtml(paper.summary || "暂无摘要")}</p>
      <div class="links">${actions}</div>
    `;

    const previewButton = card.querySelector("[data-preview='1']");
    if (previewButton) {
      previewButton.addEventListener("click", () => openPdfModal(paper));
    }

    dom.cards.appendChild(card);
  });
}

function bindEvents() {
  dom.keywordInput.addEventListener("input", (event) => {
    state.keyword = event.target.value || "";
    renderCards();
  });

  dom.closePdf.addEventListener("click", closePdfModal);
  dom.pdfModal.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.dataset.close === "1") {
      closePdfModal();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !dom.pdfModal.classList.contains("hidden")) {
      closePdfModal();
    }
  });
}

function renderError(message) {
  dom.metaStrip.innerHTML = `<span class="pill">加载失败</span>`;
  dom.cards.innerHTML = "";
  dom.emptyState.classList.remove("hidden");
  dom.emptyState.innerHTML = `
    <h2>数据读取失败</h2>
    <p>${escapeHtml(message)}</p>
    <p>请检查 <code>${escapeHtml(DATA_URL)}</code> 是否存在。</p>
  `;
}

async function bootstrap() {
  bindEvents();
  try {
    const response = await fetch(DATA_URL, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const report = await response.json();
    if (!report || !Array.isArray(report.topics)) {
      throw new Error("JSON 结构不合法");
    }

    state.report = report;
    state.flatPapers = flattenPapers(report);

    setMetaStrip(report);
    renderTopicTabs(report);
    renderCards();
  } catch (error) {
    renderError(error instanceof Error ? error.message : "未知错误");
  }
}

bootstrap();
