FROM python:3.12-slim

WORKDIR /app

COPY python/draftsim ./python/draftsim
COPY python/liveboard ./python/liveboard
COPY data ./data

RUN pip install --no-cache-dir -e ./python/draftsim -e ./python/liveboard

CMD ["sh", "-c", "python -m liveboard.live --draft-id \"$DRAFT_ID\" --test-draft-id \"$TEST_DRAFT_ID\" --host 0.0.0.0 --port \"$PORT\" --user \"$LIVEBOARD_USER\""]
