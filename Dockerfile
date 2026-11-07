# ---- build: install dependencies into an isolated venv
FROM python:3.12-slim AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /venv
COPY requirements.txt /tmp/requirements.txt
RUN /venv/bin/pip install -r /tmp/requirements.txt \
 && /venv/bin/pip uninstall -y pip setuptools wheel || true \
 && find /venv -name "__pycache__" -prune -exec rm -rf {} + \
 && find /venv -name "*.pyi" -delete

# ---- runtime
FROM python:3.12-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --user-group --no-create-home --shell /usr/sbin/nologin vantage
COPY --from=build /venv /venv
WORKDIR /app
COPY app ./app
RUN mkdir -p config assets data && chown vantage:vantage config assets data
ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VANTAGE_CONFIG=/app/config/vantage.yml \
    VANTAGE_ASSETS=/app/assets \
    VANTAGE_DATA=/app/data \
    PORT=8080
USER vantage
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request;urllib.request.urlopen('http://localhost:8080/healthz', timeout=4)"]
CMD ["python", "-m", "app"]
