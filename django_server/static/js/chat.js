/**
 * READ:ME — Chat Page JS
 * AJAX 챗봇 메시지 송수신 및 추천 카드 렌더링
 */

const VISIBLE_CARDS = 3;

const chatMessages = document.getElementById("chatMessages");
const chatInput    = document.getElementById("chatInput");
const btnSend      = document.getElementById("btnSend");
const btnNewChat   = document.getElementById("btnNewChat");
const btnResetChat = document.getElementById("btnResetChat");
const moreModal    = document.getElementById("moreModal");
const modalClose   = document.getElementById("modalClose");
const modalBookList = document.getElementById("modalBookList");

// marked 설정 (marked.js 로드된 경우)
if (typeof marked !== "undefined") {
  marked.setOptions({
    breaks: true,
    gfm: true,
  });
}

function renderMarkdown(text) {
  if (typeof marked !== "undefined") {
    // XSS 방지: DOMPurify 없이 사용할 때는 innerHTML 직접 사용
    // marked는 기본적으로 HTML 이스케이프를 하므로 안전
    return marked.parse(text);
  }
  // fallback: 줄바꿈만 처리
  return text.replace(/\n/g, "<br>");
}

// ── 초기 히스토리 복원 ─────────────────────────────────────

if (typeof CHAT_HISTORY !== "undefined" && CHAT_HISTORY.length) {
  CHAT_HISTORY.forEach((msg) => appendMessage(msg));
  scrollBottom();
}

// ── 가이드 리스트 클릭 ─────────────────────────────────────

document.querySelectorAll(".guide-list li").forEach((li) => {
  li.addEventListener("click", () => {
    const example = li.querySelector("p")?.textContent?.trim();
    if (example) {
      chatInput.value = example;
      chatInput.focus();
    }
  });
});

// ── 전송 이벤트 ───────────────────────────────────────────

btnSend.addEventListener("click", handleSend);
chatInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});

// textarea 자동 높이
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

  // 사용자 메시지 표시
  appendMessage({ role: "user", content });
  scrollBottom();

  // 타이핑 인디케이터
  const typingEl = appendTyping();
  scrollBottom();

  try {
    const res = await fetch(SEND_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": CSRF_TOKEN,
      },
      body: JSON.stringify({ message: content }),
    });

    const data = await res.json();
    typingEl.remove();

    if (!res.ok) {
      appendMessage({ role: "assistant", content: data.error || "오류가 발생했습니다." });
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
    appendMessage({ role: "assistant", content: "서버에 연결할 수 없습니다. 잠시 후 다시 시도해주세요." });
  } finally {
    setLoading(false);
    scrollBottom();
  }
}

// ── 메시지 렌더링 ──────────────────────────────────────────

function appendMessage(msg) {
  const wrap = document.createElement("div");
  wrap.className = `msg msg--${msg.role}`;

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.textContent = msg.role === "user" ? "나" : "AI";

  const body = document.createElement("div");
  body.className = "msg-body";

  // 질문 유형 뱃지
  if (msg.role === "assistant" && msg.question_type && msg.question_type !== "unrelated") {
    const badge = document.createElement("span");
    badge.className = "qtype-badge";
    badge.textContent = qtypeLabel(msg.question_type);
    body.appendChild(badge);
  }

  // 말풍선
  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";

  if (msg.role === "assistant") {
    // AI 응답: 마크다운 렌더링
    bubble.classList.add("msg-bubble--markdown");
    bubble.innerHTML = renderMarkdown(msg.content);
  } else {
    // 사용자 메시지: 텍스트 그대로
    bubble.textContent = msg.content;
  }

  body.appendChild(bubble);

  // 추천 카드
  if (msg.role === "assistant" && msg.recommendations?.length) {
    body.appendChild(renderRecommendations(msg.recommendations));
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

// ── 추천 카드 ─────────────────────────────────────────────

function renderRecommendations(recs) {
  const section = document.createElement("div");
  section.className = "rec-section";

  const label = document.createElement("div");
  label.className = "rec-label";
  label.textContent = "추천 도서";
  section.appendChild(label);

  const cardsWrap = document.createElement("div");
  cardsWrap.className = "rec-cards";

  const visible = recs.slice(0, VISIBLE_CARDS);
  const hidden  = recs.slice(VISIBLE_CARDS);

  visible.forEach((rec) => cardsWrap.appendChild(makeCard(rec)));
  section.appendChild(cardsWrap);

  if (hidden.length) {
    const moreBtn = document.createElement("button");
    moreBtn.className = "btn-more";
    moreBtn.textContent = `더보기 +${hidden.length}`;
    moreBtn.addEventListener("click", () => openModal(hidden));
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

  info.appendChild(title);
  info.appendChild(author);
  info.appendChild(meta);

  a.appendChild(thumb);
  a.appendChild(info);
  return a;
}

// ── 모달 ──────────────────────────────────────────────────

function openModal(recs) {
  modalBookList.innerHTML = "";
  recs.forEach((rec) => {
    const card = makeCard(rec);
    card.style.width = "100%";
    modalBookList.appendChild(card);
  });
  moreModal.hidden = false;
  document.body.style.overflow = "hidden";
}

modalClose.addEventListener("click", closeModal);
moreModal.addEventListener("click", (e) => { if (e.target === moreModal) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

function closeModal() {
  moreModal.hidden = true;
  document.body.style.overflow = "";
}

// ── 새 대화 / 대화 초기화 ─────────────────────────────────

btnNewChat.addEventListener("click", () => {
  if (!confirm("새 대화를 시작할까요?")) return;
  document.cookie = "readme_chat_session=; Max-Age=0; path=/;";
  location.reload();
});

if (btnResetChat) {
  btnResetChat.addEventListener("click", () => {
    if (!confirm("대화 내용을 모두 초기화할까요?")) return;
    // 화면에서 모든 메시지 제거 후 초기 메시지만 표시
    chatMessages.innerHTML = `
      <div class="msg msg--assistant">
        <div class="msg-avatar">AI</div>
        <div class="msg-body">
          <div class="msg-bubble">
            안녕하세요! 수강생님<br>
            기본적인 도서 질문, 주제 로드맵, 수준 별 도서 질문과 같은 도서와 연관된 질문을 해주세요.
          </div>
        </div>
      </div>`;
    // 세션 쿠키 제거
    document.cookie = "readme_chat_session=; Max-Age=0; path=/;";
  });
}

// ── 유틸 ──────────────────────────────────────────────────

function qtypeLabel(type) {
  return { roadmap: "🗺️ 학습 로드맵", level_based: "📊 수준별 추천", general: "💬 도서 질문" }[type] || type;
}

function setLoading(on) {
  btnSend.disabled = on;
  chatInput.disabled = on;
}

function scrollBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}
