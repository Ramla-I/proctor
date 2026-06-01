FROM ubuntu:20.04

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive \
    apt-get install -y \
    clang \
    curl \
    g++ \
    gcc \
    git \
    libclang-dev \
    libssl-dev \
    llvm \
    locales \
    make \
    pkg-config \
    sudo \
    zlib1g-dev \
 && rm -rf /var/lib/apt/lists/*

RUN locale-gen en_US.UTF-8
RUN useradd -m -s /bin/bash ubuntu
RUN echo 'ubuntu ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers

USER ubuntu
WORKDIR /home/ubuntu
ENV LANG="en_US.UTF-8" \
    PATH="/home/ubuntu/local/bin:/home/ubuntu/.cargo/bin:/home/ubuntu/.local/bin:${PATH}"

COPY --chown=ubuntu:ubuntu cmake-3.31.9-linux-x86_64 local
COPY --chown=ubuntu:ubuntu ninja local/bin
COPY --chown=ubuntu:ubuntu Python-3.14.0 Python-3.14.0
RUN cd Python-3.14.0 \
 && ./configure --prefix=/home/ubuntu/local \
 && make -j \
 && make install \
 && cd .. \
 && rm -rf Python-3.14.0
RUN pip3 install toml libclang

RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
  | sh -s -- -y -q --default-toolchain none

RUN git clone https://github.com/Yale-PROCTOR/c2rust \
 && cd c2rust \
 && git checkout tractor-0.21.0 \
 && cargo build --release --bin c2rust-transpile -Z sparse-registry \
 && ln -s ~/c2rust/target/release/c2rust-transpile ~/local/bin

RUN rustup toolchain install nightly-2025-11-11
RUN rustup toolchain install -c rust-src,rustc-dev,llvm-tools-preview nightly-2025-06-23

COPY --chown=ubuntu:ubuntu Test-Corpus Test-Corpus
COPY --chown=ubuntu:ubuntu PUBLIC-Test-Corpus PUBLIC-Test-Corpus
COPY --chown=ubuntu:ubuntu aws-translate aws-translate
RUN python3 aws-translate/scripts/package/package.py \
      --root /home/ubuntu/Test-Corpus \
      -o /home/ubuntu/bundles \
      -s Public-Tests
RUN python3 aws-translate/scripts/package/package.py \
      --root /home/ubuntu/PUBLIC-Test-Corpus \
      -o /home/ubuntu/PUBLIC-bundles \
      -s Hidden-Tests

RUN git clone https://github.com/Yale-PROCTOR/crat \
 && cd crat \
 && git checkout 257ad18 \
 && cd deps_crate \
 && cargo build \
 && cd .. \
 && cargo build --release \
 && ln -s ~/crat/crat ~/local/bin \
 && ln -s ~/crat/crat-merge ~/local/bin

RUN rm -rf PUBLIC-Test-Corpus/Public-Tests
COPY --chown=ubuntu:ubuntu scripts scripts
