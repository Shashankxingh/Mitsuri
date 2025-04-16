# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Create working directory
WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy project files
COPY . .

# Install tini to handle multiple processes
RUN apt-get update && apt-get install -y tini

# Expose a dummy port to satisfy Render's requirement for an open port
EXPOSE 10000

# Start both the dummy HTTP server and the bot (Mitsuri)
CMD ["tini", "--", "sh", "-c", "python3 -m http.server 10000 & python3 mitsuri_bot.py"]
