# Use an official Python runtime
FROM python:3.11-slim

# Set environment variables to keep Python efficient
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Create working directory
WORKDIR /app

# 1. Install dependencies first (for caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Copy the rest of the code
COPY . .

# Render automatically sets the PORT environment variable.
# The Flask app inside your code will automatically listen to it.

# 3. Start the bot (The script handles both the Bot and the Web Server)
CMD ["python", "mitsuri_bot.py"]
