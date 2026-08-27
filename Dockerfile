FROM python:3.12-slim

WORKDIR /app

ENV HF_HOME=/app/.cache/huggingface
ENV HF_HUB_DISABLE_PROGRESS_BARS=1
ENV TRANSFORMERS_NO_ADVISORY_WARNINGS=1
ENV TOKENIZERS_PARALLELISM=false

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn api.main:app --host 0.0.0.0 --port ${PORT:-8080}"]

