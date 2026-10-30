FROM dhi.io/python:3.15.2-debian13-dev@sha256:f8af7491df42d4d0b0a3d52e69992df7e3837c09483ec49a513a7e4375ce4d32 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.6-debian13-dev@sha256:d6d83d18e96a6ed138a3bfe270d1aba33ad4af5acbfb0f4815a77d92f6477cb6 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:37a0fd449194addf16e7076b68fea74793447c0b1be0844eb57a1734e3f2ab69 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
