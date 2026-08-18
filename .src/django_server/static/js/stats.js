(function () {
  "use strict";

  const PALETTE = [
    "#6366F1","#14B8A6","#F59E0B","#EF4444","#8B5CF6",
    "#06B6D4","#10B981","#F97316","#EC4899","#84CC16",
    "#3B82F6","#A855F7","#E11D48","#0EA5E9","#D97706",
  ];
  const DIFF_COLORS = { "입문":"#10B981","초급":"#3B82F6","중급":"#F59E0B","고급":"#EF4444" };
  const DIFF_ORDER  = ["입문","초급","중급","고급"];

  let mainChart = null;
  let diffChart = null;
  let pubChart  = null;
  let donutSideChart = null;

  let currentCat  = null;
  let currentType = "hbar";
  let stackData   = {};

  /* ── 커서 위치 툴팁 positioner ───────────────────────────── */
  Chart.Tooltip.positioners.cursor = function (_, eventPosition) {
    return { x: eventPosition.x, y: eventPosition.y };
  };

  const TOOLTIP_BASE = { position: "cursor" };

  /* ── 툴팁 라벨 콜백 공용 헬퍼 ─────────────────────────────── */
  // 막대 차트류: 값만 표시 (예: " 12권")
  function valueTooltip(accessor) {
    return { label: ctx => ` ${accessor(ctx).toLocaleString()}권` };
  }
  // 도넛 차트류: 라벨 + 값 표시 (예: " Python: 12권")
  function labeledTooltip() {
    return { label: ctx => ` ${ctx.label}: ${ctx.parsed.toLocaleString()}권` };
  }

  /* ── 탭 전환 ─────────────────────────────────────────────── */
  document.querySelectorAll(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    });
  });

  /* ── 초기화 ───────────────────────────────────────────────── */
  fetch(STATS_API_URL)
    .then(r => r.json())
    .then(data => {
      stackData = data.stack_data;
      fillSummary(data.summary);
      buildCatTabs(data.stack_data);
      renderSideDonut(data.cat_counts);
      renderDiff(data.diff_counts);
      renderPub(data.pub_top);
      buildHeatmap(data.heat_data);
    })
    .catch(err => console.error("stats api error:", err));

  /* ── 요약 카드 ───────────────────────────────────────────── */
  function fillSummary(s) {
    document.getElementById("s-unique").textContent   = s.unique_books.toLocaleString();
    document.getElementById("s-assign").textContent   = s.assign_total.toLocaleString();
    document.getElementById("s-top-cnt").textContent  = s.top_cat.count.toLocaleString();
    document.getElementById("s-top-name").textContent = s.top_cat.name;
    document.getElementById("s-avg").textContent      = s.avg_per_book;
  }

  /* ── 카테고리 탭 ─────────────────────────────────────────── */
  function buildCatTabs(sd) {
    const tabs = document.getElementById("catTabs");
    const cats = Object.keys(sd).filter(k => sd[k].length > 0);
    cats.forEach((cat, i) => {
      const btn = document.createElement("button");
      btn.className = "cat-btn" + (i === 0 ? " active" : "");
      btn.textContent = cat;
      btn.addEventListener("click", () => switchCat(cat, btn));
      tabs.appendChild(btn);
    });
    if (cats.length) switchCat(cats[0], tabs.firstChild);
  }

  function switchCat(cat, btn) {
    currentCat = cat;
    document.querySelectorAll(".cat-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    renderMain();
  }

  /* ── 차트 타입 버튼 ──────────────────────────────────────── */
  document.getElementById("typeRow").addEventListener("click", e => {
    const btn = e.target.closest(".type-btn");
    if (!btn) return;
    document.querySelectorAll(".type-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    currentType = btn.dataset.type;
    renderMain();
  });

  /* ── 메인 차트 ───────────────────────────────────────────── */
  function renderMain() {
    if (!currentCat) return;
    const items  = stackData[currentCat] || [];
    const labels = items.map(d => d.name);
    const values = items.map(d => d.count);
    const total  = values.reduce((a, b) => a + b, 0);

    document.getElementById("chartSub").textContent =
      items.length
        ? `총 ${total.toLocaleString()}권 (도서 제목 키워드 매칭) — 항목 클릭 시 도서 목록`
        : "해당 카테고리에서 매칭된 도서가 없습니다.";

    const mainCanvas = document.getElementById("mainCanvas");
    const wordCanvas = document.getElementById("wordCanvas");

    if (currentType === "word") {
      if (mainChart) { mainChart.destroy(); mainChart = null; }
      mainCanvas.style.display = "none";
      wordCanvas.style.display = "block";
      if (items.length) {
        requestAnimationFrame(() => renderWordCloud(items));
      }
      return;
    }

    mainCanvas.style.display = "block";
    wordCanvas.style.display = "none";

    if (!items.length) {
      if (mainChart) { mainChart.destroy(); mainChart = null; }
      return;
    }

    const cfg = currentType === "donut"
      ? buildDonutCfg(labels, values)
      : buildBarCfg(labels, values, currentType === "hbar");

    if (mainChart) mainChart.destroy();
    mainChart = new Chart(mainCanvas, cfg);
  }

  function buildBarCfg(labels, values, horizontal) {
    return {
      type: "bar",
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: PALETTE.slice(0, labels.length),
          borderRadius: 4,
          borderSkipped: false,
        }],
      },
      options: {
        indexAxis: horizontal ? "y" : "x",
        responsive: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            ...TOOLTIP_BASE,
            callbacks: valueTooltip(ctx => ctx.parsed[horizontal ? "x" : "y"]),
          },
        },
        scales: {
          x: { grid: { display: !horizontal }, ticks: { font: { size: 11 } } },
          y: { grid: { display: horizontal  }, ticks: { font: { size: 11 } } },
        },
        onClick: (_, elements) => {
          if (!elements.length) return;
          fetchAndShowPopover(currentCat, labels[elements[0].index]);
        },
      },
    };
  }

  function buildDonutCfg(labels, values) {
    return {
      type: "doughnut",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: PALETTE.slice(0, labels.length), borderWidth: 1 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        aspectRatio: 2.4,
        cutout: "60%",
        plugins: {
          legend: { position: "right", labels: { font: { size: 11 }, boxWidth: 12 } },
          tooltip: {
            ...TOOLTIP_BASE,
            callbacks: labeledTooltip(),
          },
        },
        onClick: (_, elements) => {
          if (!elements.length) return;
          fetchAndShowPopover(currentCat, labels[elements[0].index]);
        },
      },
    };
  }

  /* ── 워드클라우드 ─────────────────────────────────────────── */
  function renderWordCloud(items) {
    const canvas = document.getElementById("wordCanvas");
    canvas.width  = canvas.offsetWidth || 600;
    canvas.height = 260;

    const maxVal = Math.max(...items.map(d => d.count), 1);
    const words  = items.map(d => [d.name, Math.round(14 + (d.count / maxVal) * 52)]);
    WordCloud(canvas, {
      list: words,
      gridSize: 6,
      weightFactor: 1,
      fontFamily: "Pretendard, sans-serif",
      color: () => PALETTE[Math.floor(Math.random() * PALETTE.length)],
      rotateRatio: 0,
      backgroundColor: "#fafafa",
      shrinkToFit: true,
    });
  }

  /* ── 사이드 도넛 (카테고리 분포) ─────────────────────────── */
  function renderSideDonut(catCounts) {
    const entries = Object.entries(catCounts);
    const labels  = entries.map(e => e[0]);
    const values  = entries.map(e => e[1]);
    const total   = values.reduce((a, b) => a + b, 0);

    donutSideChart = new Chart(document.getElementById("donutSide"), {
      type: "doughnut",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: PALETTE.slice(0, labels.length), borderWidth: 1 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "65%",
        plugins: {
          legend: { display: false },
          tooltip: {
            ...TOOLTIP_BASE,
            callbacks: labeledTooltip(),
          },
        },
      },
    });

    const legend = document.getElementById("donutLegend");
    labels.forEach((name, i) => {
      const pct = total ? ((values[i] / total) * 100).toFixed(1) : 0;
      legend.insertAdjacentHTML("beforeend", `
        <div class="legend-item">
          <span class="legend-dot" style="background:${PALETTE[i]}"></span>
          <span class="legend-name">${name}</span>
          <span class="legend-count">${values[i].toLocaleString()}</span>
          <span class="legend-pct">${pct}%</span>
        </div>`);
    });
  }

  /* ── 난이도 분포 ──────────────────────────────────────────── */
  function renderDiff(diffCounts) {
    const labels = DIFF_ORDER.filter(k => diffCounts[k] !== undefined);
    const values = labels.map(k => diffCounts[k] || 0);
    const colors = labels.map(k => DIFF_COLORS[k]);

    diffChart = new Chart(document.getElementById("diffChart"), {
      type: "doughnut",
      data: {
        labels,
        datasets: [{ data: values, backgroundColor: colors, borderWidth: 1 }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "60%",
        plugins: {
          legend: { position: "bottom", labels: { font: { size: 12 }, boxWidth: 12, padding: 16 } },
          tooltip: {
            ...TOOLTIP_BASE,
            callbacks: labeledTooltip(),
          },
        },
      },
    });
  }

  /* ── 출판사별 Top 8 ───────────────────────────────────────── */
  function renderPub(pubTop) {
    const labels = pubTop.map(p => p[0]);
    const values = pubTop.map(p => p[1]);

    pubChart = new Chart(document.getElementById("pubChart"), {
      type: "bar",
      data: {
        labels,
        datasets: [{
          data: values,
          backgroundColor: PALETTE.slice(0, labels.length),
          borderRadius: 4,
          borderSkipped: false,
        }],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            ...TOOLTIP_BASE,
            callbacks: valueTooltip(ctx => ctx.parsed.x),
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
          y: { ticks: { font: { size: 12 } } },
        },
      },
    });
  }

  /* ── 히트맵 ──────────────────────────────────────────────── */
  function hexToRgb(hex) {
    const r = parseInt(hex.slice(1, 3), 16);
    const g = parseInt(hex.slice(3, 5), 16);
    const b = parseInt(hex.slice(5, 7), 16);
    return `${r},${g},${b}`;
  }

  function buildHeatmap(heatData) {
    const diffs = DIFF_ORDER;
    const cats  = Object.keys(heatData);
    if (!cats.length) return;

    const colMaxes = {};
    diffs.forEach(d => {
      colMaxes[d] = Math.max(...cats.map(c => heatData[c][d] || 0), 1);
    });

    let html = "<table class='heatmap'><thead><tr><th>카테고리</th>";
    diffs.forEach(d => { html += `<th>${d}</th>`; });
    html += "<th>합계</th></tr></thead><tbody>";

    cats.forEach(cat => {
      const row    = heatData[cat];
      const rowSum = diffs.reduce((a, d) => a + (row[d] || 0), 0);
      html += `<tr><td class="row-label">${cat}</td>`;
      diffs.forEach(d => {
        const val   = row[d] || 0;
        const alpha = val ? (0.12 + (val / colMaxes[d]) * 0.55).toFixed(2) : 0;
        const bg    = val ? `rgba(${hexToRgb(DIFF_COLORS[d])},${alpha})` : "transparent";
        html += `<td style="background:${bg}">${val || ""}</td>`;
      });
      html += `<td class="row-sum">${rowSum}</td></tr>`;
    });

    html += "</tbody></table>";
    document.getElementById("heatmapWrap").innerHTML = html;
  }

  /* ── 팝오버 ──────────────────────────────────────────────── */
  function fetchAndShowPopover(cat, tech) {
    const url = STATS_BOOKS_API_URL +
      `?cat=${encodeURIComponent(cat)}&tech=${encodeURIComponent(tech)}`;
    fetch(url)
      .then(r => r.json())
      .then(data => showPopover(data.tech, data.books, data.total))
      .catch(err => console.error("stats books api error:", err));
  }

  function showPopover(tech, books, total) {
    document.getElementById("popoverTitle").textContent = tech;
    document.getElementById("popoverCount").textContent = `총 ${total}권`;

    const body = document.getElementById("popoverBody");
    if (!books.length) {
      body.innerHTML = "<p class='popover-empty'>매칭된 도서가 없습니다.</p>";
    } else {
      body.innerHTML = books.map(b => {
        const diffClass = b.difficulty ? ` diff-${b.difficulty}` : "";
        const diffBadge = b.difficulty
          ? `<span class="popover-book-diff${diffClass}">${b.difficulty}</span>` : "";
        return `<a class="popover-book-item" href="${BOOK_DETAIL_BASE}${b.id}/">
          <span class="popover-book-title">${b.title}</span>
          ${diffBadge}
        </a>`;
      }).join("");
    }

    document.getElementById("statsBooksPopover").style.display = "flex";
  }

  /* 닫기 */
  document.getElementById("popoverClose").addEventListener("click", closePopover);
  document.getElementById("statsBooksPopover").addEventListener("click", e => {
    if (e.target === e.currentTarget) closePopover();
  });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") closePopover();
  });

  function closePopover() {
    document.getElementById("statsBooksPopover").style.display = "none";
  }
})();
