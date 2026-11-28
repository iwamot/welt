FROM dhi.io/python:3.15.2-debian13-dev@sha256:0502331366cd0ef7305342c62adbf1a7c4ece5fc0681afe7f0b8b80e0e65b350 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.10-debian13-dev@sha256:71e58b44486850a01075f4564c45bf3c5f229e7febae71ffd34568e0e1489ee0 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:41eb92496c2685833535984843c609368fa6e2b98dade88056ba425850288ba8 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
