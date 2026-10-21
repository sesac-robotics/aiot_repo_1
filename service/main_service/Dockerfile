FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tools ./tools
COPY tests ./tests
COPY sql ./sql
EXPOSE 8101 8102 8103
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8101"]
