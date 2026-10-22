FROM dhi.io/python:3.14.8-debian13-dev@sha256:b146d15f69fb9bcd657ca5d336e9a0a79317d51311fe5ec4331b47d350249d31 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.4-debian13-dev@sha256:a5d1bbced387e66cd2f14d8df93cc3a2216c472a848a0380b4b1e9d346566e94 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.8-debian13@sha256:42086668be31e67ea81cb2c350240bda1630b72473a1930cdd3f3f8d3dbd9903 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
