# Trusted sandbox image for EdgeHunter execution.
#
# The build context is the directory that *contains* the `edgehunter` package
# (for example `src/` in a checkout, or `site-packages/` for an installed
# wheel), so this Dockerfile copies only EdgeHunter's own trusted sources. It
# never copies a target repository, never installs the target's dependencies,
# and never embeds host state.
#
# The base image is pinned by digest so the image identity is verifiable rather
# than assumed. Update the tag and the digest together, deliberately.
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=0 \
    PYTHONPATH=/opt/edgehunter/site

# UID 65534 is already the image's built-in `nobody` account, which is
# non-root and non-login. No account is created here: `useradd --uid 65534`
# fails because that UID is already taken. The container is additionally started
# with `--user` and `--cap-drop ALL` by the host, so the baked-in USER is defence
# in depth rather than the only control.

WORKDIR /opt/edgehunter/site

# Only trusted project sources. `COPY <dir>` copies the *contents* of the package
# directory, so naming a subdirectory is what puts the package at
# /opt/edgehunter/site/edgehunter (importable) instead of flattening it into the
# site directory itself. /input, /output and /workspace are mounted read-only or
# purpose-built at run time.
COPY edgehunter /opt/edgehunter/site/edgehunter

USER 65534:65534

# `-s` keeps the user site-packages directory out of sys.path and `-B` stops the
# worker writing bytecode into the read-only root filesystem. `-I` is
# deliberately not used: isolated mode ignores PYTHONPATH, which is how
# `import edgehunter` resolves inside the container.
ENTRYPOINT ["python", "-B", "-s", "/opt/edgehunter/site/edgehunter/runtime/worker.py"]