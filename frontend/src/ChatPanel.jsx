import React, { useEffect, useRef, useState } from "react";
import { api } from "./api.js";
import { renderMd } from "./markdown.js";

const STARTERS = [
  "이 논문의 핵심 아이디어를 비유로 쉽게 설명해줘",
  "방법을 단계별로 설명해줘 (입력부터 출력까지)",
  "왜 이런 설계를 선택했어? 대안과 비교해서 알려줘",
  "이 논문에서 가장 약한 주장이나 한계는 뭐야?",
];

export default function ChatPanel({ id, figs, prefill, onPrefillUsed, onCite, onOpenFig, hasProfileHint }) {
  const [msgs, setMsgs] = useState(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const abort = useRef(null);
  const end = useRef(null);
  const ta = useRef(null);

  useEffect(() => { api.chat(id).then((r) => setMsgs(r.messages)).catch((e) => setErr(e.message)); }, [id]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs, busy]);
  useEffect(() => { if (prefill) { setInput(prefill); onPrefillUsed?.(); setTimeout(() => ta.current?.focus(), 50); } }, [prefill]);

  const send = async (text) => {
    const q = (text ?? input).trim();
    if (!q || busy) return;
    setInput(""); setErr(""); setBusy(true);
    setMsgs((m) => [...m, { role: "user", content: q }, { role: "assistant", content: "", streaming: true }]);
    const ctl = new AbortController(); abort.current = ctl;
    try {
      const r = await fetch(`/api/papers/${id}/chat`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message: q }), signal: ctl.signal });
      if (!r.ok) { let d = r.statusText; try { d = (await r.json()).detail || d; } catch {} throw new Error(d); }
      const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) {
        const { value, done } = await reader.read(); if (done) break;
        buf += dec.decode(value, { stream: true });
        const parts = buf.split("\n\n"); buf = parts.pop();
        for (const p of parts) {
          if (!p.startsWith("data: ")) continue;
          const ev = JSON.parse(p.slice(6));
          if (ev.delta) setMsgs((m) => m.map((x, i) => (i === m.length - 1 ? { ...x, content: x.content + ev.delta } : x)));
          if (ev.error) setMsgs((m) => m.map((x, i) => (i === m.length - 1 ? { ...x, error: ev.error } : x)));
        }
      }
    } catch (e) {
      if (e.name !== "AbortError") {
        setErr(e.message);
        setMsgs((m) => (m.length && m[m.length - 1].streaming && !m[m.length - 1].content ? m.slice(0, -2) : m)); // 응답이 시작도 못 했으면 이번 질문을 되돌림
        setInput(q);
      }
    } finally {
      setMsgs((m) => m.map((x) => ({ ...x, streaming: false })));
      setBusy(false); abort.current = null;
    }
  };
  const clear = async () => { if (confirm("이 논문의 대화를 모두 지울까요?")) { await api.chatClear(id); setMsgs([]); } };
  const onClick = (e) => {
    const p = e.target.dataset?.page; if (p) { e.preventDefault(); onCite(+p); return; }
    const f = e.target.closest?.("[data-fig]"); if (f) onOpenFig(f.dataset.fig);
  };

  if (!msgs) return <p className="pad muted">{err || "불러오는 중…"}</p>;
  return (
    <div className="chat">
      <div className="chat-log" onClick={onClick}>
        {msgs.length === 0 && (
          <div className="chat-empty">
            <h3>이 논문에 대해 무엇이든 물어보세요</h3>
            <p className="muted">논문 전문을 근거로 답하고 <b>[p.N]</b>으로 출처 페이지를 표시합니다. 이해가 안 되면 "더 쉽게", "예를 들어줘"라고 이어서 물어보세요. 리뷰에서 문장을 드래그하면 바로 질문할 수 있습니다.</p>
            <div className="starters">{STARTERS.map((s) => <button key={s} onClick={() => send(s)}>{s}</button>)}</div>
            {hasProfileHint}
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`bubble ${m.role}`}>
            {m.role === "user"
              ? <div className="user-text">{m.content}</div>
              : <div className="md" dangerouslySetInnerHTML={{ __html: renderMd(m.content || (m.streaming ? "…" : ""), { id, figs }) }} />}
            {m.error && <div className="error small">⚠ {m.error}</div>}
          </div>
        ))}
        <div ref={end} />
      </div>
      {err && <p className="error pad">{err}</p>}
      <div className="chat-input">
        <textarea ref={ta} rows={2} value={input} placeholder="질문을 입력하세요 (Enter 전송, Shift+Enter 줄바꿈)" onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
        <div className="col">
          {busy ? <button onClick={() => abort.current?.abort()}>■ 중지</button> : <button className="on" disabled={!input.trim()} onClick={() => send()}>전송</button>}
          {msgs.length > 0 && !busy && <button className="ghost" onClick={clear} title="대화 지우기">🗑</button>}
        </div>
      </div>
    </div>
  );
}
