FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
COPY templates/ templates/
COPY cache_data.json .
ENV DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 5000
CMD ["python", "server.py"]
