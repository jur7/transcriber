import os
import time
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Set the directory to clean up
TEMP_UPLOADS_DIR = 'temp_uploads'

# Set the threshold for file deletion (24 hours in seconds)
DELETE_THRESHOLD = 24 * 60 * 60

def delete_old_files(directory, threshold):
    """
    Deletes files in the specified directory that are older than the given threshold.

    Args:
        directory: The directory to clean up.
        threshold: The age threshold in seconds. Files older than this will be deleted.
    """
    current_time = time.time()
    for filename in os.listdir(directory):
        file_path = os.path.join(directory, filename)
        if os.path.isfile(file_path):
            file_creation_time = os.path.getctime(file_path)
            file_age = current_time - file_creation_time
            if file_age > threshold:
                try:
                    os.remove(file_path)
                    logging.info(f"Deleted old file: {file_path}")
                except Exception as e:
                    logging.error(f"Error deleting file {file_path}: {e}")

if __name__ == "__main__":
    logging.info("Starting cleanup of old files...")
    delete_old_files(TEMP_UPLOADS_DIR, DELETE_THRESHOLD)
    logging.info("Cleanup completed.")