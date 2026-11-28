# Paper Lab

arXiv 논문을 검색/추가하고, 웹에서 리뷰를 작성·저장하며, 이후 재현 실험까지 연결하는 개인용 워크벤치. 설계는 [PLAN.md](PLAN.md) 참고.

현재 구현: **Phase 0–5 + 리뷰 페이지 재설계** (리뷰 중심 로드맵은 PLAN.md 0장)
- arXiv 검색/URL·ID 추가, PDF 뷰어, 리뷰 에디터(자동저장·이력), 로컬 파일 저장, 상태/태그/평점
- PDF 본문 추출(페이지 태그) + 라이브러리 전문 검색
- LLM 연결 프로필(사내/사외 전환, 키는 서버에만 저장) + 근거 인용(`[p.N]`) 리뷰 초안 / 섹션 재생성

- **LLM 키 없이 시작**: alphaXiv가 이미 만들어 둔 AI 개요를 가져와(요청 1건, 로컬 저장, 출처·미검증 표시) 리뷰의 시작점으로 쓸 수 있음 (비공식 연동, 널리 읽힌 논문에서만 가능)
- **리뷰는 읽는 문서**: 목차·수식(KaTeX)·`[p.N]` 근거 뱃지·본문 속 그림, 오른쪽 패널에 **💬 논문 Q&A 채팅**(스트리밍, 논문 전문 근거·인용, 본문 드래그→질문)/원문/관련 연구/코드/메모. 목표 품질 예시: `docs/examples/transformer-review.md`
- 그림/표 추출: 캡션 앵커 방식(벡터 그림 포함), 갤러리·리뷰 임베드(`![[fig:id]]`)·확대·본문 언급 링크, 드래그로 영역 편집/수동 추가 (`scripts/eval_figures.py`로 평가)
- 관련 연구 지도: 참고문헌(Semantic Scholar: 영향력·인용 맥락)·후속 연구(OpenAlex 인용 상위)·유사 논문, 규칙 기반 티어(기반 필독/방법 계보/베이스라인/저명 배경/배경)와 공개된 점수 구성요소, LLM 읽기 가이드(논문은 id 로만 참조해 환각 방지)
- 공개 코드 탐색: 논문 본문 링크 / Hugging Face Papers / GitHub 검색 → 신뢰도 분류(official·likely-official·community·weak) + 근거 표시, 사용자가 repo 선택 또는 '직접 구현' 지정

테스트 방법: [TESTING.md](TESTING.md) (`./scripts/smoke.sh`)

## 빠른 시작

**Windows** — 저장소를 받아(ZIP 또는 `git clone`) 폴더 안의 **`start.bat`을 더블클릭**하세요.
- Python이 없으면 `winget`으로 자동 설치합니다 (winget이 없으면 설치 페이지를 열어 줍니다).
- 가상환경 생성, 패키지 설치, 서버 실행, 브라우저 열기까지 자동입니다. **Node.js는 필요 없습니다** (빌드된 화면이 저장소에 포함되어 있음).
- 처음 실행하면 `paperlab.env`가 만들어집니다. 관련 연구/코드 탐색용 API 키 등을 넣고 싶을 때만 메모장으로 편집하세요 (모두 선택).
- 최신 버전으로 올리려면 `update.bat` (git으로 받은 경우).

**Linux / macOS** — `./start.sh` (Python 3.10+ 필요, 나머지는 자동).

종료는 실행 창에서 Ctrl+C. 데이터는 `data/` 폴더에 저장되며 업데이트해도 유지됩니다. Claude API 키는 앱의 **설정 탭**에서 등록합니다.

## 실행 (수동 / 개발)

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
