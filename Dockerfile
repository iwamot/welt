FROM dhi.io/python:3.15.2-debian13-dev@sha256:fe4ba32f4d71f62df9abc9b67fc58cc58f9b75c594200146f01ed682ea35bad3 AS builder
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
