import logging
from datetime import date, timedelta
from core.database_manager import get_db_connection 

logger = logging.getLogger(__name__)

# Traducción y formato en español de días y meses

DIAS_SEMANA = {
    1: ('Lunes', 'Lun'),
    2: ('Martes', 'Mar'),
    3: ('Miércoles', 'Mié'),
    4: ('Jueves', 'Jue'),
    5: ('Viernes', 'Vie'),
    6: ('Sábado', 'Sáb'),
    7: ('Domingo', 'Dom')
}

MESES = {
    1: ('Enero', 'Ene'),
    2: ('Febrero', 'Feb'),
    3: ('Marzo', 'Mar'),
    4: ('Abril', 'Abr'),
    5: ('Mayo', 'May'),
    6: ('Junio', 'Jun'),
    7: ('Julio', 'Jul'),
    8: ('Agosto', 'Ago'),
    9: ('Septiembre', 'Sep'),
    10: ('Octubre', 'Oct'),
    11: ('Noviembre', 'Nov'),
    12: ('Diciembre', 'Dic')
}

# Poblado de la tabla teg_olap.dimtime con todas las fechas secuenciales
# desde start_year hasta end_year
def seed_dim_time(start_year=2026, end_year=2050):

    start_date = date(start_year, 1, 1)
    end_date = date(end_year, 12, 31)

    records = []
    current_date = start_date

    logger.info(f"Generando registros para DimTime desde {start_date} hasta {end_date}...")

    # Generación de cada día en memoria
    while current_date <= end_date:
        year = current_date.year
        month = current_date.month
        day_of_year = current_date.timetuple().tm_yday
        day_of_month = current_date.day
        day_of_week = current_date.isoweekday() # 1: Lunes, 7: Domingo
        week_of_year = current_date.isocalendar()[1]
        
        # Cálculos de trimestre y semestre
        quarter = (month - 1) // 3 + 1
        semester = 1 if month <= 6 else 2
        
        day_desc, day_short = DIAS_SEMANA[day_of_week]
        month_desc, month_short = MESES[month]

        # Construcción de la tupla. SKDimTime es IDENTITY
        records.append((
            current_date, year, month, day_of_year, day_of_month, 
            day_of_week, week_of_year, quarter, semester, 
            day_desc, day_short, month_desc, month_short
        ))

        current_date += timedelta(days=1)

    # Conexión e inserción en base de datos
    conn = get_db_connection()
    if not conn:
        logger.error("No se pudo conectar a la BD para poblar DimTime.")
        return

    try:
        cursor = conn.cursor()
        
        insert_query = """
            INSERT INTO teg_olap.DimTime (
                FullDate, YearCode, MonthCode, DayOfYearCode, DayOfMonthCode, 
                DayOfWeekCode, WeekCode, QuarterDesc, SemesterDesc, 
                DayOfWeekDesc, DayOfWeekShortDesc, MonthDesc, MonthShortDesc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (FullDate) DO NOTHING;
        """
        
        # executemany: inserción en lote (bulk insert)
        cursor.executemany(insert_query, records)
        conn.commit()
        
        logger.info(f"DimTime poblada con éxito. Se prepararon {len(records)} días.")
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error poblando DimTime: {str(e)}")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    seed_dim_time()