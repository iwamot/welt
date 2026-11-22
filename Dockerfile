FROM dhi.io/python:3.15.2-debian13-dev@sha256:4dc7ccb8318ef704c868faae29510d6689f1cbaf7857e72064898551107b0188 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:134a1ae38ae5b29f540c6f547816c3c4d6ca921032faf84facb7b9157e88c805 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:0a0ea3f7b9168c63b6ba3e6da5545a089fc768b315dd908360e99d9847a2a6bf AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
