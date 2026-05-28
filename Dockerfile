FROM apify/actor-python:3.14

# Install uv from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.11.14 /uv /uvx /bin/

USER myuser

# Copy dependency files first for better layer caching.
COPY --chown=myuser:myuser pyproject.toml uv.lock ./

# Install dependencies using the lockfile (no dev deps, no editable install yet).
RUN uv sync --frozen --no-dev --no-install-project

# Copy the rest of the source code.
COPY --chown=myuser:myuser . ./

# Now install the project itself.
RUN uv sync --frozen --no-dev

# Verify everything is importable.
RUN uv run python -m compileall -q main.py

# Launch the Actor.
CMD ["uv", "run", "python", "main.py"]
