import { marked } from "marked";
import DOMPurify from "dompurify";
import katex from "katex";
import "katex/dist/katex.min.css";
import { api } from "./api.js";

const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const stripLabel = (cap) => (cap || "").replace(/^(Figure|Fig\.|Table)\s*\S+\s*[:.|]?\s*/i, "");
export const slug = (t) => t.trim().toLowerCase().replace(/[^\p{L}\p{N}\s-]/gu, "").replace(/\s+/g, "-");

/**
 * 리뷰/채팅 공용 마크다운 렌더러.
 *  - $...$ / $$...$$ → KaTeX
 *  - [p.N] → 클릭 가능한 인용 (data-page)
 *  - ![[fig:ID]] → 추출된 그림 (tools=true 면 확대/영역 수정/제거 버튼)
 *  - ## 제목에 id=sec-N 부여 (목차 이동용)
 */
export function renderMd(md, { id, figs = [], tools = false } = {}) {
  const maths = [];
  const stash = (t, display) => `@@M${maths.push([t, display]) - 1}@@`;
  let s = (md || "")
    .replace(/\$\$([\s\S]+?)\$\$/g, (_, t) => stash(t, true))
    .replace(/(?<![\\$\w])\$(?=\S)([^\n$]*?\S)\$(?![\d$\w])/g, (_, t) => stash(t, false))
    .replace(/!\[\[fig:([A-Za-z0-9-]+)\]\]/g, (_, fid) => {
      const f = figs.find((x) => x.id === fid && x.file);
      if (!f) return `<span class="muted">[그림 없음: ${esc(fid)}]</span>`;
      const bar = tools
        ? `<div class="fig-tools"><button data-act="zoom" data-fig="${esc(fid)}">🔍 확대</button><button data-act="edit" data-fig="${esc(fid)}">✂ 영역 수정</button><button data-act="remove" data-fig="${esc(fid)}">✕ 본문에서 제거</button></div>`
        : "";
      return `\n\n<figure class="fig" data-fig="${esc(fid)}"><img src="${api.figUrl(id, f)}" alt="${esc(f.label)}"><figcaption><b>${esc(f.label)}</b> — ${esc(stripLabel(f.caption).slice(0, 260))}</figcaption>${bar}</figure>\n\n`;
    })
    .replace(/\[p\.(\d+)\]/g, '<a class="cite" data-page="$1">p.$1</a>');
  let html = DOMPurify.sanitize(marked.parse(s, { gfm: true }), { ADD_ATTR: ["data-fig", "data-act", "data-page"] });
  let n = 0;
  html = html.replace(/<h2>/g, () => `<h2 id="sec-${n++}">`);
  return html.replace(/@@M(\d+)@@/g, (_, i) => {
    const [t, display] = maths[+i];
    try { return katex.renderToString(t, { displayMode: display, throwOnError: false, output: "html" }); }
    catch { return esc(t); }
  });
}

/** 리뷰에서 ## N. 제목 목록 */
export function outline(md) {
  return [...(md || "").matchAll(/^## (.+)$/gm)].map((m, i) => ({ i, title: m[1].trim() }));
}

/** 템플릿만 있고 실제 내용이 없는 리뷰인지 (제목/빈 불릿/placeholder 괄호만) */
export function isEmptyReview(md) {
  return !(md || "").split("\n").some((l) => {
    const t = l.trim();
    if (!t || t.startsWith("#") || t.startsWith(">")) return false;
    if (/^\(.*\)$/.test(t)) return false; // (placeholder)
    if (/^\|[\s|:-]*\|$/.test(t) || /^\|\s*#\s*\|/.test(t)) return false; // 빈 표 머리글
    if (/^[-*]\s*(\[[ x]\]\s*)?([^:：]{0,40}[:：])?\s*$/.test(t)) return false;
    return true;
  });
}
