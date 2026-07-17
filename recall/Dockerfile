FROM python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/recall

WORKDIR /opt/recall

COPY server/requirements.txt /opt/recall/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check --no-compile -r requirements.txt \
    && groupadd --gid 10001 recall \
    && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin recall

COPY --chown=10001:10001 server/recall_server /opt/recall/recall_server
COPY --chown=10001:10001 server/schema /opt/recall/schema
COPY --chown=10001:10001 contracts /opt/contracts
COPY --chown=10001:10001 skills/recall/scripts/recall.py /opt/skills/recall/scripts/recall.py

USER 10001:10001
EXPOSE 8788

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import http.client; connection=http.client.HTTPConnection('127.0.0.1',8788,timeout=2); connection.request('GET','/readyz'); response=connection.getresponse(); raise SystemExit(0 if response.status==200 else 1)"]

ENTRYPOINT ["python", "-m", "recall_server.cli"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8788", "--require-auth", "--capability-profile", "production"]
