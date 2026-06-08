FROM python:3.11-slim

# Instalación de dependencias del sistema, driver ODBC, OpenGL por software y fuentes
RUN apt-get update && apt-get install -y --no-install-recommends \
    unixodbc \
    unixodbc-dev \
    odbc-postgresql \
    libpq-dev \
    gcc \
    g++ \
    python3-numpy \
    blender \
    prusa-slicer \
    xvfb \
    xauth \
    libgl1-mesa-dri \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia de requerimientos e instalación de dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]