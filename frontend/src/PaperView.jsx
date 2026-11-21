import React, { useCallback, useEffect, useRef, useState } from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { api, STATUSES } from "./api.js";
import CodePanel from "./CodePanel.jsx";

// [p.3] 인용을 클릭 가능한 링크로 (클릭 시 PDF가 해당 페이지로 이동)
const render = (md) =>
  DOMPurify.sanitize(
    marked.parse((md || "").replace(/\[p\.(\d+)\]/g, '<a class="cite" data-page="$1">p.$1</a>'), { gfm: true })
  );

export default function PaperView({ id }) {
  const [meta, setMeta] = useState(null);
  const [text, setText] = useState({ review: "", notes: "" });
  const [tab, setTab] = useState("review");
  const [mode, setMode] = useState("edit"); // edit | preview
  const [save, setSave] = useState("saved"); // saved | dirty | saving | error
  const [hist, setHist] = useState(null);
  const [err, setErr] = useState("");
  const [page, setPage] = useState(1);
  const [gen, setGen] = useState(false);
  const [sec, setSec] = useState(1);
  const dirty = useRef({}); // { review: true, notes: true }
  const latest = useRef(text);
  latest.current = text;

  useEffect(() => {
    Promise.all([api.get(id), api.review(id)])
      .then(([m, r]) => { setMeta(m); setText({ review: r.content, notes: r.notes }); })
      .catch((e) => setErr(e.message));
  }, [id]);

  // PDF 다운로드 / 본문 추출이 끝날 때까지 폴링 (둘 다 백그라운드 작업)
  const busyBg = meta && (meta.pdf_status === "pending" || (meta.pdf_status === "ready" && !meta.fulltext_status) || meta.code_status === "searching");
  useEffect(() => {
    if (!busyBg) return;
    const t = setInterval(() => api.get(id).then((m) => setMeta((cur) => ({ ...cur, ...m }))), 2000);
    return () => clearInterval(t);
  }, [busyBg, id]);

  const flush = useCallback(async () => {
    const keys = Object.keys(dirty.current).filter((k) => dirty.current[k]);
    if (!keys.length) return;
    setSave("saving");
    try {
      for (const k of keys) {
        dirty.current[k] = false;
        await (k === "review" ? api.saveReview : api.saveNotes)(id, latest.current[k]);
      }
      setSave(Object.values(dirty.current).some(Boolean) ? "dirty" : "saved");
    } catch (e) { setSave("error"); setErr(e.message); }
  }, [id]);

  // 입력 후 1.5초 뒤 자동저장, 이탈 경고
  useEffect(() => {
    if (save !== "dirty") return;
    const t = setTimeout(flush, 1500);
    return () => clearTimeout(t);
  }, [text, save, flush]);
  useEffect(() => {
    const on = (e) => { if (Object.values(dirty.current).some(Boolean)) e.preventDefault(); };
    addEventListener("beforeunload", on);
    return () => removeEventListener("beforeunload", on);
  }, []);

  const edit = (v) => {
    setText((t) => ({ ...t, [tab]: v }));
    dirty.current[tab] = true;
    setSave("dirty");
  };

  const generate = async (section) => {
    const what = section ? `${section}번 섹션을` : "리뷰 전체를";
    if (!confirm(`LLM으로 ${what} 생성합니다. 현재 내용은 이력에 보관되며, 생성 결과가 에디터에 반영됩니다. 계속할까요?`)) return;
    setGen(true); setErr("");
    try {
      await flush();
      const r = await api.generate(id, section);
      setMeta(r.meta); setTab("review"); setText((t) => ({ ...t, review: r.content }));
      dirty.current.review = true; setSave("dirty");
    } catch (e) { setErr(e.message); }
    setGen(false);
  };
  const reextract = async () => { try { setMeta(await api.reextract(id)); } catch (e) { setErr(e.message); } };
  const onMdClick = (e) => {
    const p = e.target.dataset?.page;
    if (p) { e.preventDefault(); setPage(+p); }
  };
  const sections = [...(text.review.matchAll(/^## (\d+)\.\s*(.*)$/gm))].map((m) => ({ n: +m[1], t: m[2] }));

  const patch = async (body) => { try { setMeta(await api.patch(id, body)); } catch (e) { setErr(e.message); } };
  const remove = async () => {
    if (!confirm("이 논문과 리뷰/노트를 모두 삭제할까요?")) return;
    await api.remove(id); location.hash = "#/";
  };
  const openHistory = async () => setHist(await api.history(id));
  const restore = async (stamp) => {
    const { content } = await api.historyItem(id, stamp);
    if (confirm("현재 리뷰를 이 버전으로 되돌립니다 (현재 내용은 history에 보관). 계속할까요?")) {
      setTab("review"); setText((t) => ({ ...t, review: content })); dirty.current.review = true; setSave("dirty"); setHist(null);
    }
  };

  if (err && !meta) return <p className="error">{err}</p>;
  if (!meta) return <p>불러오는 중…</p>;

  const saveLabel = { saved: "저장됨", dirty: "수정됨…", saving: "저장 중…", error: "저장 실패" }[save];
  return (
    <div className="paper">
      <div className="paper-head">
        <h2>{meta.title}</h2>
        <div className="muted">
          {meta.authors.join(", ")} · <a href={meta.abs_url} target="_blank" rel="noreferrer">arXiv:{meta.arxiv_id}</a> · {meta.categories.join(", ")}
        </div>
        <div className="meta-row">
          <select value={meta.status} onChange={(e) => patch({ status: e.target.value })}>
            {Object.entries(STATUSES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <label>평점
            <select value={meta.rating ?? ""} onChange={(e) => e.target.value && patch({ rating: +e.target.value })}>
              <option value="">-</option>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}
            </select>
          </label>
          <input className="tags" placeholder="태그 (쉼표 구분, Enter로 저장)" defaultValue={(meta.tags || []).join(", ")}
            onKeyDown={(e) => e.key === "Enter" && patch({ tags: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
          <button className="danger" onClick={remove}>삭제</button>
        </div>
        {meta.ai_draft && (
          <div className="banner">
            ⚠️ AI가 생성한 초안이 포함되어 있습니다. 수치·인용([p.N])을 원문과 대조한 뒤 검증 완료로 표시하세요.
            <button onClick={() => patch({ ai_draft: false })}>검증 완료로 표시</button>
          </div>
        )}
        <details><summary>초록</summary><p className="abstract">{meta.abstract}</p></details>
      </div>

      <div className="split">
        <div className="pane pdf">
          {meta.pdf_status === "ready" && <iframe title="pdf" key={page} src={`${api.pdfUrl(meta.arxiv_id)}#page=${page}`} />}
          {meta.pdf_status === "pending" && <p className="muted pad">PDF 다운로드 중…</p>}
          {meta.pdf_status === "failed" && (
            <p className="pad error">PDF 다운로드 실패. <button onClick={() => api.retryPdf(id).then(() => patch({}))}>재시도</button></p>
          )}
        </div>
        <div className="pane editor">
          <div className="editor-bar">
            <div className="tabs">
              <button className={tab === "review" ? "on" : ""} onClick={() => setTab("review")}>리뷰</button>
              <button className={tab === "notes" ? "on" : ""} onClick={() => setTab("notes")}>메모</button>
              <button className={tab === "code" ? "on" : ""} onClick={() => setTab("code")}>
                코드{meta.code?.selected ? " ✓" : meta.code?.no_official ? " (직접구현)" : meta.code_status === "searching" ? " …" : ""}
              </button>
            </div>
            {tab !== "code" && <>
            <div className="tabs">
              <button className={mode === "edit" ? "on" : ""} onClick={() => setMode("edit")}>편집</button>
              <button className={mode === "preview" ? "on" : ""} onClick={() => setMode("preview")}>미리보기</button>
            </div>
            <button onClick={openHistory}>이력</button>
            <button disabled={gen || meta.fulltext_status !== "ready"} onClick={() => generate()}
              title={meta.fulltext_status !== "ready" ? "본문 추출이 끝나야 사용할 수 있습니다" : ""}>{gen ? "생성 중… (1~2분)" : "🤖 AI 초안"}</button>
            {sections.length > 0 && (
              <span className="tabs">
                <select value={sec} onChange={(e) => setSec(+e.target.value)}>{sections.map((s) => <option key={s.n} value={s.n}>{s.n}. {s.t.slice(0, 14)}</option>)}</select>
                <button disabled={gen || meta.fulltext_status !== "ready"} onClick={() => generate(sec)}>섹션 재생성</button>
              </span>
            )}
            {meta.fulltext_status === "failed" && <button onClick={reextract}>본문 재추출</button>}
            <span className={`save s-${save}`}>{saveLabel}</span>
            </>}
          </div>
          {err && <p className="error pad">{err}</p>}
          {tab === "code" ? <CodePanel id={id} meta={meta} onMeta={setMeta} /> : mode === "edit"
            ? <textarea spellCheck={false} value={text[tab]} onChange={(e) => edit(e.target.value)} onBlur={flush} />
            : <div className="md" onClick={onMdClick} dangerouslySetInnerHTML={{ __html: render(text[tab]) }} />}
        </div>
      </div>

      {hist && (
        <div className="modal" onClick={() => setHist(null)}>
          <div onClick={(e) => e.stopPropagation()}>
            <h3>리뷰 이력</h3>
            {hist.length === 0 ? <p className="muted">아직 이력이 없습니다.</p> : (
              <ul>{hist.map((s) => <li key={s}>{s.replace("T", " ")} UTC <button onClick={() => restore(s)}>이 버전으로 복원</button></li>)}</ul>
            )}
            <button onClick={() => setHist(null)}>닫기</button>
          </div>
        </div>
      )}
    </div>
  );
}
