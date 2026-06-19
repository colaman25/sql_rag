FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN apt-get update && apt-get install -y curl

COPY config.yml .
RUN python -c "\
import yaml; \
cfg = yaml.safe_load(open('config.yml')); \
model = cfg.get('embedding', {}).get('model_name', 'Qwen/Qwen3-Embedding-0.6B'); \
print(f'Pre-loading embedding model: {model}'); \
from sentence_transformers import SentenceTransformer; SentenceTransformer(model)"

COPY . .