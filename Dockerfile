FROM dhi.io/python:3.15.2-debian13-dev@sha256:98add6f52fc7dca59a55fc0618b6d40266922b0b8482f017c082a3dbe4ab89ab AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.8-debian13-dev@sha256:4cdf9164c4def8ae1686b79a0648e9bc8096de0eb5b5941e38a103fd00211484 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:5f3efbd860ff18cf93c93e5812e300a17e6e06d35be4f973581f3129addeccac AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
