FROM python:3.12-slim

WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application
COPY . .

# Expose port
EXPOSE 8080

# Run with gunicorn - Railway sets PORT env variable
CMD gunicorn app:app --bind 0.0.0.0:${PORT:-8080} --workers 2 --timeout 120
