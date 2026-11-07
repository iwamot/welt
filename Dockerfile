FROM dhi.io/python:3.15.2-debian13-dev@sha256:ac84f91c624752629b58f17fd3d5d46a016e39c498a4bbdd86fb20eb9e4e8848 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:25fb9e920eeff3140c667d0fcc92c28b6d22e57772a7a28af9b8827734edaded /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:99033b12411ffc5b82f9c5175c3e3966022e910837bbd88f28a944c18754466c AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
