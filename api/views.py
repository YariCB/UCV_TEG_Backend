import os
import json
import logging
import random
import string
import shutil
import subprocess
import re
from pathlib import Path
from datetime import datetime
from django.http import JsonResponse
from core.database_manager import get_db_connection
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import make_password, check_password
from django.core.mail import EmailMultiAlternatives
from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.conf import settings
from .emails import build_welcome_email, build_reset_email
logger = logging.getLogger(__name__)

ALLOWED_MODEL_EXTENSIONS = {'.blend', '.obj', '.glb', '.gltf', '.stl'}
MAX_SUBMESHES = 25

_SAFE_SEGMENT_RE = re.compile(r'[^a-zA-Z0-9_-]')


def _sanitize_path_segment(value, fallback):
    if value is None:
        return fallback
    cleaned = _SAFE_SEGMENT_RE.sub('_', str(value)).strip('_')
    return cleaned or fallback


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

            try:
                subject, text_body, html_body = build_welcome_email(
                    first_name=first_name,
                    last_name=last_name,
                )
                message = EmailMultiAlternatives(subject=subject, body=text_body, to=[email])
                message.attach_alternative(html_body, "text/html")
                message.send(fail_silently=False)
            except Exception as email_error:
                logger.warning("Envío de correo de bienvenida ha fallado para %s: %s", email, email_error)

            return JsonResponse({'message': 'Usuario registrado con éxito'}, status=201)
       
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
        
        finally:
            if 'conn' in locals():
                conn.close()


# Login User
@csrf_exempt
def login_user(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email')
            password = data.get('password')

            conn = get_db_connection()
            cursor = conn.cursor()

            query = "SELECT UserID, FirstName, Password FROM teg_oltp.users WHERE Email = ? AND isActive = true"
            cursor.execute(query, (email, ))
            row = cursor.fetchone()

            if row:
                user_id = row[0]
                first_name = row[1]
                hashed_password = row[2]

                if check_password(password, hashed_password):
                    return JsonResponse({
                        'message': 'Inicio de sesión exitoso',
                        'user': {
                            'id': user_id,
                            'firstName': first_name,
                            'email': email
                        }
                    }, status=200)
                else:
                    return JsonResponse({'error': 'Contraseña incorrecta'}, status=401)
            
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)
        
        finally:
            if 'conn' in locals():
                conn.close()


# Password Recovery

@csrf_exempt
def send_reset_code(request):
    print("I'm in send_reset_code")
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email')

            # Verificación de existencia del usuario (correo)
            conn = get_db_connection()
            cursor = conn.cursor()
            query = "SELECT UserID FROM teg_oltp.users WHERE Email = ?"
            cursor.execute(query, (email,))
            if not cursor.fetchone():
                return JsonResponse({'error': 'Este correo no está registrado'}, status=404)
            
            # Generación de código aleatorio
            code = ''.join(random.choices(string.digits, k=6))
            cache.set(f"reset_code_{email}", code, timeout=15*60)  # Código válido por 15 minutos

            try:
                subject, text, html = build_reset_email(code)
                msg = EmailMultiAlternatives(subject, text, to=[email])
                msg.attach_alternative(html, "text/html")
                msg.send(fail_silently=False)

                return JsonResponse({'message': 'Código de recuperación enviado'}, status=200)
            
            except Exception as email_error:
                print(f"Error al enviar correo de recuperación a {email}: {email_error}")
                return JsonResponse({
                    'error': 'No se puedo enviar el correo de recuperación. Por favor, intente más tarde.'
                }, status=503)
            
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Datos inválidoS'}, status=400)
        
        except Exception as e:
            return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)
            
        finally:
            if 'conn' in locals():
                conn.close()

@csrf_exempt
def verify_reset_code(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email')
            code = data.get('code')

            cached_code = cache.get(f"reset_code_{email}")

            if cached_code and cached_code == code:
                return JsonResponse({'message': 'Código verificado'}, status=200)
            
            return JsonResponse({'error': 'El código es incorrecto o ha expirado'}, status=400)
        
        except json.JSONDecodeError:
            return JsonResponse({'error': 'El formato de los datos es inválido'}, status=400)
        
        except Exception as e:
            return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)

@csrf_exempt
def reset_password(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email')
            input_code = data.get('code')
            user_id = data.get('userId')
            new_password = data.get('newPassword')
            conn = get_db_connection()
            if conn is None:
                return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)
            cursor = conn.cursor()

            if input_code:
                saved_code = cache.get(f"reset_code_{email}")
                if not saved_code or saved_code != input_code:
                    return JsonResponse({'error': 'El código es incorrecto o ha expirado'}, status=400)
            else:
                if not user_id:
                    return JsonResponse({'error': 'Código requerido'}, status=400)
                cursor.execute(
                    "SELECT UserID FROM teg_oltp.users WHERE UserID = ? AND Email = ?",
                    (user_id, email)
                )
                if not cursor.fetchone():
                    return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

            hashed_password = make_password(new_password)

            query = "UPDATE teg_oltp.users SET Password = ? WHERE Email = ?"
            cursor.execute(query, (hashed_password, email))
            conn.commit()

            if input_code:
                cache.delete(f"reset_code_{email}")
                
            return JsonResponse({'message': 'Contraseña restablecida con éxito'}, status=200)
        
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Datos inválidos'}, status=400)
        
        except Exception as e:
            return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)
        
        finally:
            if 'conn' in locals():
                conn.close()


# Get user profile
@csrf_exempt
def get_user_profile(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            user_id = data.get('userId')

            if not user_id:
                return JsonResponse({'error': 'Usuario inválido'}, status=400)

            conn = get_db_connection()
            if conn is None:
                return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)
            cursor = conn.cursor()

            query = """
                SELECT firstname, lastname, email, pfpurl
                FROM teg_oltp.users
                WHERE userid = ?
            """
            cursor.execute(query, (user_id,))
            row = cursor.fetchone()

            if not row:
                return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

            return JsonResponse({
                'firstName': row[0],
                'lastName': row[1],
                'email': row[2],
                'pfpUrl': row[3]
            }, status=200)

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Datos inválidos'}, status=400)

        except Exception as e:
            return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)

        finally:
            if 'conn' in locals():
                conn.close()

@csrf_exempt
def update_user_profile(request):
    if request.method == 'POST':
        try:
            user_id = request.POST.get('userId')
            first_name = request.POST.get('firstName')
            last_name = request.POST.get('lastName')
            email = request.POST.get('email')
            avatar_file = request.FILES.get('avatar')

            if not user_id or not first_name or not last_name or not email:
                return JsonResponse({'error': 'Datos inválidos'}, status=400)

            conn = get_db_connection()
            if conn is None:
                return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)
            cursor = conn.cursor()

            cursor.execute(
                "SELECT UserID FROM teg_oltp.users WHERE Email = ? AND UserID <> ?",
                (email, user_id)
            )
            if cursor.fetchone():
                return JsonResponse({'error': 'Este correo ya está registrado. Pruebe con otro'}, status=400)
            
            profile_pic_url = None
            if avatar_file:
                relative_folder = os.path.join('users', f'user_{user_id}', 'pfp')
                full_path = os.path.join(settings.MEDIA_ROOT, relative_folder)

                if not os.path.exists(full_path):
                    os.makedirs(full_path)

                original_ext = os.path.splitext(avatar_file.name)[1].lower()
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                safe_filename = f"pfp_{user_id}_{timestamp}{original_ext}"

                fs = FileSystemStorage(location=full_path)
                filename = fs.save(safe_filename, avatar_file)

                profile_pic_url = f"{settings.MEDIA_URL}{relative_folder}/{filename}".replace("\\", "/")
            
            if profile_pic_url:
                query = """
                    UPDATE teg_oltp.users 
                    SET FirstName = ?, LastName = ?, Email = ?, pfpUrl = ?
                    WHERE UserID = ?
                """
                cursor.execute(query, (first_name, last_name, email, profile_pic_url, user_id  ))
            else:
                cursor.execute(
                    "UPDATE teg_oltp.users SET FirstName = ?, LastName = ?, Email = ? WHERE UserID = ?",
                    (first_name, last_name, email, user_id)
                )
            conn.commit()

            return JsonResponse({
                'message': 'Perfil actualizado con éxito',
                'profilePic': profile_pic_url
            }, status=200)

        except json.JSONDecodeError:
            return JsonResponse({'error': 'Datos inválidos'}, status=400)

        except Exception as e:
            return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)

        finally:
            if 'conn' in locals():
                conn.close()


# --- MODEL PROCESSING ---

@csrf_exempt
def evaluate_3d_model(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    user_id = request.POST.get('userId')
    project_id = request.POST.get('projectId')
    if not user_id or not project_id:
        return JsonResponse({'error': 'userId y projectId son requeridos'}, status=400)

    uploaded_file = request.FILES.get('model')
    if not uploaded_file:
        return JsonResponse({'error': 'Archivo requerido'}, status=400)

    original_ext = os.path.splitext(uploaded_file.name)[1].lower()
    if original_ext not in ALLOWED_MODEL_EXTENSIONS:
        return JsonResponse({'error': 'Extensión no permitida'}, status=400)

    blender_path = shutil.which('blender')
    if not blender_path:
        return JsonResponse({'error': 'Blender no está disponible en el servidor'}, status=500)

    script_path = Path(__file__).resolve().parent / 'blender_scripts' / 'evaluate_model.py'
    if not script_path.exists():
        return JsonResponse({'error': 'Script de evaluación no encontrado'}, status=500)

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    random_suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
    safe_user_id = _sanitize_path_segment(user_id, 'unknown')
    safe_project_id = _sanitize_path_segment(project_id, 'project')
    relative_folder = os.path.join('users', f'user_{safe_user_id}', 'projects', safe_project_id)
    full_folder = os.path.join(settings.MEDIA_ROOT, relative_folder)
    os.makedirs(full_folder, exist_ok=True)

    safe_filename = f"model_{timestamp}_{random_suffix}{original_ext}"
    fs = FileSystemStorage(location=full_folder)
    saved_filename = fs.save(safe_filename, uploaded_file)
    input_path = os.path.join(full_folder, saved_filename)

    base_name = os.path.splitext(saved_filename)[0]
    output_filename = f"{base_name}.glb"
    report_filename = f"{base_name}_report.json"
    output_path = os.path.join(full_folder, output_filename)
    report_path = os.path.join(full_folder, report_filename)

    command = [
        blender_path,
        '-b',
        '--factory-startup',
        '--python',
        str(script_path),
        '--',
        '--input',
        input_path,
        '--output',
        output_path,
        '--report',
        report_path,
        '--max-submeshes',
        str(MAX_SUBMESHES),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return JsonResponse({'error': 'Tiempo de espera agotado durante el procesamiento'}, status=504)

    if result.returncode != 0:
        logger.error('Blender fallo: %s', result.stderr or result.stdout)
        return JsonResponse({'error': 'No se pudo procesar el modelo 3D'}, status=500)

    if not os.path.exists(report_path):
        return JsonResponse({'error': 'No se generó el reporte de evaluación'}, status=500)

    try:
        with open(report_path, 'r', encoding='utf-8') as report_file:
            report = json.load(report_file)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Reporte de evaluación inválido'}, status=500)

    if report.get('error'):
        logger.error('Error en reporte Blender: %s', report.get('error'))
        if report.get('traceback'):
            logger.error('Traceback Blender: %s', report.get('traceback'))
        return JsonResponse({'error': report['error']}, status=422)

    submesh_count = int(report.get('submesh_count', 0))
    allowed = submesh_count <= MAX_SUBMESHES

    response = {
        'allowed': allowed,
        'submeshCount': submesh_count,
        'originalName': uploaded_file.name,
    }

    report_output_path = report.get('output_path') or output_path
    if allowed and report.get('exported') and report_output_path and os.path.exists(report_output_path):
        exported_filename = os.path.basename(report_output_path)
        gltf_url = f"{settings.MEDIA_URL}{relative_folder}/{exported_filename}".replace('\\', '/')
        response.update({
            'gltfUrl': gltf_url,
            'gltfFileName': exported_filename,
        })
    elif not allowed:
        response['message'] = (
            'El modelo supera el límite de submallados. '
            'Por favor, ingrese un modelo con menos submallados.'
        )
    else:
        response['message'] = 'No se pudo exportar el modelo a GLB.'

    return JsonResponse(response, status=200)


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


# Get units according to dimension
def get_dimension_units(request, dimension_id):
    conn = get_db_connection()
    if conn is None:
        return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)
    
    try:
        cursor = conn.cursor()
        query = """
            SELECT unitid, name, abbreviation, conversionfactor, isbase
            FROM teg_oltp.units
            WHERE dimensionid = ?
            ORDER BY abbreviation
            """
        cursor.execute(query, (dimension_id,))
        
        dimensionUnit = [
            {'id': row[0],
             'name': row[1],
             'abbreviation': row[2],
             'conversionfactor': row[3],
             'isbase': row[4]
            } 
            for row in cursor.fetchall()
        ]
        return JsonResponse(dimensionUnit, safe=False)
    finally:
        conn.close()

# Create a new material
@csrf_exempt
def create_material(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        user_id = data.get('userId')
        material_class_id = data.get('materialClassId')
        name = data.get('name')
        cost_usd = data.get('costUsd')
        unit_id = data.get('unitId')
        weight_g = data.get('weightG')
        measurement = data.get('measurement')
        wastage_factor = data.get('wastageFactor')
        min_purchase_quantity = data.get('minPurchaseQuantity')
        density_value = data.get('densityValue')
        density_unit_id = data.get('densityUnitId')
        width = data.get('width')
        length = data.get('length')
        thickness = data.get('thickness')
        thickness_unit_id = data.get('thicknessUnitId')
        is_active = data.get('isActive', True)

        if not user_id or not material_class_id or not name:
            return JsonResponse({'error': 'Datos invalidos'}, status=400)

        required_numbers = [cost_usd, unit_id, weight_g, wastage_factor, min_purchase_quantity]
        if any(value is None for value in required_numbers):
            return JsonResponse({'error': 'Datos invalidos'}, status=400)

        conn = get_db_connection()
        if conn is None:
            return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)

        cursor = conn.cursor()
        query = """
            INSERT INTO teg_oltp.material(
                userid,
                materialclassid,
                name,
                cost_usd,
                unitid,
                weight_g,
                measurement,
                wastagefactor,
                minpurchasequantity,
                densityvalue,
                densityunitid,
                width,
                length,
                thickness,
                thicknessunitid,
                isactive
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING materialid
        """

        cursor.execute(
            query,
            (
                user_id,
                material_class_id,
                name,
                cost_usd,
                unit_id,
                weight_g,
                measurement,
                wastage_factor,
                min_purchase_quantity,
                density_value,
                density_unit_id,
                width,
                length,
                thickness,
                thickness_unit_id,
                is_active
            )
        )
        row = cursor.fetchone()
        material_id = row[0] if row else None
        conn.commit()

        return JsonResponse({'message': 'Material creado con éxito', 'materialId': material_id}, status=201)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Datos inválidos'}, status=400)

    except Exception as e:
        return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)

    finally:
        if 'conn' in locals():
            conn.close()


# Deactivate material
@csrf_exempt
def deactivate_material(request, material_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Metodo no permitido'}, status=405)

    conn = get_db_connection()
    if conn is None:
        return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)

    try:
        cursor = conn.cursor()
        query = "UPDATE teg_oltp.material SET isactive = false WHERE materialid = ?"
        cursor.execute(query, (material_id,))
        conn.commit()

        if cursor.rowcount == 0:
            return JsonResponse({'error': 'Material no encontrado'}, status=404)

        return JsonResponse({'message': 'Material desactivado'}, status=200)
    except Exception as e:
        return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)
    finally:
        conn.close()


# Update material
@csrf_exempt
def update_material(request, material_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    try:
        data = json.loads(request.body)
        material_class_id = data.get('materialClassId')
        name = data.get('name')
        cost_usd = data.get('costUsd')
        unit_id = data.get('unitId')
        weight_g = data.get('weightG')
        measurement = data.get('measurement')
        wastage_factor = data.get('wastageFactor')
        min_purchase_quantity = data.get('minPurchaseQuantity')
        density_value = data.get('densityValue')
        density_unit_id = data.get('densityUnitId')
        width = data.get('width')
        length = data.get('length')
        thickness = data.get('thickness')
        thickness_unit_id = data.get('thicknessUnitId')

        if not material_class_id or not name:
            return JsonResponse({'error': 'Datos inválidos'}, status=400)

        required_numbers = [cost_usd, unit_id, weight_g, wastage_factor, min_purchase_quantity]
        if any(value is None for value in required_numbers):
            return JsonResponse({'error': 'Datos inválidos'}, status=400)

        conn = get_db_connection()
        if conn is None:
            return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)

        cursor = conn.cursor()
        query = """
            UPDATE teg_oltp.material
            SET materialclassid = ?,
                name = ?,
                cost_usd = ?,
                unitid = ?,
                weight_g = ?,
                measurement = ?,
                wastagefactor = ?,
                minpurchasequantity = ?,
                densityvalue = ?,
                densityunitid = ?,
                width = ?,
                length = ?,
                thickness = ?,
                thicknessunitid = ?
            WHERE materialid = ?
        """

        cursor.execute(
            query,
            (
                material_class_id,
                name,
                cost_usd,
                unit_id,
                weight_g,
                measurement,
                wastage_factor,
                min_purchase_quantity,
                density_value,
                density_unit_id,
                width,
                length,
                thickness,
                thickness_unit_id,
                material_id
            )
        )
        conn.commit()

        if cursor.rowcount == 0:
            return JsonResponse({'error': 'Material no encontrado'}, status=404)

        return JsonResponse({'message': 'Material actualizado'}, status=200)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Datos inválidos'}, status=400)
    except Exception as e:
        return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)
    finally:
        if 'conn' in locals():
            conn.close()


# Get user materials
def get_user_materials(request, user_id):
    conn = get_db_connection()
    if conn is None:
        return JsonResponse({'error': 'No se pudo conectar a la BD'}, status=500)
    
    try:
        cursor = conn.cursor()
        query = """
            SELECT 	M.materialid,
                    M.name,
                    M.materialclassid,
                    C.name AS MaterialClassName,
                    M.cost_usd,
                    M.unitid,
                    U.abbreviation,
                    M.weight_g,
                    CASE
                        WHEN M.measurement IS NOT NULL THEN CONCAT(M.measurement, ' ', U.abbreviation)
                        ELSE CONCAT(M.length, 'x', M.width, ' ', U.abbreviation)
                    END AS Measurement,
                    M.measurement,
                    M.length,
                    M.width,
                    M.thickness,
                    M.thicknessunitid,
                    M.wastagefactor,
                    M.minpurchasequantity,
                    M.densityvalue,
                    M.densityunitid,	
                    M.isactive,
                    U.dimensionid,
                    D.name AS DimensionName
            FROM teg_oltp.material M
            JOIN teg_oltp.materialclassification C ON M.materialclassid = C.materialclassid
            JOIN teg_oltp.units U ON M.unitid = U.unitid
            LEFT JOIN teg_oltp.units U_dens ON M.densityunitid = U_dens.unitid
            LEFT JOIN teg_oltp.units U_gros ON M.thicknessunitid = U_gros.unitid
            JOIN teg_oltp.dimension D ON U.dimensionid = D.dimensionid
            WHERE M.userid = ? AND M.isActive = true
            """
        cursor.execute(query, (user_id,))
        
        userMaterials = [
            {'id': row[0],
             'name': row[1],
             'materialClassId': row[2],
             'materialClassName': row[3],
             'costUsd': row[4],
             'unitId': row[5],
             'unitAbbreviation': row[6],
             'weightG': row[7],
             'measurement': row[8],
             'length': row[10],
             'width': row[11],
             'thickness': row[12],
             'thicknessUnitId': row[13],
             'wastageFactor': row[14],
             'minPurchaseQuantity': row[15],
             'densityValue': row[16],
             'densityUnitId': row[17],
             'isActive': row[18],
             'dimensionId': row[19],
             'dimensionName': row[20]
            } 
            for row in cursor.fetchall()
        ]
        return JsonResponse(userMaterials, safe=False)
    finally:
        conn.close()