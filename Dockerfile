# Dockerfile

# Use an official Python runtime as base image.
FROM python:3.9-slim-buster

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set the working directory inside the container.
WORKDIR /app


RUN rm /etc/apt/sources.list
RUN echo 'deb http://archive.debian.org/debian buster main contrib non-free'  >> /etc/apt/sources.list
RUN echo 'deb http://archive.debian.org/debian buster-updates main contrib non-free'  >> /etc/apt/sources.list
RUN echo 'deb http://archive.debian.org/debian-security buster/updates main contrib non-free'  >> /etc/apt/sources.list
# Install system dependencies: ffmpeg (required for pydub).

# Install system dependencies: ffmpeg (required for pydub).
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg tini && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Copy the requirements file and install Python dependencies.
COPY ./requirements.txt /app/
# Use --no-cache-dir to reduce image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire application code into the container.
COPY ./app /app/app

# REMOVED: Create necessary directories if they are managed within the container
# RUN mkdir -p /app/uploads /app/database
# These directories will be created/managed by the volume mounts in docker-compose.yml

# Expose the port Gunicorn will run on.
EXPOSE 5001

# Use Tini as the entrypoint to handle signals properly
ENTRYPOINT ["/usr/bin/tini", "--"]

# Run the application using Gunicorn (production WSGI server).
# Bind to 0.0.0.0 to accept connections from outside the container.
# Use a reasonable number of workers (e.g., based on CPU cores).
# Set timeout for longer requests if needed (default 30s).
CMD ["gunicorn", "--bind", "0.0.0.0:5001", "--workers", "4", "--timeout", "120", "app:app"]

# Note: For development, you might override CMD with:
# CMD ["flask", "run", "--host=0.0.0.0", "--port=5001"]
# But the default should be production-ready Gunicorn.