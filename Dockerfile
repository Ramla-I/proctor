# Framework container (plan §6, M8): the orchestrator plus everything
# CRAT and the index crate need, warmed at build time via
# `proctor warmup`. The legacy translation container lives on master.
#
#   docker build -t proctor-framework:dev .
#   docker run --rm proctor-framework:dev run -c tests/e2e/crat_smoke.toml \
#     --input-rust tests/e2e/fixtures/001_helloworld/c2rust \
#     --tests tests/e2e/fixtures/001_helloworld/tests

FROM ubuntu:24.04

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    clang \
    cmake \
    curl \
    git \
    libclang-dev \
    libssl-dev \
    libz3-dev \
    llvm-dev \
    ninja-build \
    pkg-config \
    python3 \
    zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

RUN useradd -m proctor
USER proctor
WORKDIR /home/proctor
ENV PATH="/home/proctor/.local/bin:/home/proctor/.cargo/bin:${PATH}"

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y -q --default-toolchain stable
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

COPY --chown=proctor:proctor . /home/proctor/proctor
WORKDIR /home/proctor/proctor

RUN uv sync
# Warm everything: c2rust-transpile (built from the submodule against
# the image's LLVM), crat (pulls its pinned nightly via
# rust-toolchain.toml), stage venvs, and the index crate.
RUN uv run proctor warmup -c tests/e2e/translation_smoke.toml

ARG PROCTOR_IMAGE=proctor-framework:dev
ENV PROCTOR_IMAGE=${PROCTOR_IMAGE}

ENTRYPOINT ["uv", "run", "proctor"]
CMD ["--help"]
