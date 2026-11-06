FROM dhi.io/python:3.15.2-debian13-dev@sha256:e7847311c2459608f506583328297bd96223b24489d35c39a38283f59643c1e0 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.7-debian13-dev@sha256:25fb9e920eeff3140c667d0fcc92c28b6d22e57772a7a28af9b8827734edaded /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:3667572184e60a6124183c2e476b48acb2569eea695eb9dd678458cc3b1e78f5 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
