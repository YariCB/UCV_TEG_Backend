import logging
from core.database_manager import get_db_connection

logger = logging.getLogger(__name__)


# Cálculo, prorrateo, búsqueda de SKs e inserción de hechos de estimación en el OLAP
def run_fact_estimation_sync(project_id, version_number):

    conn = get_db_connection()
    if not conn:
        logger.error("ETL FactCost: No hay conexión a la BD.")
        return

    try:
        cursor = conn.cursor()
        
        # Eliminación de hechos existentes para manejar actualizaciones de versiones (Draft -> Final)
        delete_query = """
            DELETE FROM teg_olap.fact_costestimation
            WHERE skdimsubmesh IN (
                SELECT skdimsubmesh FROM teg_olap.dimsubmeshversion
                WHERE projectid = ? AND versionnumber = ?
            );
        """
        cursor.execute(delete_query, (project_id, float(version_number)))

        # Extracción, Prorrateo (Transformación) y Búsqueda de SKs (Lookups)
        insert_query = """
            WITH VolumeTotal AS (
                SELECT projectid, versionnumber, SUM(COALESCE(volume_cm3, 0)) AS total_volume
                FROM teg_oltp.submesh
                WHERE projectid = ? AND versionnumber = ?
                GROUP BY projectid, versionnumber
            )
            INSERT INTO teg_olap.fact_costestimation (
                skprojectcreatedat, skversioncreatedat, skdimuser, 
                skdimsubmesh, skdimmaterial, appliedunitprice_usd, 
                submeshcost_usd, estimatedweight_g, printingtime_min
            )
            SELECT
                dt_proj.skdimtime AS skprojectcreatedat,
                dt_ver.skdimtime AS skversioncreatedat,
                du.skdimuser AS skdimuser,
                ds.skdimsubmesh AS skdimsubmesh,
                dm.skdimmaterial AS skdimmaterial,
                ma.appliedunitprice_usd,
                ma.submeshcost_usd,
                ma.estimatedweight_g,
                -- Prorrateo del tiempo: (Tiempo Total * (Volumen Submalla / Volumen Total))
                (pv.printingtime_min * s.volume_cm3 / NULLIF(vt.total_volume, 0)) AS printingtime_min
            FROM teg_oltp.submesh s
            JOIN teg_oltp.projectversion pv 
                ON s.projectid = pv.projectid AND s.versionnumber = pv.versionnumber
            JOIN teg_oltp.project p 
                ON s.projectid = p.projectid
            JOIN teg_oltp.materialassignment ma 
                ON s.submeshid = ma.submeshid
            JOIN VolumeTotal vt 
                ON s.projectid = vt.projectid AND s.versionnumber = vt.versionnumber
            -- Lookups a las dimensiones para obtener SKs
            LEFT JOIN teg_olap.dimtime dt_proj ON dt_proj.fulldate = CAST(p.createdat AS DATE)
            LEFT JOIN teg_olap.dimtime dt_ver ON dt_ver.fulldate = CAST(pv.createdat AS DATE)
            LEFT JOIN teg_olap.dimuser du ON du.userid = p.userid
            LEFT JOIN teg_olap.dimsubmeshversion ds ON ds.submeshid = s.submeshid
            LEFT JOIN teg_olap.dimmaterial dm ON dm.materialid = ma.materialid
            WHERE s.projectid = ? AND s.versionnumber = ?;
        """
        
        v_float = float(version_number)
        cursor.execute(insert_query, (project_id, v_float, project_id, v_float))
        
        conn.commit()
        logger.info(f"ETL FactCost: Sincronizados {cursor.rowcount} hechos para {project_id} v{v_float}")

    except Exception as e:
        conn.rollback()
        logger.error(f"ETL FactCost Error para {project_id} v{version_number}: {str(e)}")
    finally:
        cursor.close()
        conn.close()