import json
from datetime import datetime
from django.http import JsonResponse
from core.database_manager import get_db_connection
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import make_password



# --- AUTH ---

# Register user
@csrf_exempt
def register_user(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            first_name = data.get('firstName')
            last_name = data.get('lastName')
            email = data.get('email')
            password = data.get('password')

            # Password hashing
            hashed_password = make_password(password)

            registration_date = datetime.now()
            is_active = True

            conn = get_db_connection()
            cursor = conn.cursor()

            # Check if email already exists
            check_query = "SELECT COUNT(*) FROM teg_oltp.users WHERE Email = ?"
            cursor.execute(check_query, (email,))
            if cursor.fetchone()[0] > 0:
                return JsonResponse({'error': 'Este correo ya está registrado. Pruebe recuperando su contraseña'}, status=400)

            query = """
                INSERT INTO teg_oltp.users (FirstName, LastName, Email, Password, RegistrationDate, isActive)
                VALUES (?, ?, ?, ?, ?, ?)
            """
            cursor.execute(query, (first_name, last_name, email, hashed_password, registration_date, is_active))
            conn.commit()

            return JsonResponse({'message': 'Usuario registrado con éxito'}, status=201)
       
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
        
        finally:
            if 'conn' in locals():
                conn.close()


# Get user by email and password

# 



# --- MATERIALS VIEW ---

# Get material classifications
def get_material_classifications(request):
    conn = get_db_connection()
    if conn is None:
        return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)
    
    try:
        cursor = conn.cursor()
        query = "SELECT MaterialClassID, Name FROM teg_oltp.MaterialClassification ORDER BY Name"
        cursor.execute(query)
        
        materialClass = [
            {'id': row[0], 'name': row[1]} 
            for row in cursor.fetchall()
        ]
        return JsonResponse(materialClass, safe=False)
    finally:
        conn.close()

# Get material dimensions
def get_material_dimensions(request):
    conn = get_db_connection()
    if conn is None:
        return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)
    
    try:
        cursor = conn.cursor()
        query = "SELECT dimensionid, name, calculationmethod FROM teg_oltp.dimension ORDER BY name"
        cursor.execute(query)
        
        materialDim = [
            {'id': row[0], 'name': row[1], 'calculationmethod': row[2]} 
            for row in cursor.fetchall()
        ]
        return JsonResponse(materialDim, safe=False)
    finally:
        conn.close()