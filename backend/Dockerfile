FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Installation de p7zip (pour compresser/extraire) et curl (pour healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    p7zip-full \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN addgroup --system appgroup && adduser --system --group appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Dossiers pour la compilation temporaire et le stockage final
RUN mkdir -p /app/tmp_builds /app/public_packs /app/previews && \
    chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
