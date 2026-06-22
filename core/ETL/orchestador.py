import threading
import logging
from .dim_user import run_dim_user_sync
from .dim_material import run_dim_material_sync
from .dim_submeshVersion import run_dim_submesh_sync, run_deactivate_project_submeshes
from .fact_costEstimation import run_fact_estimation_sync


logger = logging.getLogger(__name__)


# Orden de ejecución de ETLs
def _execute_version_pipeline(project_id, version_number):
    try:
        # Primero las dimensiones
        run_dim_submesh_sync(project_id, version_number)
        # Luego la tabla de hechos
        run_fact_estimation_sync(project_id, version_number)
    except Exception as e:
        logger.error(f"Error en pipeline de versión para {project_id}: {str(e)}")


# Orquestación de la sincronización de un usuario hacia la dimensión en segundo plano
def sync_user_to_olap(user_id):
    try:
        thread = threading.Thread(target=run_dim_user_sync, args=(user_id,))
        thread.start()
    except Exception as e:
        logger.error(f"Error al orquestar ETL de usuario {user_id}: {str(e)}")


# Orquestación de la sincronización de un material hacia la dimensión en segundo plano
def sync_material_to_olap(material_id):
    try:
        thread = threading.Thread(target=run_dim_material_sync, args=(material_id,))
        thread.start()
    except Exception as e:
        logger.error(f"Error al orquestar ETL de material {material_id}: {str(e)}")


# Orquestación de la sincronización de los submallados por versión de proyecto hacia la dimensión en segundo plano
def sync_submeshes_to_olap(project_id, version_number):
    try:
        thread = threading.Thread(target=_execute_version_pipeline, args=(project_id, version_number))
        thread.start()
    except Exception as e:
        logger.error(f"Error al iniciar hilo ETL para {project_id} v{version_number}: {str(e)}")


# Orquestación de la desactivación de un proyecto y todas sus versiones en el OLAP
def deactivate_project_in_olap(project_id):
    try:
        thread = threading.Thread(target=run_deactivate_project_submeshes, args=(project_id,))
        thread.start()
    except Exception as e:
        logger.error(f"Error al orquestar desactivación de proyecto {project_id}: {str(e)}")