FROM dhi.io/python:3.14.8-debian13-dev@sha256:0fc1a06a500fd1a1a884d7bb53c8da242527878e39eefcf106ed8050e94693ad AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.5-debian13-dev@sha256:6138cd23cd05380aec5c8d0347ae526c2a005b43824edd0a5ac5af36a952d1df /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.14.8-debian13@sha256:02aeafdfeb18e859d8ccb221c2ca8e23c54632fd79ce841359f9f06b646877b3 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
