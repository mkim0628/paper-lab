# Paper Lab

arXiv 논문을 검색/추가하고, 웹에서 리뷰를 작성·저장하며, 이후 재현 실험까지 연결하는 개인용 워크벤치. 설계는 [PLAN.md](PLAN.md) 참고.

현재 구현: **Phase 0–1** (arXiv 검색/URL·ID 추가, PDF 뷰어, 리뷰 에디터, 로컬 파일 저장, 상태/태그/평점, 리뷰 이력)

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

환경변수: `PAPERLAB_DATA`(데이터 경로), `PAPERLAB_ARXIV_INTERVAL`(arXiv 요청 간격, 기본 3초).
