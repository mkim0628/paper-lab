import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, STATUSES } from "./api.js";
import { renderMd, outline, isEmptyReview, slug } from "./markdown.js";
import ChatPanel from "./ChatPanel.jsx";
import CodePanel from "./CodePanel.jsx";
import RelatedPanel from "./RelatedPanel.jsx";
import AlphaxivPanel from "./AlphaxivPanel.jsx";
import FigurePicker, { BBoxEditor, FigureLightbox } from "./FiguresPanel.jsx";

const SIDE = [["chat", "💬 질문"], ["pdf", "📄 원문"], ["alphaxiv", "🅰 alphaXiv"], ["related", "🔗 관련 연구"], ["code", "💻 코드"], ["notes", "📝 메모"]];

export default function PaperView({ id }) {
  const [meta, setMeta] = useState(null);
  const [text, setText] = useState({ review: "", notes: "" });
  const [mode, setMode] = useState("read"); // read | edit
  const [side, setSide] = useState("chat");
  const [pdfPage, setPdfPage] = useState(1);
  const [save, setSave] = useState("saved");
  const [hist, setHist] = useState(null);
  const [err, setErr] = useState("");
  const [gen, setGen] = useState(false);
  const [sec, setSec] = useState(1);
  const [figData, setFigData] = useState(null);
  const [lightbox, setLightbox] = useState(null);
  const [editFig, setEditFig] = useState(null);
  const [picker, setPicker] = useState(false);
  const [sel, setSel] = useState(null);
  const [chatPrefill, setChatPrefill] = useState("");
  const [axAuto, setAxAuto] = useState(false);
  const dirty = useRef({});
  const latest = useRef(text);
  latest.current = text;
  const cursor = useRef(null);
  const docRef = useRef(null);
  const scrollTo = useRef(null);

  useEffect(() => {
    Promise.all([api.get(id), api.review(id)])
      .then(([m, r]) => { setMeta(m); setText({ review: r.content, notes: r.notes }); })
      .catch((e) => setErr(e.message));
  }, [id]);

  const loadFigs = useCallback(() => api.figures(id).then(setFigData).catch(() => setFigData({ items: [], page_sizes: [], stats: {} })), [id]);
  useEffect(() => { loadFigs(); }, [loadFigs, meta?.figures_status]);

  // 백그라운드 작업(PDF/본문/그림/코드/관련 연구)이 끝날 때까지 폴링
  const busyBg = meta && (meta.pdf_status === "pending" || (meta.pdf_status === "ready" && !meta.fulltext_status) || meta.code_status === "searching" || meta.figures_status === "extracting" || meta.related_status === "fetching");
  useEffect(() => {
    if (!busyBg) return;
    const t = setInterval(() => api.get(id).then((m) => setMeta((cur) => ({ ...cur, ...m }))), 2000);
    return () => clearInterval(t);
  }, [busyBg, id]);

  // ---- 저장 (자동저장 1.5초, 이탈 경고) ----
  const flush = useCallback(async () => {
    const keys = Object.keys(dirty.current).filter((k) => dirty.current[k]);
    if (!keys.length) return;
    setSave("saving");
    try {
      for (const k of keys) { dirty.current[k] = false; await (k === "review" ? api.saveReview : api.saveNotes)(id, latest.current[k]); }
      setSave(Object.values(dirty.current).some(Boolean) ? "dirty" : "saved");
    } catch (e) { setSave("error"); setErr(e.message); }
  }, [id]);
  useEffect(() => { if (save !== "dirty") return; const t = setTimeout(flush, 1500); return () => clearTimeout(t); }, [text, save, flush]);
  useEffect(() => {
    const on = (e) => { if (Object.values(dirty.current).some(Boolean)) e.preventDefault(); };
    addEventListener("beforeunload", on);
    return () => removeEventListener("beforeunload", on);
  }, []);
  const edit = (key, v) => { setText((t) => ({ ...t, [key]: v })); dirty.current[key] = true; setSave("dirty"); };

  // ---- 본문 선택 → "이 부분 질문하기" ----
  useEffect(() => {
    const down = (e) => { if (!e.target.closest?.(".ask-sel")) setSel(null); };
    document.addEventListener("mousedown", down);
    return () => document.removeEventListener("mousedown", down);
  }, []);
  const onMouseUp = () => {
    const s = window.getSelection(); const t = s?.toString().trim();
    if (!t || t.length < 4 || !docRef.current?.contains(s.anchorNode)) return;
    const r = s.getRangeAt(0).getBoundingClientRect();
    setSel({ text: t.replace(/\s+/g, " ").slice(0, 600), x: Math.min(innerWidth - 190, Math.max(8, r.left + r.width / 2 - 85)), y: Math.max(8, r.top - 44) });
  };
  const askAbout = () => {
    setChatPrefill(`> ${sel.text}\n\n이 부분이 무슨 뜻인지 쉽게 설명해줘.`);
    setSide("chat"); setSel(null); window.getSelection()?.removeAllRanges();
  };

  // ---- 그림 ----
  const figs = figData?.items || [];
  const insertFigure = (it) => {
    const md = `\n\n![[fig:${it.id}]]\n\n`;
    setText((t) => { const pos = mode === "edit" && cursor.current != null ? cursor.current : t.review.length; return { ...t, review: t.review.slice(0, pos) + md + t.review.slice(pos) }; });
    cursor.current = null; dirty.current.review = true; setSave("dirty"); scrollTo.current = it.id;
  };
  const removeFigure = (fid) => edit("review", text.review.replace(new RegExp(`\\n*!\\[\\[fig:${fid}\\]\\]\\n*`, "g"), "\n\n"));
  const saveRegion = async (v) => {
    try { await api.figPatch(id, editFig.id, { bbox: v.bbox }); await loadFigs(); } catch (e) { setErr(e.message); }
    setEditFig(null);
  };
  useEffect(() => {   // 삽입 직후 해당 그림으로 스크롤
    if (!scrollTo.current) return;
    const el = docRef.current?.querySelector(`[data-fig="${scrollTo.current}"]`);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); scrollTo.current = null; }
  });

  // ---- 문서 클릭: 인용, 그림 도구, 목차 앵커 ----
  const onDocClick = (e) => {
    const p = e.target.dataset?.page;
    if (p) { e.preventDefault(); setPdfPage(+p); setSide("pdf"); return; }
    const btn = e.target.closest?.("[data-act]");
    if (btn) {
      const fid = btn.dataset.fig;
      if (btn.dataset.act === "zoom") setLightbox(fid);
      if (btn.dataset.act === "edit") setEditFig(figs.find((f) => f.id === fid) || null);
      if (btn.dataset.act === "remove" && confirm("이 그림을 본문에서 제거할까요? (그림 자체는 삭제되지 않습니다)")) removeFigure(fid);
      return;
    }
    const f = e.target.closest?.("figure[data-fig]");
    if (f) { setLightbox(f.dataset.fig); return; }
    const a = e.target.closest?.("a[href^='#']");
    if (a && !a.getAttribute("href").startsWith("#/")) {
      e.preventDefault();
      const want = decodeURIComponent(a.getAttribute("href").slice(1));
      const i = outline(text.review).findIndex((h) => slug(h.title) === want);
      if (i >= 0) goSection(i);
    }
  };
  const goSection = (i) => docRef.current?.querySelector(`#sec-${i}`)?.scrollIntoView({ behavior: "smooth", block: "start" });

  // ---- 메타 / AI ----
  const patch = async (body) => { try { setMeta(await api.patch(id, body)); } catch (e) { setErr(e.message); } };
  const remove = async () => { if (confirm("이 논문과 리뷰/메모/대화를 모두 삭제할까요?")) { await api.remove(id); location.hash = "#/"; } };
  const generate = async (section) => {
    const what = section ? `${section}번 섹션을` : "리뷰 전체를";
    if (text.review.trim() && !isEmptyReview(text.review) && !confirm(`LLM으로 ${what} 생성합니다. 현재 내용은 이력에 보관되며 생성 결과로 대체됩니다. 계속할까요?`)) return;
    setGen(true); setErr("");
    try {
      await flush();
      const r = await api.generate(id, section);
      setMeta(r.meta); setMode("read"); edit("review", r.content);
    } catch (e) { setErr(e.message); }
    setGen(false);
  };
  const adoptAlphaxiv = async (d) => {
    if (text.review.trim() && !isEmptyReview(text.review) && !confirm("현재 리뷰를 alphaXiv 개요로 대체합니다 (현재 내용은 이력에 보관됩니다). 계속할까요?")) return;
    await flush();
    const head = `# ${meta.title} — 리뷰 (alphaXiv 개요에서 시작)\n\n> ⚠️ 아래는 alphaXiv의 AI 개요(${d.source_url}, ${d.fetched_at?.slice(0, 10)} 가져옴)를 그대로 옮긴 **외부 AI 생성 요약**이며 검증되지 않았습니다. 수치와 인용 [p.N]을 원문과 대조해 고쳐 쓰세요.\n\n`;
    edit("review", head + d.markdown);
    await patch({ ai_draft: true });
    setMode("read");
  };
  const openAlphaxiv = () => { setAxAuto(true); setSide("alphaxiv"); };
  const startBlank = async () => { const r = await api.skeleton(id); edit("review", r.content); setMode("edit"); };
  const openHistory = async () => setHist(await api.history(id));
  const restore = async (stamp) => {
    const { content } = await api.historyItem(id, stamp);
    if (confirm("현재 리뷰를 이 버전으로 되돌립니다 (현재 내용은 이력에 보관). 계속할까요?")) { edit("review", content); setHist(null); }
  };

  const html = useMemo(() => renderMd(text.review, { id, figs, tools: true }), [text.review, id, figData]);
  const toc = useMemo(() => outline(text.review), [text.review]);
  const sections = useMemo(() => [...text.review.matchAll(/^## (\d+)\.\s*(.*)$/gm)].map((m) => ({ n: +m[1], t: m[2] })), [text.review]);

  if (err && !meta) return <p className="error">{err}</p>;
  if (!meta) return <p>불러오는 중…</p>;
  const empty = isEmptyReview(text.review);
  const ftReady = meta.fulltext_status === "ready";
  const saveLabel = { saved: "저장됨", dirty: "수정됨…", saving: "저장 중…", error: "저장 실패" }[save];

  return (
    <div className="paper2">
      <div className="p2-head">
        <h1>{meta.title}</h1>
        <div className="muted">{meta.authors.join(", ")} · <a href={meta.abs_url} target="_blank" rel="noreferrer">arXiv:{meta.arxiv_id}</a> · {meta.categories.join(", ")}</div>
        <div className="meta-row">
          <select value={meta.status} onChange={(e) => patch({ status: e.target.value })}>{Object.entries(STATUSES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <label>평점 <select value={meta.rating ?? ""} onChange={(e) => e.target.value && patch({ rating: +e.target.value })}><option value="">-</option>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></label>
          <input className="tags" placeholder="태그 (쉼표 구분, Enter)" defaultValue={(meta.tags || []).join(", ")} onKeyDown={(e) => e.key === "Enter" && patch({ tags: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
          <details className="abs"><summary>초록</summary><p className="abstract">{meta.abstract}</p></details>
          <button className="danger" onClick={remove}>삭제</button>
        </div>
      </div>

      <div className="p2-grid">
        {/* ============ 리뷰 문서 ============ */}
        <section className="doc">
          <div className="doc-toolbar">
            <div className="tabs">
              <button className={mode === "read" ? "on" : ""} onClick={() => setMode("read")}>읽기</button>
              <button className={mode === "edit" ? "on" : ""} onClick={() => { setMode("edit"); if (empty && !text.review.trim()) startBlank(); }}>편집</button>
            </div>
            <button disabled={gen || !ftReady} onClick={() => generate()} title={ftReady ? "" : "본문 추출이 끝나야 사용할 수 있습니다"}>{gen ? "생성 중… (1~2분)" : text.review.trim() && !empty ? "🤖 AI로 다시 쓰기" : "🤖 AI로 리뷰 작성"}</button>
            {sections.length > 0 && !empty && (
              <span className="tabs">
                <select value={sec} onChange={(e) => setSec(+e.target.value)}>{sections.map((s) => <option key={s.n} value={s.n}>{s.n}. {s.t.slice(0, 16)}</option>)}</select>
                <button disabled={gen || !ftReady} onClick={() => generate(sec)}>섹션 재생성</button>
              </span>
            )}
            <button disabled={!figs.some((f) => f.bbox && !f.hidden) && !figData?.page_sizes?.length} onClick={() => setPicker(true)}>🖼 그림 삽입</button>
            <button onClick={openHistory}>이력</button>
            <span className={`save s-${save}`}>{saveLabel}</span>
          </div>

          <div className="doc-scroll" ref={docRef} onMouseUp={onMouseUp}>
            {err && <p className="error">{err}</p>}
            {meta.ai_draft && (
              <div className="banner block">⚠️ AI가 작성한 초안이 포함되어 있습니다. 수치와 인용([p.N])을 원문과 대조한 뒤 검증 완료로 표시하세요.
                <button onClick={() => patch({ ai_draft: false })}>검증 완료로 표시</button></div>
            )}

            {mode === "edit" ? (
              <textarea className="editor-ta" spellCheck={false} value={text.review} onChange={(e) => edit("review", e.target.value)}
                onBlur={(e) => { cursor.current = e.target.selectionStart; flush(); }} onSelect={(e) => { cursor.current = e.target.selectionStart; }}
                placeholder="마크다운으로 작성합니다. 수식은 $...$ / $$...$$, 그림은 ![[fig:figure-1]], 근거는 [p.4]" />
            ) : empty ? (
              <div className="empty-state">
                <h2>아직 리뷰가 없습니다</h2>
                <p>논문의 핵심 기술을 분석하고 정리하는 문서입니다. 시작하는 방법은 두 가지입니다.</p>
                <div className="row center">
                  <button className="primary" disabled={gen || !ftReady} onClick={() => generate()}>{gen ? "생성 중…" : "🤖 AI로 리뷰 작성"}</button>
                  <button disabled={gen} onClick={startBlank}>✍ 빈 구조로 직접 작성</button>
                </div>
                <div className="row center"><button onClick={openAlphaxiv} title="LLM API 키가 필요 없습니다">📚 키 없이 시작: alphaXiv 개요 가져오기</button></div>
                {!ftReady && <p className="muted">본문 추출 중입니다… 끝나면 AI 작성을 쓸 수 있습니다.</p>}
                <ul className="muted left">
                  <li>AI 작성은 설정의 <b>LLM 프로필</b>(API 키)이 필요하며, 모든 주장에 근거 페이지 [p.N]와 관련 그림이 함께 들어갑니다.</li>
                  <li>API 키가 없으면 <b>alphaXiv 개요</b>(이미 생성된 외부 AI 요약)를 가져와 시작할 수 있습니다. 널리 읽힌 논문에서만 가능합니다.</li>
                  <li>이해가 안 되는 부분은 오른쪽 <b>💬 질문</b>에서 먼저 물어봐도 됩니다.</li>
                </ul>
              </div>
            ) : (
              <>
                {toc.length >= 3 && (
                  <nav className="toc">{toc.map((h) => <button key={h.i} onClick={() => goSection(h.i)}>{h.title}</button>)}</nav>
                )}
                <article className="review-doc" onClick={onDocClick} dangerouslySetInnerHTML={{ __html: html }} />
              </>
            )}
          </div>
        </section>

        {/* ============ 사이드 패널 ============ */}
        <aside className="side">
          <div className="side-tabs">
            {SIDE.map(([k, v]) => (
              <button key={k} className={side === k ? "on" : ""} onClick={() => setSide(k)}>
                {v}{k === "related" && meta.related_count != null ? ` ${meta.related_count}` : ""}{k === "code" && meta.code?.selected ? " ✓" : ""}
              </button>
            ))}
          </div>
          <div className="side-body">
            {side === "chat" && (ftReady
              ? <ChatPanel id={id} figs={figs} prefill={chatPrefill} onPrefillUsed={() => setChatPrefill("")} onCite={(p) => { setPdfPage(p); setSide("pdf"); }} onOpenFig={setLightbox} />
              : <p className="pad muted">본문 추출이 끝나면 질문할 수 있습니다…</p>)}
            {side === "pdf" && (
              meta.pdf_status === "ready" ? <iframe title="pdf" key={pdfPage} src={`${api.pdfUrl(meta.arxiv_id)}#page=${pdfPage}`} />
                : meta.pdf_status === "pending" ? <p className="pad muted">PDF 다운로드 중…</p>
                  : <p className="pad error">PDF 다운로드 실패. <button onClick={() => api.retryPdf(id).then(() => patch({}))}>재시도</button></p>
            )}
            {side === "alphaxiv" && <AlphaxivPanel id={id} auto={axAuto} onAutoUsed={() => setAxAuto(false)} onCite={(p) => { setPdfPage(p); setSide("pdf"); }} onAdopt={adoptAlphaxiv} />}
            {side === "related" && <RelatedPanel id={id} meta={meta} onMeta={setMeta} />}
            {side === "code" && <CodePanel id={id} meta={meta} onMeta={setMeta} />}
            {side === "notes" && <textarea className="notes-ta" spellCheck={false} value={text.notes} placeholder="자유 메모 (마크다운)" onChange={(e) => edit("notes", e.target.value)} onBlur={flush} />}
          </div>
        </aside>
      </div>

      {sel && <button className="ask-sel" style={{ left: sel.x, top: sel.y }} onClick={askAbout}>💬 이 부분 질문하기</button>}
      {picker && <FigurePicker id={id} meta={meta} data={figData} reload={async () => { await loadFigs(); setMeta(await api.get(id)); }} onInsert={insertFigure} onClose={() => setPicker(false)} />}
      {editFig && <BBoxEditor id={id} pageSizes={figData.page_sizes} init={{ item: editFig, page: editFig.page, bbox: editFig.bbox, label: editFig.label, kind: editFig.kind }} isNew={false} onSave={saveRegion} onCancel={() => setEditFig(null)} />}
      {lightbox && figs.find((i) => i.id === lightbox) && (
        <FigureLightbox id={id} item={figs.find((i) => i.id === lightbox)} onClose={() => setLightbox(null)} onGoto={(p) => { setPdfPage(p); setSide("pdf"); }} onInsert={insertFigure} />
      )}
      {hist && (
        <div className="modal" onClick={() => setHist(null)}>
          <div onClick={(e) => e.stopPropagation()}>
            <h3>리뷰 이력</h3>
            {hist.length === 0 ? <p className="muted">아직 이력이 없습니다.</p> : <ul>{hist.map((s) => <li key={s}>{s.replace("T", " ")} UTC <button onClick={() => restore(s)}>이 버전으로 복원</button></li>)}</ul>}
            <button onClick={() => setHist(null)}>닫기</button>
          </div>
        </div>
      )}
    </div>
  );
}
