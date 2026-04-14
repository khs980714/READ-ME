/**
 * READ:ME — Chat Page JS
 * AJAX / SSE 스트리밍 챗봇 메시지 송수신 및 추천 카드 렌더링
 */

const VISIBLE_CARDS  = 3;
const MODAL_PAGE_SIZE = 10;

let _modalAllRecs = [];
let _modalPage    = 0;

const chatMessages  = document.getElementById("chatMessages");
const chatInput     = document.getElementById("chatInput");
const btnSend       = document.getElementById("btnSend");
const btnResetChat  = document.getElementById("btnResetChat");
const streamToggle  = document.getElementById("streamToggle");
const streamStatus  = document.getElementById("streamStatus");
const moreModal     = document.getElementById("moreModal");
const modalClose    = document.getElementById("modalClose");
const modalBookList = document.getElementById("modalBookList");

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
  // 빈 말풍선 생성
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

      // SSE 이벤트 단위(\n\n) 파싱
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
              // 첫 토큰 수신 시 타이핑 표시 제거
              bubble.classList.remove("streaming-waiting");
              firstChunk = false;
            }
            fullContent += event.content;
            bubble.innerHTML = renderMarkdown(fullContent);
            scrollBottom();

          } else if (event.type === "done") {
            const qtype = event.question_type;
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
            // content에 구체적인 오류 메시지가 없으면 기본 안내 표시
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

// ── 추천 카드 ────────────────────────────────────────────
// 단계별 큐레이션 유형은 전체 카드를 바로 노출합니다.
const FULL_VISIBLE_TYPES = new Set(["goal_oriented", "career_certification"]);

function renderRecommendations(recs, qtype) {
  const section = document.createElement("div");
  section.className = "rec-section";

  const label = document.createElement("div");
  label.className = "rec-label";
  label.textContent = "추천 도서";
  section.appendChild(label);

  const cardsWrap = document.createElement("div");
  cardsWrap.className = "rec-cards";

  const showAll = FULL_VISIBLE_TYPES.has(qtype);
  const visible = showAll ? recs : recs.slice(0, VISIBLE_CARDS);

  visible.forEach((rec) => cardsWrap.appendChild(makeCard(rec)));
  section.appendChild(cardsWrap);

  if (!showAll && recs.length > VISIBLE_CARDS) {
    const moreBtn = document.createElement("button");
    moreBtn.className = "btn-more";
    moreBtn.textContent = `더보기 (총 ${recs.length}권)`;
    moreBtn.addEventListener("click", () => openModal(recs));
    section.appendChild(moreBtn);
  }

  return section;
}

function makeCard(rec) {
  const url = BOOK_DETAIL_URL.replace("{id}", rec.id);
  const a = document.createElement("a");
  a.className = "rec-card";
  a.href = url;

  const thumb = document.createElement("div");
  thumb.className = "rec-card-thumb";
  if (rec.thumbnail_url) {
    const img = document.createElement("img");
    img.src = rec.thumbnail_url;
    img.alt = rec.title;
    thumb.appendChild(img);
  } else {
    const ph = document.createElement("div");
    ph.className = "rec-card-thumb-placeholder";
    ph.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
      <path d="M4 19.5A2.5 2.5 0 016.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 014 19.5v-15A2.5 2.5 0 016.5 2z"/>
    </svg>`;
    thumb.appendChild(ph);
  }
  if (rec.rank) {
    const rankBadge = document.createElement("span");
    rankBadge.className = "rec-card-rank";
    rankBadge.textContent = `#${rec.rank}`;
    thumb.appendChild(rankBadge);
  }

  const info = document.createElement("div");
  info.className = "rec-card-info";

  const title = document.createElement("div");
  title.className = "rec-card-title";
  title.textContent = rec.title;

  const author = document.createElement("div");
  author.className = "rec-card-author";
  author.textContent = rec.author;

  const meta = document.createElement("div");
  meta.className = "rec-card-meta";
  if (rec.difficulty) {
    const badge = document.createElement("span");
    badge.className = `badge badge--${rec.difficulty}`;
    badge.textContent = rec.difficulty;
    meta.appendChild(badge);
  }
  if (rec.score != null) {
    const scoreEl = document.createElement("span");
    scoreEl.className = "rec-card-score";
    scoreEl.textContent = `유사도 ${Math.round(rec.score * 100)}%`;
    meta.appendChild(scoreEl);
  }

  info.appendChild(title);
  info.appendChild(author);
  info.appendChild(meta);

  a.appendChild(thumb);
  a.appendChild(info);
  return a;
}

// ── 모달 ────────────────────────────────────────────────
const modalTitle = document.getElementById("modalTitle");
const modalPagination = document.getElementById("modalPagination");

function openModal(recs) {
  _modalAllRecs = recs;
  _modalPage    = 0;
  modalTitle.textContent = `도서 목록 (총 ${recs.length}권)`;
  renderModalPage();
  moreModal.hidden = false;
  document.body.style.overflow = "hidden";
}

function renderModalPage() {
  const total      = _modalAllRecs.length;
  const totalPages = Math.ceil(total / MODAL_PAGE_SIZE);
  const start      = _modalPage * MODAL_PAGE_SIZE;
  const pageRecs   = _modalAllRecs.slice(start, start + MODAL_PAGE_SIZE);

  modalBookList.innerHTML = "";
  pageRecs.forEach((rec) => {
    const card = makeCard(rec);
    card.style.width = "100%";
    modalBookList.appendChild(card);
  });
  modalBookList.scrollTop = 0;

  modalPagination.innerHTML = "";
  if (totalPages <= 1) return;

  const prev = document.createElement("button");
  prev.className = "btn-page";
  prev.textContent = "이전";
  prev.disabled = _modalPage === 0;
  prev.addEventListener("click", () => { _modalPage--; renderModalPage(); });

  const info = document.createElement("span");
  info.className = "modal-page-info";
  info.textContent = `${_modalPage + 1} / ${totalPages} 페이지`;

  const next = document.createElement("button");
  next.className = "btn-page";
  next.textContent = "다음";
  next.disabled = _modalPage >= totalPages - 1;
  next.addEventListener("click", () => { _modalPage++; renderModalPage(); });

  modalPagination.appendChild(prev);
  modalPagination.appendChild(info);
  modalPagination.appendChild(next);
}

modalClose.addEventListener("click", closeModal);
moreModal.addEventListener("click", (e) => { if (e.target === moreModal) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

function closeModal() {
  moreModal.hidden = true;
  document.body.style.overflow = "";
}

// ── 대화 초기화 ──────────────────────────────────────────
if (btnResetChat) {
  btnResetChat.addEventListener("click", () => {
    if (!confirm("대화 내용을 모두 초기화할까요?")) return;
    location.reload();
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
