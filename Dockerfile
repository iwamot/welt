FROM dhi.io/python:3.15.2-debian13-dev@sha256:bd03dfeeb9fd00ba49e01701fa9014af705c33b1999715ebe3048b8e47c08186 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:9498b4fafdf893784f7345bf79f918a7c909648a22487287932d94a05b18d0eb /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:c8903499b629d8bbeaf46ce4813749f6eaf98dad3974cfeb110a75c97618f126 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
