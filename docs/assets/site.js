const DATA_URL = "data/latest.json";

const state = {
  report: null,
  activeTopic: "all",
  keyword: "",
  singleDate: "",
  dateFrom: "",
  dateTo: "",
  availableDateMin: "",
  availableDateMax: "",
  flatPapers: [],
};

const dom = {
  metaStrip: document.getElementById("metaStrip"),
  topicTabs: document.getElementById("topicTabs"),
  quickDateButtons: document.getElementById("quickDateButtons"),
  dateStatus: document.getElementById("dateStatus"),
  cards: document.getElementById("cards"),
  emptyState: document.getElementById("emptyState"),
  keywordInput: document.getElementById("keywordInput"),
  singleDateInput: document.getElementById("singleDateInput"),
  dateFromInput: document.getElementById("dateFromInput"),
  dateToInput: document.getElementById("dateToInput"),
  clearDateFilter: document.getElementById("clearDateFilter"),
  detailModal: document.getElementById("detailModal"),
  closeDetail: document.getElementById("closeDetail"),
  detailTopic: document.getElementById("detailTopic"),
  detailTitle: document.getElementById("detailTitle"),
  detailDate: document.getElementById("detailDate"),
  detailAuthors: document.getElementById("detailAuthors"),
  detailId: document.getElementById("detailId"),
  detailAbsLink: document.getElementById("detailAbsLink"),
  detailPdfLink: document.getElementById("detailPdfLink"),
  detailPreviewBtn: document.getElementById("detailPreviewBtn"),
  detailSummary: document.getElementById("detailSummary"),
  detailAbstract: document.getElementById("detailAbstract"),
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

function parseFlexibleDate(input) {
  if (!input) return null;
  if (input instanceof Date) {
    return Number.isNaN(input.getTime()) ? null : input;
  }

  const raw = String(input).trim();
  const ymdMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (ymdMatch) {
    return new Date(Number(ymdMatch[1]), Number(ymdMatch[2]) - 1, Number(ymdMatch[3]), 12);
  }

  const parsed = new Date(raw);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatDateValue(date) {
  return [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ].join("-");
}

function toLocalDateValue(input) {
  const parsed = parseFlexibleDate(input);
  if (parsed) {
    return formatDateValue(parsed);
  }
  const match = String(input || "").match(/\d{4}-\d{2}-\d{2}/);
  return match ? match[0] : "";
}

function shiftDateValue(ymd, days) {
  const parsed = parseFlexibleDate(ymd);
  if (!parsed) return "";
  parsed.setDate(parsed.getDate() + days);
  return formatDateValue(parsed);
}

function toDateLabel(input) {
  const date = parseFlexibleDate(input);
  if (!date) {
    return String(input || "-").slice(0, 10) || "-";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function toQuickDateLabel(ymd) {
  const date = parseFlexibleDate(ymd);
  if (!date) return ymd;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    weekday: "short",
  }).format(date);
}

function formatTimeWithZone(input) {
  const date = parseFlexibleDate(input);
  if (!date) return String(input || "-");
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
  const parsed = parseFlexibleDate(input);
  return parsed ? parsed.getTime() : 0;
}

function computeDateBounds(papers) {
  const dates = papers.map((paper) => toLocalDateValue(paper.published)).filter(Boolean).sort();
  return {
    min: dates[0] || "",
    max: dates[dates.length - 1] || "",
  };
}

function passesDateFilter(paper) {
  const paperYmd = toLocalDateValue(paper.published);
  if (!paperYmd) {
    return false;
  }

  if (state.singleDate) {
    return paperYmd === state.singleDate;
  }

  if (state.dateFrom && paperYmd < state.dateFrom) {
    return false;
  }

  if (state.dateTo && paperYmd > state.dateTo) {
    return false;
  }

  return true;
}

function updateDateInputLimits() {
  const min = state.availableDateMin || "";
  const max = state.availableDateMax || "";

  dom.singleDateInput.min = min;
  dom.singleDateInput.max = max;
  dom.dateFromInput.min = min;
  dom.dateToInput.max = max;
  dom.dateFromInput.max = state.dateTo || max;
  dom.dateToInput.min = state.dateFrom || min;
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

function buildQuickDateOptions() {
  const base = state.availableDateMax || toLocalDateValue(state.report?.generated_at_utc || new Date());
  if (!base) return [];
  return [0, 1, 2].map((offset) => shiftDateValue(base, -offset)).filter(Boolean);
}

function renderQuickDateButtons() {
  dom.quickDateButtons.innerHTML = "";

  buildQuickDateOptions().forEach((ymd, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "quick-date-btn";
    if (state.singleDate === ymd) {
      button.classList.add("active");
    }

    const caption = document.createElement("span");
    caption.className = "quick-date-caption";
    caption.textContent = index === 0 ? "最新" : `近 ${index + 1} 天`;

    const dateText = document.createElement("strong");
    dateText.textContent = toQuickDateLabel(ymd);

    button.append(caption, dateText);
    button.addEventListener("click", () => {
      if (state.singleDate === ymd) {
        clearDateFilters();
        return;
      }
      applySingleDateFilter(ymd);
    });

    dom.quickDateButtons.appendChild(button);
  });
}

function renderDateStatus() {
  if (state.singleDate) {
    dom.dateStatus.textContent = `当前：仅显示 ${toDateLabel(state.singleDate)} 的论文`;
    return;
  }

  if (state.dateFrom || state.dateTo) {
    if (state.dateFrom && state.dateTo) {
      dom.dateStatus.textContent = `当前：显示 ${toDateLabel(state.dateFrom)} 至 ${toDateLabel(state.dateTo)} 的论文`;
      return;
    }
    if (state.dateFrom) {
      dom.dateStatus.textContent = `当前：显示 ${toDateLabel(state.dateFrom)} 之后的论文`;
      return;
    }
    dom.dateStatus.textContent = `当前：显示 ${toDateLabel(state.dateTo)} 之前的论文`;
    return;
  }

  dom.dateStatus.textContent = "当前：显示全部日期的论文";
}

function syncDateControls() {
  dom.singleDateInput.value = state.singleDate;
  dom.dateFromInput.value = state.dateFrom;
  dom.dateToInput.value = state.dateTo;
  updateDateInputLimits();
  renderQuickDateButtons();
  renderDateStatus();
}

function applySingleDateFilter(ymd) {
  state.singleDate = ymd || "";
  state.dateFrom = "";
  state.dateTo = "";
  syncDateControls();
  renderCards();
}

function applyRangeFilter(from, to) {
  state.singleDate = "";
  state.dateFrom = from || "";
  state.dateTo = to || "";
  syncDateControls();
  renderCards();
}

function clearDateFilters() {
  state.singleDate = "";
  state.dateFrom = "";
  state.dateTo = "";
  syncDateControls();
  renderCards();
}

function currentFilteredPapers() {
  const keyword = state.keyword.trim().toLowerCase();
  return state.flatPapers.filter((paper) => {
    if (state.activeTopic !== "all" && paper.topic_name !== state.activeTopic) {
      return false;
    }

    if (!passesDateFilter(paper)) {
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
  if (dom.detailModal.classList.contains("hidden")) {
    document.body.style.overflow = "";
  }
}

function openDetailModal(paper) {
  dom.detailTopic.textContent = paper.topic_name || "-";
  dom.detailTitle.textContent = paper.title || "Untitled";
  dom.detailDate.textContent = `日期: ${toDateLabel(paper.published)}`;
  dom.detailAuthors.textContent = `作者: ${(paper.authors || []).join(", ") || "-"}`;
  dom.detailId.textContent = paper.id ? `arXiv: ${paper.id}` : "";
  dom.detailSummary.textContent = paper.summary || "暂无摘要";
  dom.detailAbstract.textContent = paper.abstract || "暂无原始摘要";

  const absUrl = paper.html_url || "";
  const pdfUrl = paper.pdf_url || "";
  dom.detailAbsLink.href = absUrl || "#";
  dom.detailAbsLink.style.display = absUrl ? "" : "none";
  dom.detailPdfLink.href = pdfUrl || "#";
  dom.detailPdfLink.style.display = pdfUrl ? "" : "none";
  dom.detailPreviewBtn.style.display = pdfUrl ? "" : "none";
  dom.detailPreviewBtn.onclick = () => openPdfModal(paper);

  dom.detailModal.classList.remove("hidden");
  document.body.style.overflow = "hidden";
}

function closeDetailModal() {
  dom.detailModal.classList.add("hidden");
  if (dom.pdfModal.classList.contains("hidden")) {
    document.body.style.overflow = "";
  }
}

function renderCards() {
  const papers = currentFilteredPapers();
  dom.cards.innerHTML = "";
  renderDateStatus();

  if (papers.length === 0) {
    dom.emptyState.classList.remove("hidden");
    return;
  }

  dom.emptyState.classList.add("hidden");

  papers.forEach((paper, index) => {
    const card = document.createElement("article");
    card.className = "card";
    card.style.animationDelay = `${Math.min(index * 30, 320)}ms`;
    card.setAttribute("tabindex", "0");
    card.setAttribute("role", "button");
    card.setAttribute("aria-label", `${paper.title || "论文"} 详情`);

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
      previewButton.addEventListener("click", (event) => {
        event.stopPropagation();
        openPdfModal(paper);
      });
    }

    card.querySelectorAll("a").forEach((anchor) => {
      anchor.addEventListener("click", (event) => {
        event.stopPropagation();
      });
    });

    card.addEventListener("click", () => openDetailModal(paper));
    card.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        openDetailModal(paper);
      }
    });

    dom.cards.appendChild(card);
  });
}

function bindEvents() {
  dom.keywordInput.addEventListener("input", (event) => {
    state.keyword = event.target.value || "";
    renderCards();
  });

  dom.singleDateInput.addEventListener("change", (event) => {
    const value = event.target.value || "";
    if (!value) {
      clearDateFilters();
      return;
    }
    applySingleDateFilter(value);
  });

  dom.dateFromInput.addEventListener("change", (event) => {
    let nextFrom = event.target.value || "";
    let nextTo = state.dateTo;
    if (nextTo && nextFrom && nextTo < nextFrom) {
      nextTo = nextFrom;
    }
    applyRangeFilter(nextFrom, nextTo);
  });

  dom.dateToInput.addEventListener("change", (event) => {
    let nextTo = event.target.value || "";
    let nextFrom = state.dateFrom;
    if (nextFrom && nextTo && nextFrom > nextTo) {
      nextFrom = nextTo;
    }
    applyRangeFilter(nextFrom, nextTo);
  });

  dom.clearDateFilter.addEventListener("click", () => {
    clearDateFilters();
  });

  dom.closePdf.addEventListener("click", closePdfModal);
  dom.pdfModal.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.dataset.close === "1") {
      closePdfModal();
    }
  });

  dom.closeDetail.addEventListener("click", closeDetailModal);
  dom.detailModal.addEventListener("click", (event) => {
    const target = event.target;
    if (target instanceof HTMLElement && target.dataset.closeDetail === "1") {
      closeDetailModal();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    if (!dom.pdfModal.classList.contains("hidden")) {
      closePdfModal();
      return;
    }
    if (!dom.detailModal.classList.contains("hidden")) {
      closeDetailModal();
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
    const bounds = computeDateBounds(state.flatPapers);
    state.availableDateMin = bounds.min;
    state.availableDateMax = bounds.max;

    setMetaStrip(report);
    renderTopicTabs(report);
    syncDateControls();
    renderCards();
  } catch (error) {
    renderError(error instanceof Error ? error.message : "未知错误");
  }
}

bootstrap();
