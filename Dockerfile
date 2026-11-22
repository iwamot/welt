FROM dhi.io/python:3.15.2-debian13-dev@sha256:62b23e314538117109e233d95cd00fce62112fb53ba740524cbabe233cdd489c AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:134a1ae38ae5b29f540c6f547816c3c4d6ca921032faf84facb7b9157e88c805 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:81cd77fe80b9027b62631ee13e66a98de96655853d1411f7c9bb964af147f856 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
