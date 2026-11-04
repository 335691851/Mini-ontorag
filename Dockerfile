FROM python:3.12-slim
WORKDIR /srv/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ontorag_service ./ontorag_service
RUN python -m ontorag_service.patch_ontorag
CMD ["python", "-m", "ontorag_service.worker"]
