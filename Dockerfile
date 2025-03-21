# Dockerfile 

# Use an official Python runtime as base image.
FROM python:3.9-slim-buster

# Set the working directory inside the container.
WORKDIR /app

# Install ffmpeg (required for pydub).
RUN apt-get update && apt-get install -y ffmpeg

# Copy the requirements file and install dependencies.
COPY ./requirements.txt /app/
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire app folder.
COPY ./app /app/app

# Expose the port the app runs on.
EXPOSE 5001

# Set environment variables for Flask.
ENV FLASK_APP=app
ENV FLASK_RUN_HOST=0.0.0.0

# Run the Flask application.
CMD ["flask", "run", "--port=5001"]
