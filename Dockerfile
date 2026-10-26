FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 APP_HOST=0.0.0.0 APP_PORT=8768 LLM_LAB_DB=/data/llm_concurrency_history.sqlite3 LLM_LAB_NO_BROWSER=1
WORKDIR /app
COPY app.py complex_cases.py index.html /app/
RUN pip install --no-cache-dir cryptography \
    && useradd --system --uid 10001 --home-dir /app lab \
    && mkdir -p /data && chown -R lab:lab /data /app
USER lab
EXPOSE 8768
VOLUME /data
CMD ["python", "-u", "/app/app.py"]
