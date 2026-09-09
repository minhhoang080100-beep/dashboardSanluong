FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1     PYTHONUNBUFFERED=1

# Microsoft ODBC 17 is retained for compatibility with the local configuration.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg libgssapi-krb5-2 \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb -o /tmp/microsoft-prod.deb \
    && dpkg -i /tmp/microsoft-prod.deb \
    && rm /tmp/microsoft-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql17 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip==26.2.1 \
    && pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home --uid 10001 dashboard

# Keep diagnostics, snapshots, and business data out of the runtime image.
COPY config.py database.py repository.py main.py ./
USER dashboard
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
