# python:3.14-slim-trixie
FROM python:3.14-slim-trixie@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# WeasyPrint needs Pango/Cairo (no headless browser). fonts-liberation covers PDF text.
# CVE-2026-111497: curl depends on libbrotli1. trixie main still ships 1.1.0-2+b7.
# Pin the trixie-security fix 1.1.0-2+deb13u1 (DSA-6582-1).
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
        'libbrotli1=1.1.0-2+deb13u1' \
        libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
        libgdk-pixbuf-2.0-0 libffi8 libjpeg62-turbo libopenjp2-7 \
        shared-mime-info fonts-liberation \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
COPY LICENSE NOTICE ./
COPY licenses ./licenses
# Base image pip 26.2.1 vendors certifi 2026.6.17 in pip/_vendor/bom.cdx.json.
# Trivy reads that SBOM and flags GHSA-6q48-6c7w-9gw2. pip 26.2.2 vendors 2026.10.19.
RUN pip install --no-cache-dir --upgrade 'pip==26.2.2' \
    && pip install --no-cache-dir . \
    && useradd -u 10001 -m asm \
    && mkdir -p /data /secrets \
    && chown -R asm:asm /data /secrets
USER asm
ENV AM_DATA_DIR=/data AM_SECRETS_DIR=/secrets AM_HOST=0.0.0.0
EXPOSE 8780 8781
HEALTHCHECK --interval=10s --timeout=5s --retries=30 CMD curl -fsS http://127.0.0.1:8780/healthz || exit 1
CMD ["artifactsmith", "serve"]
