FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LM_BENCHLAB_HOST=0.0.0.0 LM_BENCHLAB_PORT=8768 LM_BENCHLAB_NO_BROWSER=1 LM_BENCHLAB_DEFAULT_ENGINE_HOST=host.docker.internal
WORKDIR /app
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --system --uid 10001 --home-dir /app lab \
    && mkdir -p /data && ln -s /data /app/data \
    && chown -R lab:lab /data /app
COPY --chown=lab:lab app.py config.py complex_cases.py prompt_catalog.py index.html /app/
USER lab
EXPOSE 8768
VOLUME /data
CMD ["python", "-u", "/app/app.py"]
