import os
from pathlib import Path

# 모든 논문 데이터는 이 디렉터리 아래에 파일로 저장된다 (source of truth).
DATA_DIR = Path(os.environ.get("PAPERLAB_DATA", Path(__file__).resolve().parents[2] / "data")).resolve()
PAPERS_DIR = DATA_DIR / "papers"
FRONTEND_DIST = Path(os.environ.get("PAPERLAB_FRONTEND", Path(__file__).resolve().parents[2] / "frontend" / "dist"))
ARXIV_API = os.environ.get("PAPERLAB_ARXIV_API", "https://export.arxiv.org/api/query")
ARXIV_PDF = os.environ.get("PAPERLAB_ARXIV_PDF", "https://arxiv.org/pdf")
# arXiv API 이용 규칙: 3초에 1요청
ARXIV_MIN_INTERVAL = float(os.environ.get("PAPERLAB_ARXIV_INTERVAL", "3.0"))
