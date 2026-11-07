FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    bc bison flex build-essential crossbuild-essential-arm64 \
    libssl-dev libelf-dev dwarves cpio kmod \
    device-tree-compiler python3 git ca-certificates xz-utils zstd \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /out
