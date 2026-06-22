import logging
from core.database_manager import get_db_connection

logger = logging.getLogger(__name__)

# Lectura de datos frescos del material desde el OLTP
# Desnormalización de datos relacionados (clasificación, unidades y dimensión) y carga hacia el OLAP
def run_dim_material_sync(material_id):

    conn = get_db_connection()
    if not conn:
        logger.error("ETL DimMaterial: No hay conexión a la BD.")
        return

    try:
        cursor = conn.cursor()
        
        # Extracción y Transformación (Desnormalización)
        # Uso de COALESCE para evitar inyectar valores NULL en la dimensión
        extract_query = """
            SELECT 
                M.materialid, 
                M.name AS materialname, 
                M.cost_usd, 
                M.weight_g,
                COALESCE(M.measurement, -1), 
                COALESCE(M.width, -1),
                COALESCE(M.length, -1), 
                COALESCE(M.thickness, -1),
                COALESCE(M.wastagefactor, -1), 
                COALESCE(M.minpurchasequantity, -1),
                M.materialclassid, 
                C.name AS materialclassname,
                M.unitid, 
                U.abbreviation AS unitabbr, 
                U.name AS unitname, 
                U.dimensionid,
                D.name AS dimensionname,
                COALESCE(M.densityunitid, -1), 
                COALESCE(U_dens.abbreviation, 'N/A') AS densityunitabbr, 
                COALESCE(M.densityvalue, 0),
                COALESCE(M.thicknessunitid, -1), 
                COALESCE(U_gros.abbreviation, 'N/A') AS thicknessunitabbr
            FROM teg_oltp.material M
            JOIN teg_oltp.materialclassification C ON M.materialclassid = C.materialclassid
            JOIN teg_oltp.units U ON M.unitid = U.unitid
            LEFT JOIN teg_oltp.units U_dens ON M.densityunitid = U_dens.unitid
            LEFT JOIN teg_oltp.units U_gros ON M.thicknessunitid = U_gros.unitid
            JOIN teg_oltp.dimension D ON U.dimensionid = D.dimensionid
            WHERE M.materialid = ?;
        """
        
        cursor.execute(extract_query, (material_id,))
        row = cursor.fetchone()

        if not row:
            logger.warning(f"ETL DimMaterial: Material {material_id} no encontrado en OLTP.")
            return

        # Carga (Upsert)
        upsert_query = """
            INSERT INTO teg_olap.dimmaterial (
                materialid, materialname, cost_usd, weight_g, measurement, width, length, thickness,
                wastagefactor, minpurchasequantity, materialclassid, materialclassname,
                unitid, unitabbr, unitname, dimensionid, dimensionname,
                densityunitid, densityunitabbr, densityvalue, thicknessunitid, thicknessunitabbr
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (materialid) 
            DO UPDATE SET 
                materialname = EXCLUDED.materialname,
                cost_usd = EXCLUDED.cost_usd,
                weight_g = EXCLUDED.weight_g,
                measurement = EXCLUDED.measurement,
                width = EXCLUDED.width,
                length = EXCLUDED.length,
                thickness = EXCLUDED.thickness,
                wastagefactor = EXCLUDED.wastagefactor,
                minpurchasequantity = EXCLUDED.minpurchasequantity,
                materialclassid = EXCLUDED.materialclassid,
                materialclassname = EXCLUDED.materialclassname,
                unitid = EXCLUDED.unitid,
                unitabbr = EXCLUDED.unitabbr,
                unitname = EXCLUDED.unitname,
                dimensionid = EXCLUDED.dimensionid,
                dimensionname = EXCLUDED.dimensionname,
                densityunitid = EXCLUDED.densityunitid,
                densityunitabbr = EXCLUDED.densityunitabbr,
                densityvalue = EXCLUDED.densityvalue,
                thicknessunitid = EXCLUDED.thicknessunitid,
                thicknessunitabbr = EXCLUDED.thicknessunitabbr;
        """
        
        cursor.execute(upsert_query, row)
        conn.commit()
        logger.info(f"ETL DimMaterial: Sincronización exitosa para material_id {material_id}")

    except Exception as e:
        conn.rollback()
        logger.error(f"ETL DimMaterial Error para {material_id}: {str(e)}")
    finally:
        cursor.close()
        conn.close()