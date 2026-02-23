FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

LABEL org.opencontainers.image.title="e-note-ion" \
      org.opencontainers.image.description="Cron-based content scheduler for Vestaboard split-flap displays" \
      org.opencontainers.image.source="https://github.com/JasonPuglisi/e-note-ion" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Compile bytecode at install time (faster startup) and use copy mode
# so layer snapshots aren't affected by hardlink counts.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Install dependencies before copying source for better layer caching.
# There is no [build-system] in pyproject.toml so --no-install-project is
# implied; the venv just needs the declared runtime dependencies.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source, config example, and bundled contrib content.
COPY scheduler.py entrypoint.sh config.py config.example.toml ./
COPY integrations/ ./integrations/
COPY content/contrib/ ./content/contrib/

# Create user content directory, ensure entrypoint is executable, drop to
# a non-root user.
RUN mkdir -p content/user \
    && chmod +x entrypoint.sh \
    && chown -R nobody:nogroup /app

USER nobody

# Put the venv on PATH so `python` resolves without needing `uv run`.
ENV PATH="/app/.venv/bin:$PATH"

VOLUME ["/app/content/user"]

ENTRYPOINT ["/app/entrypoint.sh"]
