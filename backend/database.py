import pyodbc
from .config import settings
import logging

logger = logging.getLogger(__name__)

def get_db_connection():
    try:
        conn_str = (
            f"DRIVER={{{settings.DB_DRIVER}}};"
            f"SERVER={settings.DB_SERVER};"
            f"DATABASE={settings.DB_DATABASE};"
            f"UID={settings.DB_USERNAME};"
            f"PWD={settings.DB_PASSWORD};"
            "Encrypt=yes;"
            "TrustServerCertificate=yes;" # For development/testing
        )
        conn = pyodbc.connect(conn_str)
        return conn
    except Exception as e:
        logger.error(f"Error connecting to database: {e}")
        return None
