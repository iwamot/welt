FROM dhi.io/python:3.15.2-debian13-dev@sha256:74f0261e2bfead93cb7b7c553b198ce2a7b8ddeae8e35264043e1864379976ac AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.6-debian13-dev@sha256:7591a975b675f8817d3e1fe54394138ac117272124fb14f3f2bf07df5338cad3 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:8b0f1de0f2a53209a735cab91d52116938aa38858c24537f454f87bbaa718c5a AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
