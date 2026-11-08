FROM dhi.io/python:3.15.2-debian13-dev@sha256:bf6e3ca018ffb2e5b1450f9a918965cc1bd239a30d4791d84d974fd4297d431d AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:648f1c528110207dc0f2c95cd5f74023e9730c50fdf5f22b53deb4a3a34a7b41 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:d90203ad5dd6c0d9fe58efd6347795375b3f5f2430be2bf61573510452f718e5 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
