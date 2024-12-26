# backend/config.py
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    ASSEMBLYAI_API_KEY = os.environ.get('ASSEMBLYAI_API_KEY')
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

    # Database Path
    DATABASE = 'backend/transcriptions.db'