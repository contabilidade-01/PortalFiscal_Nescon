# =============================================================================
# Portal Fiscal Nescon — imagem para EasyPanel (mesmo VPS do GClick)
# Dados (banco + XML + certificados) em /app/data → montar UM volume lá.
# Segredos vêm das env vars do EasyPanel, não da imagem.
# libxmlsec1 é necessário para a assinatura da Ciência 210210 (xmlsec).
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=America/Sao_Paulo \
    FISCAL_DATA_DIR=/app/data \
    FISCAL_XML_DIR=/app/data/XML \
    FISCAL_CRON=1 \
    FISCAL_CRON_HORA=06:00 \
    TRUST_PROXY=1

# xmlsec (Ciência 210210) + openssl + tzdata (BRT) + gosu (drop de root no entrypoint)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential pkg-config \
        libxml2-dev libxmlsec1-dev libxmlsec1-openssl \
        openssl tzdata gosu \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 appgroup && useradd --uid 1000 --gid 1000 --create-home appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh \
    && mkdir -p /app/data/XML /app/data/Certificados /app/data/logs \
    && chown -R appuser:appgroup /app

# entrypoint sobe como root só para chown do volume; depois vira appuser
USER root

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)"

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["waitress-serve", "--listen=0.0.0.0:8000", "--threads=8", "app:app"]
