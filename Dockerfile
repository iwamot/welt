FROM dhi.io/python:3.15.2-debian13-dev@sha256:f9121e954baf4ec5f29d85f5025b64b4274054607db9753ac090a42281c66a7d AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:648f1c528110207dc0f2c95cd5f74023e9730c50fdf5f22b53deb4a3a34a7b41 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:99033b12411ffc5b82f9c5175c3e3966022e910837bbd88f28a944c18754466c AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
