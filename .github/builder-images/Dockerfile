# The images that build, emulate and SBOM-scan the release container in publish.yml.
# This file is never built. Dependabot (docker ecosystem, 14-day cooldown) reads these
# FROM lines and proposes bumps; publish.yml reads the same lines by stage name, so a
# merged Dependabot PR is the whole bump. Keep every image pinned by index digest.

# QEMU user-mode emulation for the linux/arm64 half of the build.
FROM docker.io/tonistiigi/binfmt:qemu-v10.2.3@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0 AS binfmt

# The BuildKit daemon that buildx runs.
FROM docker.io/moby/buildkit:v0.34.1@sha256:99368f3e46177c6405b9740e4461d91cee004128daeecca78d4ceac50ec1da5c AS buildkit

# The SBOM generator BuildKit attaches to the image.
FROM docker.io/docker/buildkit-syft-scanner:1.12.0@sha256:ae4f3b554449e7e25548e7d8ccc029d17357348e30c6e3df01b92bc93654d6a9 AS sbom
