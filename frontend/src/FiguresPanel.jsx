import React, { useRef, useState } from "react";
import { api } from "./api.js";

const BADGE = (it) => (it.source === "manual" ? "수동" : it.edited ? "수정됨" : "자동");

/** PDF 페이지 이미지 위에서 영역을 드래그로 지정/이동/크기조절. 좌표는 PDF pt 단위, 화면 위치는 % 로 표시. */
export function BBoxEditor({ id, pageSizes, init, isNew, onSave, onCancel }) {
  const [page, setPage] = useState(init.page);
  const [kind, setKind] = useState(init.kind || "figure");
  const [rect, setRect] = useState(init.bbox);
  const [label, setLabel] = useState(init.label || "");
  const [caption, setCaption] = useState(init.caption || "");
  const wrap = useRef(null);
  const drag = useRef(null);
  const [pw, ph] = pageSizes[page - 1] || [612, 792];

  const pt = (e) => {
    const r = wrap.current.getBoundingClientRect();
    return [Math.min(pw, Math.max(0, ((e.clientX - r.left) / r.width) * pw)), Math.min(ph, Math.max(0, ((e.clientY - r.top) / r.height) * ph))];
  };
  const down = (e) => {
    const [x, y] = pt(e);
    const tol = (12 / wrap.current.getBoundingClientRect().width) * pw;
    let mode = null;
    if (rect) {
      const [x0, y0, x1, y1] = rect;
      const near = (a, b) => Math.abs(a - b) <= tol;
      const cx = near(x, x0) ? "x0" : near(x, x1) ? "x1" : null;
      const cy = near(y, y0) ? "y0" : near(y, y1) ? "y1" : null;
      if (cx && cy) mode = { t: "resize", cx, cy };
      else if (x > x0 && x < x1 && y > y0 && y < y1) mode = { t: "move", dx: x - x0, dy: y - y0 };
    }
    if (!mode) { mode = { t: "draw", ax: x, ay: y }; setRect([x, y, x, y]); }
    drag.current = mode;
    wrap.current.setPointerCapture(e.pointerId);
  };
  const move = (e) => {
    const m = drag.current; if (!m) return;
    const [x, y] = pt(e);
    if (m.t === "draw") setRect([m.ax, m.ay, x, y]);
    else if (m.t === "resize") {
      const r = [...rect]; r[m.cx === "x0" ? 0 : 2] = x; r[m.cy === "y0" ? 1 : 3] = y; setRect(r);
    } else {
      const [x0, y0, x1, y1] = rect; const w = x1 - x0, h = y1 - y0;
      const nx = Math.min(pw - w, Math.max(0, x - m.dx)), ny = Math.min(ph - h, Math.max(0, y - m.dy));
      setRect([nx, ny, nx + w, ny + h]);
    }
  };
  const up = () => {
    drag.current = null;
    if (rect) setRect([Math.min(rect[0], rect[2]), Math.min(rect[1], rect[3]), Math.max(rect[0], rect[2]), Math.max(rect[1], rect[3])]);
  };
  const box = rect && { left: `${(rect[0] / pw) * 100}%`, top: `${(rect[1] / ph) * 100}%`, width: `${((rect[2] - rect[0]) / pw) * 100}%`, height: `${((rect[3] - rect[1]) / ph) * 100}%` };
  const valid = rect && rect[2] - rect[0] >= 8 && rect[3] - rect[1] >= 8;

  return (
    <div className="modal" onClick={onCancel}>
      <div className="editor-modal" onClick={(e) => e.stopPropagation()}>
        <div className="row">
          <b>{isNew ? "새 그림/표 영역 지정" : `${init.label} 영역 편집`}</b>
          {isNew && <>
            <label>페이지 <input type="number" min="1" max={pageSizes.length} value={page} style={{ width: 64 }} onChange={(e) => { setPage(Math.min(pageSizes.length, Math.max(1, +e.target.value || 1))); setRect(null); }} /></label>
            <select value={kind} onChange={(e) => setKind(e.target.value)}><option value="figure">그림</option><option value="table">표</option></select>
            <input placeholder="라벨 (예: Figure 3)" value={label} onChange={(e) => setLabel(e.target.value)} />
          </>}
          <span className="muted">{rect ? "드래그: 새 영역 / 안쪽: 이동 / 모서리: 크기 조절" : "페이지 위에서 드래그해 영역을 그리세요"}</span>
        </div>
        {isNew && <input className="wide" placeholder="캡션 (선택)" value={caption} onChange={(e) => setCaption(e.target.value)} />}
        <div className="page-wrap" ref={wrap} onPointerDown={down} onPointerMove={move} onPointerUp={up}>
          <img src={api.pageUrl(id, page)} alt={`page ${page}`} draggable={false} />
          {box && <div className="bbox" style={box}>{["tl", "tr", "bl", "br"].map((c) => <i key={c} className={`h ${c}`} />)}</div>}
        </div>
        <div className="row">
          <button disabled={!valid} onClick={() => onSave({ page, kind, label, caption, bbox: rect.map((v) => Math.round(v * 10) / 10) })}>저장</button>
          <button onClick={onCancel}>취소</button>
        </div>
      </div>
    </div>
  );
}

export function FigureLightbox({ id, item, onClose, onGoto, onInsert }) {
  return (
    <div className="modal" onClick={onClose}>
      <div className="lightbox" onClick={(e) => e.stopPropagation()}>
        <div className="row"><b>{item.label}</b><span className="muted">p.{item.page} · {BADGE(item)}</span>
          <button onClick={() => { onGoto(item.page); onClose(); }}>PDF에서 보기</button>
          {onInsert && <button onClick={() => { onInsert(item); onClose(); }}>리뷰에 삽입</button>}
          <button onClick={onClose}>닫기</button></div>
        {item.file && <img src={api.figUrl(id, item)} alt={item.label} />}
        <p className="abstract">{item.caption || "(캡션 없음)"}</p>
        {item.mentions?.length > 0 && (
          <details open><summary>본문에서 언급된 곳 ({item.mentions.length})</summary>
            <ul className="ev">{item.mentions.map((m, i) => <li key={i}><a href="#/" onClick={(e) => { e.preventDefault(); onGoto(m.page); onClose(); }}>p.{m.page}</a> {m.snippet}</li>)}</ul>
          </details>
        )}
      </div>
    </div>
  );
}

/** 그림/표 선택 모달: 리뷰에 삽입, 영역 수정, 숨김/복원, 수동 추가, 재추출 (별도 '탭'이 아니라 리뷰 작성 중 호출하는 도구) */
export default function FigurePicker({ id, meta, data, reload, onInsert, onClose }) {
  const [filter, setFilter] = useState("all");
  const [showHidden, setShowHidden] = useState(false);
  const [edit, setEdit] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const run = async (fn) => { setBusy(true); setErr(""); try { await fn(); await reload(); } catch (e) { setErr(e.message); } setBusy(false); };
  const extracting = meta.figures_status === "extracting";
  const items = (data?.items || []).filter((i) => (filter === "all" || i.kind === filter) && (showHidden ? i.hidden : !i.hidden));
  const nHidden = (data?.items || []).filter((i) => i.hidden).length;
  const noRegion = (data?.items || []).filter((i) => !i.bbox && !i.hidden).length;
  const save = async (v) => {
    const it = edit.item;
    await run(() => (it ? api.figPatch(id, it.id, { bbox: v.bbox }) : api.figAdd(id, { page: v.page, kind: v.kind, label: v.label || undefined, caption: v.caption, bbox: v.bbox })));
    setEdit(null);
  };

  return (
    <div className="modal" onClick={onClose}>
      <div className="editor-modal picker" onClick={(e) => e.stopPropagation()}>
        <div className="row"><b>🖼 그림·표 삽입</b>
          <span className="muted">논문에서 자동으로 추출한 {data?.items?.length || 0}개 (캡션 {data?.stats?.captions ?? 0}개 중 영역 {data?.stats?.with_region ?? 0}개 인식)</span>
          <button style={{ marginLeft: "auto" }} onClick={onClose}>닫기</button></div>
        <div className="row">
          <select value={filter} onChange={(e) => setFilter(e.target.value)}><option value="all">그림+표</option><option value="figure">그림만</option><option value="table">표만</option></select>
          <button disabled={busy || extracting} onClick={() => run(() => api.figuresExtract(id))}>{extracting ? "추출 중…" : "🔄 다시 추출"}</button>
          <button disabled={!data?.page_sizes?.length} onClick={() => setEdit({ page: 1, bbox: null, kind: "figure" })}>＋ 영역 직접 지정</button>
          {nHidden > 0 && <label className="muted"><input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} /> 숨긴 항목 {nHidden}</label>}
        </div>
        {err && <p className="error">{err}</p>}
        {noRegion > 0 && <div className="banner block">영역을 찾지 못한 항목 {noRegion}개: "영역 지정"으로 직접 잡아 주세요.</div>}
        <div className="figs-grid">
          {items.map((it) => (
            <div key={it.id} className="fig-card">
              <div className="thumb">{it.file ? <img src={api.figUrl(id, it)} alt={it.label} loading="lazy" /> : <div className="noimg">영역 없음</div>}</div>
              <div><b>{it.label}</b> <span className="muted">p.{it.page} · {BADGE(it)}</span></div>
              <div className="muted clamp">{it.caption.replace(/^(Figure|Fig\.|Table)\s*\S+\s*[:.|]?\s*/i, "")}</div>
              <div className="row">
                {it.bbox && !it.hidden && <button onClick={() => { onInsert(it); onClose(); }}>리뷰에 삽입</button>}
                <button onClick={() => setEdit({ item: it, page: it.page, bbox: it.bbox, label: it.label, kind: it.kind })}>{it.bbox ? "영역 편집" : "영역 지정"}</button>
                {it.hidden
                  ? <button onClick={() => run(() => api.figPatch(id, it.id, { hidden: false }))}>복원</button>
                  : <button className="danger" onClick={() => run(() => api.figDelete(id, it.id))}>{it.source === "manual" ? "삭제" : "숨김"}</button>}
              </div>
            </div>
          ))}
        </div>
        {edit && <BBoxEditor id={id} pageSizes={data.page_sizes} init={edit} isNew={!edit.item} onSave={save} onCancel={() => setEdit(null)} />}
      </div>
    </div>
  );
}
