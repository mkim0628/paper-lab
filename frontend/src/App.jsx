import React, { useEffect, useState } from "react";
import Library from "./Library.jsx";
import Search from "./Search.jsx";
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

export default function App() {
  const route = useRoute();
  const paperId = route.startsWith("/paper/") ? decodeURIComponent(route.slice(7)) : null;
  return (
    <div className="app">
      <header>
        <a className="logo" href="#/">📄 Paper Lab</a>
        <nav>
          <a href="#/" className={route === "/" ? "on" : ""}>라이브러리</a>
          <a href="#/search" className={route === "/search" ? "on" : ""}>arXiv 검색 / 추가</a>
        </nav>
      </header>
      <main>
        {paperId ? <PaperView key={paperId} id={paperId} /> : route === "/search" ? <Search /> : <Library />}
      </main>
    </div>
  );
}
