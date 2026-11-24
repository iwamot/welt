FROM dhi.io/python:3.15.2-debian13-dev@sha256:6ae754ee360f846c5def592cbb43946d6e0c4cd3fdb7ced0065aa89ed3d76ac3 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:e1731edb249509b4aac438d96c32084969c1164ad3c95643861fd233678e621c /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:a85031f7d1403e0d66b427651a528d2d338438cd9c5017b17481df50978eda53 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
