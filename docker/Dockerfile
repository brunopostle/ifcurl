FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    git libgl1 libglib2.0-0 libosmesa6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir ".[service]"

# Remap localhost:3000 → forgejo:3000 so git can clone from the Forgejo
# container by its Docker-internal hostname when processing ifc:// URLs.
RUN git config --global url."http://forgejo:3000/".insteadOf "http://localhost:3000/"

EXPOSE 8000
CMD ["ifcurl", "serve", "--host", "0.0.0.0", "--port", "8000", "--allowed-hosts", "localhost:3000"]
