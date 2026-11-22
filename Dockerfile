FROM dhi.io/python:3.15.2-debian13-dev@sha256:e5fcc612e4197adacd31f40583f4fb4eba948660479eb9624f84ff20654f0d1d AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:ae33f5c3fa1cfd43942cae31d8258b6ec616d0340342e7bd0a248588ea76729c /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:d4272cff48250132693476e504bb1225d9e32531cfc030926ad5d29defa95b59 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
