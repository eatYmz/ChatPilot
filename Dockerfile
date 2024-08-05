# syntax=docker/dockerfile:1

FROM python:3.11-slim-bookworm as base
WORKDIR /app
COPY . .

# 安装 procps 包含 ps 工具
RUN apt-get update && apt-get install -y procps && rm -rf /var/lib/apt/lists/*

ENV ENV=prod
ENV PORT "8181"

ENV OLLAMA_BASE_URL ""
ENV OPENAI_API_BASE_URLS ""
ENV OPENAI_API_KEYS ""

ENV WEBUI_SECRET_KEY ""

ENV SCARF_NO_ANALYTICS true
ENV DO_NOT_TRACK true

# RAG Embedding Model Settings
ENV RAG_EMBEDDING_MODEL="shibing624/text2vec-base-multilingual"

# 安装 gunicorn
RUN pip3 install gunicorn
RUN pip3 install torch --index-url https://download.pytorch.org/whl/cpu --no-cache-dir
RUN pip3 install -r requirements.txt --no-cache-dir

CMD ["bash", "start.sh"]
