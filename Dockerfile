# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="ChargeOpt OS"
LABEL org.opencontainers.image.version="0.2.0"
LABEL org.opencontainers.image.description="Enterprise charging energy dispatch and VPP optimisation platform"

# Non-root user
RUN addgroup --system chargeopt && adduser --system --ingroup chargeopt chargeopt

WORKDIR /app

COPY --from=builder /install /usr/local
COPY chargeopt/ ./chargeopt/
COPY migrations/ ./migrations/
COPY scripts/ ./scripts/
COPY static/ ./static/
COPY pyproject.toml ./pyproject.toml

RUN chown -R chargeopt:chargeopt /app

USER chargeopt

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000 \
    WORKERS=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c \
        "import urllib.request, os; urllib.request.urlopen('http://localhost:' + os.environ.get('PORT','8000') + '/health')" \
    || exit 1

# Use a shell entrypoint script so migrate runs before uvicorn,
# but uvicorn is exec'd directly as PID 1 to receive SIGTERM correctly.
ENTRYPOINT ["sh", "-c", "python scripts/migrate.py && exec uvicorn chargeopt.app:app --host 0.0.0.0 --port \"$PORT\" --workers \"$WORKERS\" --no-access-log"]
