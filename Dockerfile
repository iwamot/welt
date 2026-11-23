FROM dhi.io/python:3.15.2-debian13-dev@sha256:d0ea99ed5f0930ad224af90c68e200f3feeca815ea1fdac2c34d37a3b6ba7a72 AS builder
WORKDIR /build/
COPY --from=dhi.io/uv:0.13.9-debian13-dev@sha256:545b93d8f69b358d4c72f2dd9d01e0c0c5992e4f70e474752aa2da9749fcfc36 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock /build/
RUN uv sync --frozen --no-dev

FROM dhi.io/python:3.15.2-debian13@sha256:76e89381500bad12fdaba7333bca42a96e4f1a72e3bf5ef753bbe5258cc77ac8 AS app
WORKDIR /app/
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY main.py /app/
COPY app/ /app/app/
ENTRYPOINT [ "python", "main.py" ]
