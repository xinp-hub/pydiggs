# The pydiggs dev container: the same environment on any machine, in GitHub Codespaces or
# any editor that speaks the Dev Container spec. Both images are pinned by digest and kept
# current by Dependabot (`docker` in .github/dependabot.yml), with a cooldown.
FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04@sha256:d94c97dd9cacf183d0a6fd12a8e87b526e9e928307674ae9c94139139c0c6eae

# uv from Astral's own image, as their docs recommend for containers. Python itself is not
# installed here: uv installs the CI matrix (3.11-3.14) in post-create.sh, so the container
# tests exactly the interpreters CI does.
COPY --from=ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b /uv /uvx /usr/local/bin/
