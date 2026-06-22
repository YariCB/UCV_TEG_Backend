import logging
from core.database_manager import get_db_connection

logger = logging.getLogger(__name__)

# Lectura de datos frescos del usuario desde el OLTP
# utilizando el user_id del usuario recién creado.
def run_dim_user_sync(user_id):
    conn = get_db_connection()
    if not conn:
        logger.error("ETL DimUser: No hay conexión a la BD.")
        return

    try:
        cursor = conn.cursor()
        
        # 1. Extracción (E) desde el OLTP
        cursor.execute("""
            SELECT userid, firstname, lastname, registrationdate, isactive
            FROM teg_oltp.users
            WHERE userid = ?;
        """, (user_id,))
        
        row = cursor.fetchone()

        if not row:
            logger.warning(f"ETL DimUser: No se encontró al usuario con email {user_id} en el OLTP.")
            return

        user_id, first_name, last_name, reg_date, is_active = row

        # Transformación y Carga (T-L) hacia el OLAP
        # Insert/Update del registro
        cursor.execute("""
            INSERT INTO teg_olap.dimuser (userid, firstname, lastname, registrationdate, isactive)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (userid) 
            DO UPDATE SET 
                firstname = EXCLUDED.firstname,
                lastname = EXCLUDED.lastname,
                isactive = EXCLUDED.isactive;
        """, (user_id, first_name, last_name, reg_date, is_active))
        
        conn.commit()
        logger.info(f"ETL DimUser: Sincronización exitosa para el usuario {user_id}.")

    except Exception as e:
        conn.rollback()
        logger.error(f"ETL DimUser Error para {user_id}: {str(e)}")
    finally:
        cursor.close()
        conn.close()