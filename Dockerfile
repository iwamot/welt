FROM dhi.io/python:3.14.6-debian13-dev@sha256:a0f83babf95ae5c0936254c6dfd55c621a6a46f5a23bb85c7db2973c385b51b6 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.11.26-debian13-dev@sha256:ba767fb3c0c7477d321b039cab9ca8d8d22a25c618424721293aa72c6296135f /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.6-debian13@sha256:c27c6f9902521b7ab0ce8e453fef348e82dd4203da9816e8f3c582125baadc1c AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
