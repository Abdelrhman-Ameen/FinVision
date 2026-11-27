"use strict";

const byId = (id) => document.getElementById(id);
const question = byId("question");
const timeline = byId("timeline");
const sourcesPanel = byId("sources");
const sourceToggle = byId("source-toggle");
const compact = matchMedia("(max-width: 800px)");
let previous = [];
let current = null;

function element(tag, text = "", className = "") {
  const item = document.createElement(tag);
  item.textContent = text;
  if (className) item.className = className;
  return item;
}

function link(label, url) {
  const parsed = new URL(url);
  if (parsed.protocol !== "https:") return element("span", label);
  const item = element("a", label);
  item.href = parsed.href;
  item.target = "_blank";
  item.rel = "noopener noreferrer";
  return item;
}

function showSources(open) {
  sourcesPanel.toggleAttribute("data-open", open);
  sourceToggle.setAttribute("aria-expanded", String(open));
}

sourceToggle.addEventListener("click", () => {
  const open = !sourcesPanel.hasAttribute("data-open");
  showSources(open);
  if (open) {
    byId("source-close").focus({preventScroll: true});
    sourcesPanel.scrollIntoView({block: "nearest"});
  }
});

byId("source-close").addEventListener("click", () => {
  showSources(false);
  sourceToggle.focus();
});

function sourceCards(sources, references, active) {
  const records = sources.length ? sources : references;
  const region = byId("source-list");
  byId("empty-sources").hidden = records.length > 0;
  const count = byId("source-count");
  count.hidden = !records.length;
  count.textContent = sources.length ? String(records.length) : "مراجع مرتبطة";
  const mobileCount = byId("mobile-source-count");
  mobileCount.hidden = !records.length;
  mobileCount.textContent = String(records.length);
  region.replaceChildren(...records.map((source) => {
    const card = element("details", "", sources.length ? "source-card" : "source-card reference");
    card.open = source.number === active;
    const summary = element("summary");
    if (sources.length) {
      const number = element("span", String(source.number), "source-number");
      number.dir = "ltr";
      summary.append(number);
    }
    const title = element("span", source.title);
    title.dir = "auto";
    summary.append(title);
    card.append(summary);
    const detail = element("div", "", "source-detail");
    const metadata = [source.authors.join("، "), source.publisher, source.published, source.locator].filter(Boolean);
    const details = element("small", metadata.join(" · "));
    details.dir = "auto";
    detail.append(details);
    const excerpt = element("p", source.excerpt);
    excerpt.dir = "auto";
    detail.append(excerpt, link("افتح المصدر", source.url));
    if (source.license) {
      detail.append(link("رخصة المصدر", source.license));
      detail.append(element("small", [source.rights_holder, source.modifications].filter(Boolean).join(" · ")));
    }
    card.append(detail);
    return card;
  }));
  if (active) {
    if (compact.matches) showSources(true);
    const opened = region.querySelector("details[open]");
    if (opened) {
      const header = opened.querySelector("summary");
      header.focus({preventScroll: true});
      opened.scrollIntoView({block: "nearest"});
    }
  }
}

function addMessage(text, style) {
  const message = element("article", text, "message " + style);
  if (text) message.dir = "auto";
  byId("messages").append(message);
  timeline.scrollTop = timeline.scrollHeight;
  return message;
}

function showAnswer(result, placeholder) {
  placeholder.className = "message answer";
  placeholder.replaceChildren();
  for (const block of result.blocks) {
    const numbers = new Set(block.citations);
    const text = block.text.replace(/\s*\[\s*(\d+)\s*\]/g, (marker, number) =>
      numbers.has(Number(number)) ? "" : marker
    ).trim();
    const paragraph = element("p", text, "answer-block");
    paragraph.dir = "auto";
    for (const number of numbers) {
      const button = element("button", String(number), "citation");
      button.type = "button";
      button.setAttribute("aria-label", "عرض المصدر " + number);
      button.addEventListener("click", () => sourceCards(result.sources, result.references, number));
      paragraph.append(button);
    }
    placeholder.append(paragraph);
  }
  if (result.limitations) {
    const limit = element("p", result.limitations, "limit");
    limit.dir = "auto";
    placeholder.append(limit);
  }
  sourceCards(result.sources, result.references);
  timeline.scrollTop = timeline.scrollHeight;
}

function enableSend() {
  byId("send").disabled = current !== null || !question.value.trim();
  const counter = byId("length");
  counter.hidden = question.value.length === 0;
  counter.textContent = `${question.value.length} / ${question.maxLength}`;
}

async function send(event) {
  event.preventDefault();
  const text = question.value.trim();
  if (!text || current) return;
  const request = new AbortController();
  current = request;
  const context = previous.slice(-3);
  question.value = "";
  enableSend();
  byId("welcome").hidden = true;
  addMessage(text, "ask");
  const placeholder = addMessage("بدور على مصادر للسؤال…", "pending");
  try {
    const response = await fetch("/api/chat", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: text, previous_questions: context}),
      signal: request.signal,
    });
    if (request !== current) return;
    const result = await response.json();
    if (!response.ok) throw new Error(result.error?.message || result.detail || "الطلب ماكملش.");
    if (result.route !== "conversation") previous = [...context, text].slice(-3);
    showAnswer(result, placeholder);
  } catch (error) {
    if (request !== current || error.name === "AbortError") return;
    placeholder.className = "message error";
    placeholder.textContent = error instanceof TypeError ? "الاتصال بالسيرفر اتقطع. جرّب تاني." : error.message;
    if (!question.value.trim()) question.value = text;
  } finally {
    if (request === current) {
      current = null;
      enableSend();
      question.focus({preventScroll: true});
    }
  }
}

byId("ask").addEventListener("submit", send);
question.addEventListener("input", enableSend);
question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!current && question.value.trim()) byId("ask").requestSubmit();
  }
});

document.querySelectorAll(".suggestion").forEach((button) => {
  button.addEventListener("click", () => {
    question.value = button.querySelector(".suggestion-text").textContent.trim();
    enableSend();
    question.focus();
  });
});

byId("reset").addEventListener("click", () => {
  current?.abort();
  current = null;
  previous = [];
  question.value = "";
  byId("messages").replaceChildren();
  byId("welcome").hidden = false;
  sourceCards([], []);
  showSources(false);
  enableSend();
  question.focus();
});

enableSend();
fetch("/api/status")
  .then((response) => {
    if (!response.ok) throw new Error();
    return response.json();
  })
  .then((status) => {
    const availability = byId("availability");
    const text = byId("availability-text");
    availability.title = status.model;
    if (status.provider === "ollama") {
      text.textContent = status.ready ? "شغّال محليًا" : "موديل Gemma المحلي مش متاح";
    } else {
      text.textContent = status.ready ? "Gemini · جاهز" : "مفتاح Gemini محتاج إعداد";
    }
    availability.dataset.state = status.ready ? "ready" : "issue";
  })
  .catch(() => {
    byId("availability-text").textContent = "تعذر الاتصال بالسيرفر";
    byId("availability").dataset.state = "issue";
  });
