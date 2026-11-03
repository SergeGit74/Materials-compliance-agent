# Materials Compliance Agent: container for Render, Railway or a VM.
# Build:  docker build -t materials-compliance .
# Run:    docker run -p 8501:8501 -v mc-results:/app/results --env-file .env materials-compliance
# Demo:   add  -e DEMO_MODE=true -e DEMO_PASSCODE=...  (keys come from --env-file or the host's secret settings, never from the image)
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

# pdftotext (poppler) builds the clause index over the rulebooks
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Download the rulebook PDFs listed in rules/INDEX.md (https, issuer hosts only, checksum-checked). A failed download does not stop the
# build: the app repeats the fetch on first start when rules/ has no confirmed PDF.
RUN python -m src.fetch_rules || echo "fetch_rules reported problems; the app will retry at start"

# Results (decisions, saved items, adoptions, the demo counter) live here. Mount a named volume to keep them across restarts:
# an empty named volume is filled from the image on first use, so the 50 seed results are there; a bind mount of an empty folder is not.
VOLUME /app/results

EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status == 200 else 1)"
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
