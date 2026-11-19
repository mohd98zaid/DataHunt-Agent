FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin datahunt \
    && mkdir -p /app/data /app/exports \
    && chown -R datahunt:datahunt /app

COPY --chown=datahunt:datahunt . .

USER datahunt

EXPOSE 8000

CMD ["uvicorn", "datahunt.api:app", "--host", "0.0.0.0", "--port", "8000"]
