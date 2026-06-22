import logging
from core.database_manager import get_db_connection

logger = logging.getLogger(__name__)

# Lectura de datos desde el registro de versiones del OLTP
# Desnormalización de datos relacionados (proyecto, versión, submallado) y carga hacia el OLAP
def run_dim_submesh_sync(project_id, version_number):

    print("Estoy en run_dim_submesh_sync con:", project_id, version_number)

    conn = get_db_connection()
    if not conn:
        logger.error("ETL DimSubmeshVersion: No hay conexión a la BD.")
        return

    try:
        cursor = conn.cursor()
        
        # Extracción y Transformación
        extract_query = """
            SELECT 
                s.submeshid,
                s.submeshname,
                s.volume_cm3,
                s.area_cm2,
                s.bboxwidth_x,
                s.bboxheight_y,
                s.bboxdepth_z,
                p.projectid,
                pv.versionnumber,
                pv.gbboxwidth_x,
                pv.gbboxheight_y,
                pv.gbboxdepth_z,
                pv.isdraft,
                p.projectname,
                p.is3dprinting,
                p.isactive
            FROM teg_oltp.submesh s
            JOIN teg_oltp.projectversion pv 
                ON s.projectid = pv.projectid AND s.versionnumber = pv.versionnumber
            JOIN teg_oltp.project p 
                ON pv.projectid = p.projectid
            WHERE s.projectid = ? AND s.versionnumber = ?;
        """
        
        cursor.execute(extract_query, (project_id, version_number))
        rows = cursor.fetchall()

        if not rows:
            logger.warning(f"ETL DimSubmesh: No se hallaron submallados para {project_id} v{version_number}.")
            return

        # 2. Carga en Bloque (Bulk Upsert)
        upsert_query = """
            INSERT INTO teg_olap.dimsubmeshversion (
                submeshid, submeshname, volume_cm3, area_cm2, bboxwidth_x,
                bboxheight_y, bboxdepth_z, projectid, versionnumber,
                gbboxwidth_x, gbboxheight_y, gbboxdepth_z,
                isdraft, projectname, is3dprinting, isactive
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (submeshid) 
            DO UPDATE SET 
                submeshname = EXCLUDED.submeshname,
                volume_cm3 = EXCLUDED.volume_cm3,
                area_cm2 = EXCLUDED.area_cm2,
                bboxwidth_x = EXCLUDED.bboxwidth_x,
                bboxheight_y = EXCLUDED.bboxheight_y,
                bboxdepth_z = EXCLUDED.bboxdepth_z,
                versionnumber = EXCLUDED.versionnumber,
                gbboxwidth_x = EXCLUDED.gbboxwidth_x,
                gbboxheight_y = EXCLUDED.gbboxheight_y,
                gbboxdepth_z = EXCLUDED.gbboxdepth_z,
                isdraft = EXCLUDED.isdraft,
                projectname = EXCLUDED.projectname,
                is3dprinting = EXCLUDED.is3dprinting,
                isactive = EXCLUDED.isactive;
        """
        
        cursor.executemany(upsert_query, rows)
        conn.commit()
        logger.info(f"ETL DimSubmesh: {cursor.rowcount} submallados sincronizados para {project_id} v{version_number}")

    except Exception as e:
        conn.rollback()
        logger.error(f"ETL DimSubmesh Error para {project_id} v{version_number}: {str(e)}")
    finally:
        cursor.close()
        conn.close()


# Actualización de submallados de un proyecto para marcarlos como inactivos en el OLAP
def run_deactivate_project_submeshes(project_id):

    print("Estoy en run_deactivate_project_submeshes con:", project_id)

    conn = get_db_connection()
    if not conn:
        logger.error("ETL DimSubmesh: No hay conexión a la BD para desactivar.")
        return

    try:
        cursor = conn.cursor()
        
        update_query = """
            UPDATE teg_olap.dimsubmeshversion
            SET isactive = False
            WHERE projectid = ?;
        """
        
        cursor.execute(update_query, (project_id,))
        conn.commit()
        
        logger.info(f"ETL DimSubmesh: Proyecto {project_id} desactivado. Filas afectadas: {cursor.rowcount}")

    except Exception as e:
        conn.rollback()
        logger.error(f"ETL DimSubmesh Error al desactivar {project_id}: {str(e)}")
    finally:
        cursor.close()
        conn.close()