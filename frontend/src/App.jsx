import React, { useEffect, useState } from "react";
import Library from "./Library.jsx";
import Search from "./Search.jsx";
import Settings from "./Settings.jsx";
import PaperView from "./PaperView.jsx";

// 간단한 해시 라우터: #/ (라이브러리), #/search, #/paper/<arxiv_id>
function useRoute() {
  const [hash, setHash] = useState(location.hash || "#/");
  useEffect(() => {
    const on = () => setHash(location.hash || "#/");
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return hash.replace(/^#/, "");
}

/** 서버가 앱 업데이트 전 코드로 실행 중인지 감지 (git pull 후 재시작을 잊은 경우) */
function useStaleServer() {
  const [stale, setStale] = useState(false);
  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const r = await fetch("/api/status");
        // 이 화면보다 오래된 서버는 /api/status 를 모른다: 404 이거나, 화면(SPA) 폴백이 index.html(JSON 아님)을 돌려준다.
        // code_stale = 서버가 시작된 뒤 디스크의 코드가 바뀜(업데이트 후 재시작 안 함)
        const isJson = (r.headers.get("content-type") || "").includes("json");
        const s = r.status === 404 || (r.ok && !isJson) ? true : r.ok ? (await r.json()).code_stale : false;
        if (alive) setStale(!!s);
      } catch { /* 서버 연결 문제는 여기서 다루지 않는다 */ }
    };
    check();
    const t = setInterval(check, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return stale;
}

export default function App() {
  const route = useRoute();
  const stale = useStaleServer();
  const paperId = route.startsWith("/paper/") ? decodeURIComponent(route.slice(7)) : null;
  return (
    <div className="app">
      {stale && (
        <div className="stale-banner">
          ⚠️ 앱이 업데이트되었는데 서버가 이전 버전으로 실행 중입니다. 일부 기능이 오류를 낼 수 있습니다.
          <b> 실행 창(start.bat)을 닫고 다시 실행해 주세요.</b>
        </div>
      )}
      <header>
        <a className="logo" href="#/">📄 Paper Lab</a>
        <nav>
          <a href="#/" className={route === "/" ? "on" : ""}>라이브러리</a>
          <a href="#/search" className={route === "/search" ? "on" : ""}>arXiv 검색 / 추가</a>
          <a href="#/settings" className={route === "/settings" ? "on" : ""}>설정</a>
        </nav>
      </header>
      <main>
        {paperId ? <PaperView key={paperId} id={paperId} /> : route === "/search" ? <Search /> : route === "/settings" ? <Settings /> : <Library />}
      </main>
    </div>
  );
}
