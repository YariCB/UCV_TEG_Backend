import pyodbc
import os

def get_db_connection():
    try:
        conn = pyodbc.connect(
            'DRIVER={PostgreSQL Unicode};'
            f'SERVER={os.environ.get("DB_HOST")};'
            f'DATABASE={os.environ.get("DB_NAME")};'
            f'UID={os.environ.get("DB_USER")};'
            f'PWD={os.environ.get("DB_PASSWORD")};'
            f'PORT={os.environ.get("DB_PORT")};'
        )
        return conn
    except Exception as e:
        print(f"Error conectando a PostgreSQL: {e}")
        return None