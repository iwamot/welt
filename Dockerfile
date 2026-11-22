FROM dhi.io/python:3.15.2-debian13-dev@sha256:e5fcc612e4197adacd31f40583f4fb4eba948660479eb9624f84ff20654f0d1d AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:74a9928a02be5d4f0aa53f502444a89597a80490f4a068a53e6044c861707aa3 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:0a0ea3f7b9168c63b6ba3e6da5545a089fc768b315dd908360e99d9847a2a6bf AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
