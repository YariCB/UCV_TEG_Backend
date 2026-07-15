# TEG Backend

Servicio backend del Trabajo Especial de Grado (TEG) para la Licenciatura en Computación de la Universidad Central de Venezuela (UCV), encargado de exponer la API del sistema, gestionar autenticación, persistencia de datos, evaluación de modelos 3D y sincronización de información para analítica.

## Alcance

Este repositorio implementa principalmente:

- API REST para autenticación y perfil de usuario.
- Manejo de materiales y proyectos.
- Carga y evaluación de modelos 3D.
- Integración de estimación de impresión 3D mediante PrusaSlicer.
- Envío de correos para registro y recuperación de contraseña.
- Sincronización de Micro-ETL de datos operacionales hacia capa analítica (OLAP).
- Generación de URL embebida para analítica con Metabase.

## Stack Tecnológico

- Python 3.11
- Django 5 + Django REST Framework
- PostgreSQL
- ODBC (`pyodbc`) y `psycopg2`
- Blender (procesamiento de modelos 3D)
- PrusaSlicer (estimación de impresión)
- Docker

## Estructura Relevante

- `api/views.py`: endpoints principales de negocio y procesamiento.
- `api/analytics_views.py`: integración con analitica.
- `api/blender_scripts/`: scripts para evaluación geométrica.
- `core/settings.py`: configuración general y variables de entorno.
- `core/ETL/`: sincronización de entidades a capa OLAP.

## Endpoints (Resumen)

Prefijo base: `/api/`

- Auth: registro, login, recuperación de contraseña, perfil.
- Materials: clasificaciones, dimensiones, unidades, CRUD lógico.
- Models: inicio de evaluación y consulta de estado por `job_id`.
- Projects: guardado de versiones, listado y desactivación.
- Analytics: generación de URL para embebido de Metabase.

## Variables de Entorno Principales

Definir variables en el archivo `.env` utilizado por el entorno de ejecución:

- `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`
- `EMAIL_USE_TLS`, `EMAIL_USE_SSL`
- Variables de Metabase usadas por `docker-compose` en la raíz

## Ejecución Local (Desarrollo)

1. Crear y activar entorno virtual.
2. Instalar dependencias.
3. Configurar variables de entorno.
4. Ejecutar servidor Django.

Ejemplo:

```bash
pip install -r requirements.txt
python manage.py runserver 0.0.0.0:8000
```

## Ejecución con Docker Compose (Proyecto Completo)

El archivo `docker-compose.yml` oficial del proyecto se encuentra en la **raíz general** del trabajo, fuera de este repositorio:

- `../docker-compose.yml` (referencia desde `teg-backend`)

Para iniciar el ecosistema completo (frontend, backend, PostgreSQL y Metabase), ejecutar desde la raíz del proyecto:

```bash
docker compose up --build
```

## Respaldo de configuracion de raiz

Con fines de trazabilidad, este repositorio incluye copias de respaldo de archivos de configuración global en:

- `docs/respaldo-raiz/docker-compose.root-backup.yml`
- `docs/respaldo-raiz/gitignore.root-backup`

Estas copias son **solo de referencia**. La fuente oficial para ejecución es el archivo ubicado en la raíz del proyecto.
