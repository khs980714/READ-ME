/**
 * READ:ME — Chat Page JS
 * AJAX / SSE 스트리밍 챗봇 메시지 송수신 및 추천 카드 렌더링
 */

const VISIBLE_CARDS   = 3;
const MODAL_PAGE_SIZE = 10;

// 모달 상태
const _modal = {
  allGroups:   [],    // [{rep, editions:[...]}, ...] 전체 그룹 목록
  page:        0,
  state:       "list", // "list" | "detail"
  detailGroup: null,   // 현재 상세보기 중인 그룹
};

const chatMessages  = document.getElementById("chatMessages");
const chatInput     = document.getElementById("chatInput");
const btnSend       = document.getElementById("btnSend");
const btnResetChat  = document.getElementById("btnResetChat");
const streamToggle  = document.getElementById("streamToggle");
const streamStatus  = document.getElementById("streamStatus");
const moreModal     = document.getElementById("moreModal");
const modalClose    = document.getElementById("modalClose");
const modalBack     = document.getElementById("modalBack");
const modalBookList = document.getElementById("modalBookList");
const modalTitle    = document.getElementById("modalTitle");
const modalPagination = document.getElementById("modalPagination");

// ── marked 설정 ───────────────────────────────────────────
if (typeof marked !== "undefined") {
  marked.setOptions({ breaks: true, gfm: true });
}

function renderMarkdown(text) {
  if (typeof marked !== "undefined") return marked.parse(text);
  return text.replace(/\n/g, "<br>");
}

// ── 스트리밍 토글 상태 ────────────────────────────────────
const STREAM_KEY = "readme_streaming";

streamToggle.checked = localStorage.getItem(STREAM_KEY) === "true";
updateStreamStatus();

streamToggle.addEventListener("change", () => {
  localStorage.setItem(STREAM_KEY, streamToggle.checked);
  updateStreamStatus();
});

function updateStreamStatus() {
  const on = streamToggle.checked;
  streamStatus.textContent = on ? "ON" : "OFF";
  streamStatus.className = "stream-status" + (on ? " on" : "");
}

// ── 초기 히스토리 복원 ────────────────────────────────────
if (typeof CHAT_HISTORY !== "undefined" && CHAT_HISTORY.length) {
  CHAT_HISTORY.forEach((msg) => appendMessage(msg));
  scrollBottom();
}

// ── 가이드 리스트 클릭 ────────────────────────────────────
document.querySelectorAll(".guide-list li").forEach((li) => {
  li.addEventListener("click", () => {
    const example = li.querySelector("p")?.textContent?.trim();
    if (example) {
      chatInput.value = example;
      chatInput.focus();
    }
  });
});

// ── 전송 이벤트 ──────────────────────────────────────────
btnSend.addEventListener("click", handleSend);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});

chatInput.addEventListener("input", () => {
  chatInput.style.height = "auto";
  chatInput.style.height = Math.min(chatInput.scrollHeight, 120) + "px";
});

async function handleSend() {
  const content = chatInput.value.trim();
  if (!content || btnSend.disabled) return;

  chatInput.value = "";
  chatInput.style.height = "auto";
  setLoading(true);

  appendMessage({ role: "user", content });
  scrollBottom();

  if (streamToggle.checked) {
    await handleStreamSend(content);
  } else {
    await handleNormalSend(content);
  }

  setLoading(false);
  scrollBottom();
}

// ── 오류 코드별 메시지 ────────────────────────────────────
function httpErrorMessage(status) {
  if (status === 429) return "요청이 너무 많습니다. 잠시 후 다시 시도해주세요. (Rate Limit)";
  if (status === 504 || status === 502) return "AI 서버 응답 시간이 초과되었습니다. 잠시 후 다시 시도해주세요. (Timeout)";
  if (status >= 500) return "서버 오류가 발생했습니다. 잠시 후 다시 시도해주세요.";
  return "오류가 발생했습니다. 잠시 후 다시 시도해주세요.";
}

// ── 일반 모드 (JSON 응답) ─────────────────────────────────
async function handleNormalSend(content) {
  const typingEl = appendTyping();
  scrollBottom();

  try {
    const res = await fetch(SEND_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF_TOKEN },
      body: JSON.stringify({ message: content }),
    });
    const data = await res.json();
    typingEl.remove();

    if (!res.ok) {
      appendMessage({ role: "assistant", content: data.error || httpErrorMessage(res.status) });
    } else {
      appendMessage({
        role: "assistant",
        content: data.answer,
        question_type: data.question_type,
        recommendations: data.recommendations || [],
      });
    }
  } catch {
    typingEl.remove();
    appendMessage({ role: "assistant", content: "서버에 연결할 수 없습니다. 네트워크 상태를 확인해주세요." });
  }
}

// ── 스트리밍 모드 (SSE) ───────────────────────────────────
async function handleStreamSend(content) {
  const { wrap, body, bubble } = createStreamBubble();
  chatMessages.appendChild(wrap);
  scrollBottom();

  let fullContent = "";

  try {
    const res = await fetch(STREAM_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF_TOKEN },
      body: JSON.stringify({ message: content }),
    });

    if (!res.ok) {
      bubble.classList.remove("streaming-waiting");
      bubble.textContent = httpErrorMessage(res.status);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let firstChunk = true;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });

      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);

        for (const line of block.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          let event;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }

          if (event.type === "answer_chunk") {
            if (firstChunk) {
              bubble.classList.remove("streaming-waiting");
              firstChunk = false;
            }
            fullContent += event.content;
            bubble.innerHTML = renderMarkdown(fullContent);
            scrollBottom();

          } else if (event.type === "done") {
            const qtype = event.question_type;
            convertTablesToList(bubble);
            if (qtype && qtype !== "out_of_scope") {
              const badge = document.createElement("span");
              badge.className = "qtype-badge";
              badge.textContent = qtypeLabel(qtype);
              body.insertBefore(badge, bubble);
            }
            if (event.recommendations?.length) {
              body.appendChild(renderRecommendations(event.recommendations, qtype));
              scrollBottom();
            }

          } else if (event.type === "error") {
            bubble.classList.remove("streaming-waiting");
            bubble.textContent = event.content || "오류가 발생했습니다. 잠시 후 다시 시도해주세요.";
          }
        }
      }
    }
  } catch {
    bubble.classList.remove("streaming-waiting");
    bubble.textContent = "서버에 연결할 수 없습니다. 네트워크 상태를 확인해주세요.";
  }
}

function createStreamBubble() {
  const wrap = document.createElement("div");
  wrap.className = "msg msg--assistant";

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.textContent = "AI";

  const body = document.createElement("div");
  body.className = "msg-body";

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble msg-bubble--markdown streaming-waiting";

  body.appendChild(bubble);
  wrap.appendChild(avatar);
  wrap.appendChild(body);
  return { wrap, body, bubble };
}

// ── 메시지 렌더링 ────────────────────────────────────────
function appendMessage(msg) {
  const wrap = document.createElement("div");
  wrap.className = `msg msg--${msg.role}`;

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.textContent = msg.role === "user" ? "나" : "AI";

  const body = document.createElement("div");
  body.className = "msg-body";

  if (msg.role === "assistant" && msg.question_type && msg.question_type !== "out_of_scope") {
    const badge = document.createElement("span");
    badge.className = "qtype-badge";
    badge.textContent = qtypeLabel(msg.question_type);
    body.appendChild(badge);
  }

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";

  if (msg.role === "assistant") {
    bubble.classList.add("msg-bubble--markdown");
    bubble.innerHTML = renderMarkdown(msg.content);
    convertTablesToList(bubble);
  } else {
    bubble.textContent = msg.content;
  }

  body.appendChild(bubble);

  if (msg.role === "assistant" && msg.recommendations?.length) {
    body.appendChild(renderRecommendations(msg.recommendations, msg.question_type));
  }

  wrap.appendChild(avatar);
  wrap.appendChild(body);
  chatMessages.appendChild(wrap);
  return wrap;
}

function appendTyping() {
  const wrap = document.createElement("div");
  wrap.className = "msg msg--assistant";

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.textContent = "AI";

  const body = document.createElement("div");
  body.className = "msg-body";

  const indicator = document.createElement("div");
  indicator.className = "typing-indicator";
  for (let i = 0; i < 3; i++) {
    const dot = document.createElement("span");
    dot.className = "typing-dot";
    indicator.appendChild(dot);
  }
  body.appendChild(indicator);
  wrap.appendChild(avatar);
  wrap.appendChild(body);
  chatMessages.appendChild(wrap);
  return wrap;
}

// ── 추천 목록 → 제목별 그룹화 ─────────────────────────────
function _codeNum(code) {
  if (!code) return Infinity;
  const m = code.match(/(\d+)$/);
  return m ? parseInt(m[1], 10) : Infinity;
}

function groupRecommendations(recs) {
  const map = new Map(); // title → [{rec}, ...]
  for (const rec of recs) {
    const key = rec.title;
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(rec);
  }
  // 각 그룹 내부: book_code 오름차순 정렬 → 첫 번째가 대표
  const groups = [];
  for (const editions of map.values()) {
    editions.sort((a, b) => _codeNum(a.book_code) - _codeNum(b.book_code));
    groups.push({ rep: editions[0], editions });
  }
  // 그룹 순서: 대표의 rank 오름차순
  groups.sort((a, b) => a.rep.rank - b.rep.rank);
  return groups;
}

// ── 추천 카드 섹션 렌더링 ────────────────────────────────
function renderRecommendations(recs, qtype) {
  const groups = groupRecommendations(recs);
  _modal.allGroups = groups; // 모달에서 재사용

  const section = document.createElement("div");
  section.className = "rec-section";

  const label = document.createElement("div");
  label.className = "rec-label";
  label.textContent = "추천 도서";
  section.appendChild(label);

  const cardsWrap = document.createElement("div");
  cardsWrap.className = "rec-cards";

  const visibleGroups = groups.slice(0, VISIBLE_CARDS);
  visibleGroups.forEach((g, i) => {
    cardsWrap.appendChild(makeGroupCard(g, groups, i + 1));
  });
  section.appendChild(cardsWrap);

  if (groups.length > VISIBLE_CARDS) {
    const moreBtn = document.createElement("button");
    moreBtn.className = "btn-more";
    moreBtn.textContent = `더보기 (총 ${recs.length}권)`;
    moreBtn.addEventListener("click", () => openListModal(groups));
    section.appendChild(moreBtn);
  }

  return section;
}

// ── 도서 플레이스홀더 아이콘 (표지 없을 때 공용) ─────────
function bookPlaceholderSVG() {
  return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
      <path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>
    </svg>`;
}

// ── 추천 카드 (그룹 대표) ────────────────────────────────
// onSelect가 주어지면 클릭/엔터 시 그것을 호출하고, 없으면 기본 상세 모달을 엽니다.
function makeGroupCard(group, allGroups, displayRank, onSelect) {
  const { rep, editions } = group;
  const div = document.createElement("div");
  div.className = "rec-card";
  div.setAttribute("role", "button");
  div.setAttribute("tabindex", "0");
  const select = onSelect || (() => openDetailModal(group, allGroups));
  div.addEventListener("click", select);
  div.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") select();
  });

  const thumb = document.createElement("div");
  thumb.className = "rec-card-thumb";
  if (rep.thumbnail_url) {
    const img = document.createElement("img");
    img.src = rep.thumbnail_url;
    img.alt = rep.title;
    thumb.appendChild(img);
  } else {
    const ph = document.createElement("div");
    ph.className = "rec-card-thumb-placeholder";
    ph.innerHTML = bookPlaceholderSVG();
    thumb.appendChild(ph);
  }
  const rank = displayRank != null ? displayRank : rep.rank;
  if (rank) {
    const rankBadge = document.createElement("span");
    rankBadge.className = "rec-card-rank";
    rankBadge.textContent = `#${rank}`;
    thumb.appendChild(rankBadge);
  }

  const info = document.createElement("div");
  info.className = "rec-card-info";

  const title = document.createElement("div");
  title.className = "rec-card-title";
  title.textContent = rep.title;

  const author = document.createElement("div");
  author.className = "rec-card-author";
  author.textContent = rep.author;

  const meta = document.createElement("div");
  meta.className = "rec-card-meta";
  if (rep.difficulty) {
    const badge = document.createElement("span");
    badge.className = `badge badge--${rep.difficulty}`;
    badge.textContent = rep.difficulty;
    meta.appendChild(badge);
  }
  // 판차 뱃지 (복수 edition)
  if (editions.length > 1) {
    const edBadge = document.createElement("span");
    edBadge.className = "rec-card-edition-count";
    edBadge.textContent = `${editions.length}종`;
    meta.appendChild(edBadge);
  }

  info.appendChild(title);
  info.appendChild(author);
  info.appendChild(meta);

  div.appendChild(thumb);
  div.appendChild(info);
  return div;
}

// ── 모달: 목록(더보기) 열기 ──────────────────────────────
function openListModal(groups) {
  _modal.allGroups = groups;
  _modal.page      = 0;
  _modal.state     = "list";
  _modal.detailGroup    = null;
  modalBack.hidden = true;
  modalTitle.textContent = `도서 목록 (총 ${groups.reduce((s, g) => s + g.editions.length, 0)}권)`;
  renderModalListPage();
  moreModal.hidden = false;
  document.body.style.overflow = "hidden";
}

function renderModalListPage() {
  const total      = _modal.allGroups.length;
  const totalPages = Math.ceil(total / MODAL_PAGE_SIZE);
  const start      = _modal.page * MODAL_PAGE_SIZE;
  const pageGroups = _modal.allGroups.slice(start, start + MODAL_PAGE_SIZE);

  modalBookList.innerHTML = "";
  pageGroups.forEach((g, i) => {
    const displayRank = start + i + 1;
    const card = makeGroupCard(g, _modal.allGroups, displayRank, () => openDetailFromList(g));
    card.style.width = "100%";
    modalBookList.appendChild(card);
  });
  modalBookList.scrollTop = 0;

  modalPagination.innerHTML = "";
  if (totalPages <= 1) return;

  const prev = document.createElement("button");
  prev.className = "btn-page";
  prev.textContent = "이전";
  prev.disabled = _modal.page === 0;
  prev.addEventListener("click", () => { _modal.page--; renderModalListPage(); });

  const info = document.createElement("span");
  info.className = "modal-page-info";
  info.textContent = `${_modal.page + 1} / ${totalPages} 페이지`;

  const next = document.createElement("button");
  next.className = "btn-page";
  next.textContent = "다음";
  next.disabled = _modal.page >= totalPages - 1;
  next.addEventListener("click", () => { _modal.page++; renderModalListPage(); });

  modalPagination.appendChild(prev);
  modalPagination.appendChild(info);
  modalPagination.appendChild(next);
}

// ── 모달: 상세보기 열기 ──────────────────────────────────
function _showDetailPane(group) {
  _modal.detailGroup = group;
  _modal.state = "detail";
  modalBack.hidden = false;
  modalTitle.textContent = group.rep.title;
  renderModalDetail(group);
}

function openDetailModal(group, allGroups) {
  _modal.allGroups = allGroups || _modal.allGroups;
  _showDetailPane(group);
  moreModal.hidden = false;
  document.body.style.overflow = "hidden";
}

function openDetailFromList(group) {
  _showDetailPane(group);
}

function renderModalDetail(group) {
  const { rep, editions } = group;

  modalBookList.innerHTML = "";
  modalPagination.innerHTML = "";

  const detail = document.createElement("div");
  detail.className = "book-detail-view";

  // 상단: 썸네일 + 기본 정보
  const header = document.createElement("div");
  header.className = "book-detail-header";

  const thumb = document.createElement("div");
  thumb.className = "book-detail-thumb";
  if (rep.thumbnail_url) {
    const img = document.createElement("img");
    img.src = rep.thumbnail_url;
    img.alt = rep.title;
    thumb.appendChild(img);
  } else {
    thumb.innerHTML = bookPlaceholderSVG();
  }

  const meta = document.createElement("div");
  meta.className = "book-detail-meta";

  const metaTitle = document.createElement("div");
  metaTitle.className = "book-detail-title";
  metaTitle.textContent = rep.title;

  const metaAuthor = document.createElement("div");
  metaAuthor.className = "book-detail-author";
  metaAuthor.textContent = `${rep.author} · ${rep.publisher}`;

  const metaBadges = document.createElement("div");
  metaBadges.className = "book-detail-badges";
  if (rep.difficulty) {
    const b = document.createElement("span");
    b.className = `badge badge--${rep.difficulty}`;
    b.textContent = rep.difficulty;
    metaBadges.appendChild(b);
  }

  meta.appendChild(metaTitle);
  meta.appendChild(metaAuthor);
  meta.appendChild(metaBadges);

  header.appendChild(thumb);
  header.appendChild(meta);
  detail.appendChild(header);

  // 판차 목록
  const edSection = document.createElement("div");
  edSection.className = "book-detail-editions";

  const edLabel = document.createElement("div");
  edLabel.className = "book-detail-editions-label";
  edLabel.textContent = editions.length > 1 ? "판차 목록" : "도서 링크";
  edSection.appendChild(edLabel);

  editions.forEach((ed) => {
    const link = document.createElement("a");
    link.className = "book-edition-link";
    link.href = BOOK_DETAIL_URL.replace("{id}", ed.id);
    link.textContent = ed.edition
      ? `${ed.edition}  (${ed.book_code})`
      : `자세히 보기  (${ed.book_code})`;
    link.title = ed.title + (ed.edition ? ` ${ed.edition}` : "");
    edSection.appendChild(link);
  });

  detail.appendChild(edSection);
  modalBookList.appendChild(detail);
}

// ── 모달 뒤로가기 ────────────────────────────────────────
modalBack.addEventListener("click", () => {
  _modal.state  = "list";
  _modal.detailGroup = null;
  modalBack.hidden = true;
  modalTitle.textContent = `도서 목록 (총 ${_modal.allGroups.reduce((s, g) => s + g.editions.length, 0)}권)`;
  renderModalListPage();
});

modalClose.addEventListener("click", closeModal);
moreModal.addEventListener("click", (e) => { if (e.target === moreModal) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

function closeModal() {
  moreModal.hidden = true;
  document.body.style.overflow = "";
  _modal.state  = "list";
  _modal.detailGroup = null;
  modalBack.hidden = true;
}

// ── 대화 초기화 ──────────────────────────────────────────
if (btnResetChat) {
  btnResetChat.addEventListener("click", () => {
    if (!confirm("대화 내용을 모두 초기화할까요?")) return;
    location.reload();
  });
}

// ── 모바일: 테이블 → 카드 리스트 변환 ────────────────────
function convertTablesToList(container) {
  if (window.innerWidth > 768) return;
  container.querySelectorAll("table").forEach((table) => {
    const headers = [...table.querySelectorAll("thead th")].map((th) => th.textContent.trim());
    const rows = table.querySelectorAll("tbody tr");

    const list = document.createElement("div");
    list.className = "mobile-table-list";

    rows.forEach((row) => {
      const card = document.createElement("div");
      card.className = "mobile-table-card";

      row.querySelectorAll("td").forEach((cell, i) => {
        const item = document.createElement("div");
        item.className = "mobile-table-item";

        if (headers[i]) {
          const lbl = document.createElement("span");
          lbl.className = "mobile-table-label";
          lbl.textContent = headers[i] + ":";
          item.appendChild(lbl);
        }

        const val = document.createElement("span");
        val.className = "mobile-table-value";
        val.innerHTML = cell.innerHTML;
        item.appendChild(val);

        card.appendChild(item);
      });

      list.appendChild(card);
    });

    table.parentNode.replaceChild(list, table);
  });
}

// ── 유틸 ────────────────────────────────────────────────
function qtypeLabel(type) {
  return {
    keyword_search:      "🔎 도서 목록 조회",
    specific_search:     "🔍 기술·키워드 탐색",
    goal_oriented:       "🗺️ 진로·목적 큐레이션",
    career_certification:"🏆 자격증·포트폴리오",
    level_based:         "📊 수준별 추천",
  }[type] || type;
}

function setLoading(on) {
  btnSend.disabled = on;
  chatInput.disabled = on;
}

function scrollBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}
