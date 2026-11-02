FROM dhi.io/python:3.15.2-debian13-dev@sha256:74f0261e2bfead93cb7b7c553b198ce2a7b8ddeae8e35264043e1864379976ac AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.6-debian13-dev@sha256:5bf4cb017668dc327dd04a802988b22671e9fce1088e255453ce72679293144f /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:0f22f5388fbb8e2b6457c4f0b9964d99000a288bae0f845e0f5989ed7033c7bf AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
