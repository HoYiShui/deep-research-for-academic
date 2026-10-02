# Backend image (prod). Build context: backend/.
#
# Packages live at the top level (domain/application/infrastructure/interface),
# so WORKDIR /app lets `uvicorn interface.main:app` resolve them — no PYTHONPATH
# hack needed. BGE weights are volume-mounted (see docker-compose.prod.yml).

FROM python:3.12-slim

WORKDIR /app

# Install runtime deps + the project. The `embedding` extra pulls in
# sentence-transformers/torch for BGE-M3 / BGE-reranker (CPU build by default;
# swap the base image to nvidia/cuda for GPU).
COPY . .
RUN pip install --no-cache-dir '.[embedding]'

ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["uvicorn", "interface.main:app", "--host", "0.0.0.0", "--port", "8000"]
