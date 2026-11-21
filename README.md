# Paper Lab

arXiv 논문을 검색/추가하고, 웹에서 리뷰를 작성·저장하며, 이후 재현 실험까지 연결하는 개인용 워크벤치. 설계는 [PLAN.md](PLAN.md) 참고.

현재 구현: **Phase 0–5** (리뷰 중심 로드맵은 PLAN.md 0장)
- arXiv 검색/URL·ID 추가, PDF 뷰어, 리뷰 에디터(자동저장·이력), 로컬 파일 저장, 상태/태그/평점
- PDF 본문 추출(페이지 태그) + 라이브러리 전문 검색
- LLM 연결 프로필(사내/사외 전환, 키는 서버에만 저장) + 근거 인용(`[p.N]`) 리뷰 초안 / 섹션 재생성

- 그림/표 추출: 캡션 앵커 방식(벡터 그림 포함), 갤러리·리뷰 임베드(`![[fig:id]]`)·확대·본문 언급 링크, 드래그로 영역 편집/수동 추가 (`scripts/eval_figures.py`로 평가)
- 관련 연구 지도: 참고문헌(Semantic Scholar: 영향력·인용 맥락)·후속 연구(OpenAlex 인용 상위)·유사 논문, 규칙 기반 티어(기반 필독/방법 계보/베이스라인/저명 배경/배경)와 공개된 점수 구성요소, LLM 읽기 가이드(논문은 id 로만 참조해 환각 방지)
- 공개 코드 탐색: 논문 본문 링크 / Hugging Face Papers / GitHub 검색 → 신뢰도 분류(official·likely-official·community·weak) + 근거 표시, 사용자가 repo 선택 또는 '직접 구현' 지정

테스트 방법: [TESTING.md](TESTING.md) (`./scripts/smoke.sh`)

## 실행

### Docker (서버 배포)
```bash
docker compose up -d --build
# 로컬에서 접속: ssh -L 8000:localhost:8000 <server>  →  http://localhost:8000
```
데이터는 `./data/papers/<arxiv_id>/` 에 파일로 저장됩니다 (meta.json, paper.pdf, review.md, notes.md, review.history/).

### 개발
```bash
cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
uvicorn app.main:app --reload            # :8000
cd frontend && npm install && npm run dev # :5173 (/api 는 :8000 으로 프록시)
cd backend && pytest
```

환경변수: `PAPERLAB_DATA`(데이터 경로), `PAPERLAB_GITHUB_TOKEN`(선택, GitHub rate limit 완화), `PAPERLAB_S2_API_KEY` / `PAPERLAB_OPENALEX_API_KEY`(선택, 관련 연구 조회), `PAPERLAB_ARXIV_INTERVAL`(arXiv 요청 간격, 기본 3초).
