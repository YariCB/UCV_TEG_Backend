import threading
import logging
from .dim_user import run_dim_user_sync
from .dim_material import run_dim_material_sync
# from .fact_estimation import run_fact_draft_insert, run_fact_consolidation

logger = logging.getLogger(__name__)


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


# # Orquestación de la inserción de un borrador en la tabla de hechos
# def insert_draft_estimation(project_id, version_id):
#     try:
#         thread = threading.Thread(target=run_fact_draft_insert, args=(project_id, version_id))
#         thread.start()
#     except Exception as e:
#         logger.error(f"Error al orquestar ETL de borrador para proyecto {project_id}: {str(e)}")


# # Orquestación de la actualización (consolidación) de la tabla de hechos
# def consolidate_estimation(project_id, version_label):
#     try:
#         thread = threading.Thread(target=run_fact_consolidation, args=(project_id, version_label))
#         thread.start()
#     except Exception as e:
#         logger.error(f"Error al orquestar consolidación para proyecto {project_id}: {str(e)}")