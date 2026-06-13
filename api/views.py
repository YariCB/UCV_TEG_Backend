import os
import glob
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

ALLOWED_MODEL_EXTENSIONS = {'.blend', '.obj', '.glb', '.stl'}
MAX_SUBMESHES = 10

DEFAULT_PRUSA_CONFIG = Path(__file__).resolve().parent / 'prusa_defaults.ini'

_SAFE_SEGMENT_RE = re.compile(r'[^a-zA-Z0-9_-]')


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
                    D.name AS DimensionName,
                    D.calculationmethod,
                    U.conversionfactor
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
            {   'id': row[0],
                'name': row[1],
                'materialClassId': row[2],
                'materialClassName': row[3],
                'costUsd': row[4],
                'unitId': row[5],
                'unitAbbreviation': row[6],
                'weightG': row[7],
                'measurementText': row[8],
                'measurementNum': row[9],
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
                'dimensionName': row[20],
                'calculationMethod': row[21],
                'conversionFactor': row[22]
            } 
            for row in cursor.fetchall()
        ]
        return JsonResponse(userMaterials, safe=False)
    finally:
        conn.close()


# --- MODEL PROCESSING ---

# Función auxiliar para limpiar segmentos de ruta
def _sanitize_path_segment(value, fallback):
    if value is None:
        return fallback
    cleaned = _SAFE_SEGMENT_RE.sub('_', str(value)).strip('_')
    return cleaned or fallback

# Función auxiliar para limpiar nombres de archivo
def _sanitize_filename(value, fallback, forced_ext=None):
    base_name = os.path.basename(str(value)) if value else ''
    name, ext = os.path.splitext(base_name)
    ext = forced_ext or ext
    if ext and not ext.startswith('.'):
        ext = f".{ext}"
    safe_name = _SAFE_SEGMENT_RE.sub('_', name).strip('_') or fallback
    return f"{safe_name}{ext.lower()}"

# Obtención de nombre de archivo .mtl referenciado en un .obj
def _extract_obj_mtl_name(obj_path):
    try:
        with open(obj_path, 'r', encoding='utf-8', errors='ignore') as obj_file:
            for line in obj_file:
                if line.lower().startswith('mtllib '):
                    parts = line.strip().split(maxsplit=1)
                    return parts[1] if len(parts) > 1 else None
    except OSError:
        return None
    return None

# Reescritura del nombre del archivo .mtl referenciado en un .obj para que coincida con el nombre del archivo guardado
def _rewrite_obj_mtl_name(obj_path, new_name):
    try:
        with open(obj_path, 'r', encoding='utf-8', errors='ignore') as obj_file:
            lines = obj_file.readlines()
    except OSError:
        return

    updated = False
    for i, line in enumerate(lines):
        if line.lower().startswith('mtllib '):
            lines[i] = f"mtllib {new_name}\n"
            updated = True
            break

    if not updated:
        return

    try:
        with open(obj_path, 'w', encoding='utf-8') as obj_file:
            obj_file.writelines(lines)
    except OSError:
        return


def _slice_with_prusa(stl_path, scale_factor=1.0):
    """
    Ejecuta PrusaSlicer CLI aplicando factores de escala correctivos, centrado de cama
    y limpieza estricta de archivos residuales para evitar falsos positivos.
    """
    try:
        absolute_stl = os.path.abspath(str(stl_path))
        project_folder = os.path.dirname(absolute_stl)
        base_gcode_path = os.path.splitext(absolute_stl)[0] + '.gcode'
        config_str = os.path.abspath(str(DEFAULT_PRUSA_CONFIG))

        # CONTROL DE RESIDUOS: Si existe un G-code viejo con el mismo nombre, lo borramos antes de empezar
        if os.path.exists(base_gcode_path):
            os.remove(base_gcode_path)

        logger.info(f"[PrusaCLI] Slicing: {absolute_stl} | Escala aplicada: {scale_factor}x")

        # Configuración del comando CLI de PrusaSlicer
        command = [
            "xvfb-run",
            "-n", "99",
            "--server-args=-screen 0 1024x768x24",
            "prusa-slicer",
            "--load", config_str,
            "--scale", str(scale_factor),
            "--center", "110,110", 
            "--slice", absolute_stl,
            "--output", base_gcode_path
        ]

        # Ejecutamos el proceso capturando las salidas
        result = subprocess.run(command, capture_output=True, text=True, check=False)

        # Si el código de salida no es cero, PrusaSlicer falló catastróficamente
        if result.returncode != 0:
            logger.error(f"[PrusaCLI] PrusaSlicer abortó con código {result.returncode}")
            return {
                'success': False, 
                'error': f"PrusaSlicer falló internamente: {result.stderr or result.stdout}"
            }

        # Verificación estricta de la existencia del archivo generado
        actual_gcode_path = base_gcode_path
        if not os.path.exists(actual_gcode_path):
            # Búsqueda de emergencia en la carpeta por si mutó el nombre
            found_gcodes = glob.glob(os.path.join(project_folder, "*.gcode"))
            if found_gcodes:
                actual_gcode_path = found_gcodes[0]
            else:
                return {
                    'success': False,
                    'error': f"El motor no generó bloques válidos. Log: {result.stdout or result.stderr}"
                }

        # Inicialización de variables de métricas
        printing_time_min = "Desconocido"
        filament_grams = 0.0
        filament_cm3 = 0.0
        parsed_successfully = False

        # Lectura del archivo G-code generado
        with open(actual_gcode_path, 'rb') as f:
            try:
                f.seek(-131072, os.SEEK_END)  # Toma 128 KB desde el final, ya que ahí se encuentran los resultados
            except IOError:
                f.seek(0)  # Si el archivo mide menos de 128 KB, leer completo de forma segura
            
            tail_content = f.read().decode('utf-8', errors='ignore')
            lines = tail_content.splitlines()

        for line in lines:
            clean_line = line.strip()
            
            if "; filament used [g]" in clean_line:
                parts = clean_line.split('=')
                if len(parts) > 1 and parts[1].strip() != '':
                    filament_grams = float(parts[1].strip())
                    parsed_successfully = True
                    
            elif "; filament used [cm3]" in clean_line:
                parts = clean_line.split('=')
                if len(parts) > 1 and parts[1].strip() != '':
                    filament_cm3 = float(parts[1].strip())
                    
            # Ajustamos la búsqueda al modo normal para evitar sobreescritura del modo silencioso
            elif "; estimated printing time (normal mode)" in clean_line:
                parts = clean_line.split('=')
                if len(parts) > 1:
                    raw_time = parts[1].strip()
                    hours_match = re.search(r'(\d+)\s*h', raw_time)
                    minutes_match = re.search(r'(\d+)\s*m', raw_time)
                    
                    total_minutes = 0
                    if hours_match:
                        total_minutes += int(hours_match.group(1)) * 60
                    if minutes_match:
                        total_minutes += int(minutes_match.group(1))
                        
                    printing_time_min = str(total_minutes) if total_minutes > 0 else raw_time

        # Si el archivo se leyó pero no contiene métricas reales (objeto vacío/microscópico)
        if parsed_successfully and filament_grams == 0.0:
            return {
                'success': False,
                'error': 'La geometría escalada sigue siendo demasiado pequeña o inválida para impresión.'
            }

        # Eliminación del archivo temporal (Descomentar para producción, comentar en desarrollo)
        # if os.path.exists(actual_gcode_path):
        #     os.remove(actual_gcode_path)

        print(f"[PrusaCLI] Resultados - printingTimeMin: {printing_time_min} min, filamentGrams: {filament_grams} g, filamentVolumeCm3: {filament_cm3} cm³")

        return {
            'success': True,
            'printingTimeMin': printing_time_min,
            'filamentGrams': filament_grams,
            'filamentVolumeCm3': filament_cm3
        }

    except Exception as e:
        logger.error(f"[PrusaCLI] Excepción en tiempo de ejecución: {str(e)}")
        return {'success': False, 'error': f"Error interno en el script de laminación: {str(e)}"}
    

# Vista principal para evaluación de modelos 3D
# Recibe el archivo 3D cargado desde el front, lo almacena en el servidor (backend) e invoca el proceso de Blender (headless)
# para contar sus submallados y exportarlo a formato .glb/.gltf
@csrf_exempt
def evaluate_3d_model(request):

    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    user_id = request.POST.get('userId')
    project_id = request.POST.get('projectId')
    version_label = request.POST.get('versionLabel') or request.POST.get('version')
    if not user_id or not project_id:
        return JsonResponse({'error': 'userId y projectId son requeridos'}, status=400)

    # Recuperación del archivo binario del modelo 3D y su extensión original

    uploaded_file = request.FILES.get('model')
    mtl_file = request.FILES.get('mtl')
    if not uploaded_file:
        return JsonResponse({'error': 'Archivo requerido'}, status=400)

    original_ext = os.path.splitext(uploaded_file.name)[1].lower()
    if original_ext not in ALLOWED_MODEL_EXTENSIONS:
        return JsonResponse({'error': 'Extensión no permitida'}, status=400)

    # Verificación de disponibilidad de Blender y del script de evaluación

    blender_path = shutil.which('blender')
    if not blender_path:
        return JsonResponse({'error': 'Blender no está disponible en el servidor'}, status=500)

    script_path = Path(__file__).resolve().parent / 'blender_scripts' / 'evaluate_model.py'
    if not script_path.exists():
        return JsonResponse({'error': 'Script de evaluación no encontrado'}, status=500)

    # Construcción de rutas para almacenar el archivo
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    safe_user_id = _sanitize_path_segment(user_id, 'unknown')
    safe_project_id = _sanitize_path_segment(project_id, 'project')
    safe_version = _sanitize_path_segment(version_label or 'v1.0', 'v1.0')
    relative_folder = os.path.join('users', f'user_{safe_user_id}', 'projects', safe_project_id)
    full_folder = os.path.join(settings.MEDIA_ROOT, relative_folder)
    os.makedirs(full_folder, exist_ok=True)

    # Formato de nombre: date_userid_projectid_version.ext
    safe_filename = f"{timestamp}_{safe_user_id}_{safe_project_id}_{safe_version}{original_ext}"
    fs = FileSystemStorage(location=full_folder)
    saved_filename = fs.save(safe_filename, uploaded_file)
    input_path = os.path.join(full_folder, saved_filename)

    # Dependencia de materiales .mtl para archivos .obj
    if original_ext == '.obj' and mtl_file:
        referenced_mtl = _extract_obj_mtl_name(input_path)
        target_mtl_name = referenced_mtl or mtl_file.name
        safe_mtl_name = _sanitize_filename(target_mtl_name, 'material', '.mtl')
        mtl_path = os.path.join(full_folder, safe_mtl_name)
        with open(mtl_path, 'wb') as mtl_dest:
            for chunk in mtl_file.chunks():
                mtl_dest.write(chunk)
        if referenced_mtl and os.path.basename(referenced_mtl) != safe_mtl_name:
            _rewrite_obj_mtl_name(input_path, safe_mtl_name)

    # Definición de rutas absolutas
    base_name = os.path.splitext(saved_filename)[0]
    output_filename = f"{base_name}.glb"
    report_filename = f"{base_name}_report.json"
    output_path = os.path.join(full_folder, output_filename)
    report_path = os.path.join(full_folder, report_filename)

    # Captura del flag que indica si se trata de un proyecto de manufactura aditiva
    for_3d_printing = request.POST.get('for3dPrinting') == 'true'

    # Construcción de la llamada por CLI para invocar a Blender Headless
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
        '--filename',
        uploaded_file.name,
    ]

    # Agregado de flag para proyectos de manufactura aditiva
    if for_3d_printing:
        command.append('--for-3d-printing')

    try:
        # Ejecución del análisis geométrico
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return JsonResponse({'error': 'Tiempo de espera agotado durante el procesamiento'}, status=504)

    # Verificación de resultados y manejo de errores

    if result.returncode != 0:
        logger.error('Blender fallo: %s', result.stderr or result.stdout)
        return JsonResponse({'error': 'No se pudo procesar el modelo 3D'}, status=500)

    if not os.path.exists(report_path):
        return JsonResponse({'error': 'No se generó el reporte de evaluación'}, status=500)

    # Carga y conversión del reporte devuelto por Blender
    try:
        with open(report_path, 'r', encoding='utf-8') as report_file:
            report = json.load(report_file)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Reporte de evaluación inválido'}, status=500)

    # Control de excepciones atrapadas e identificadas por el script de Blender
    if report.get('error'):
        logger.error('Error en reporte Blender: %s', report.get('error'))
        if report.get('traceback'):
            logger.error('Traceback Blender: %s', report.get('traceback'))
        return JsonResponse({'error': report['error']}, status=422)

    # Evaluación de submallados permitidos

    submesh_count = int(report.get('submesh_count', 0))
    submeshes_detail = report.get('submeshes', [])
    allowed = submesh_count <= MAX_SUBMESHES

    response = {
        'allowed': allowed,
        'submeshCount': submesh_count,
        'submeshes': submeshes_detail,
        'originalName': uploaded_file.name,
    }

    # Despacho de URLs estáticas públicas para renderizado con Three.js
    report_output_path = report.get('output_path') or output_path
    if allowed and report.get('exported') and report_output_path and os.path.exists(report_output_path):
        exported_filename = os.path.basename(report_output_path)
        gltf_url = f"{settings.MEDIA_URL}{relative_folder}/{exported_filename}".replace('\\', '/')
        response.update({
            'gltfUrl': gltf_url,
            'gltfFileName': exported_filename,
        })
        # Llamado a PrusaSlicer por CLI
        stl_path = report.get('stl_output_path')
        if (not stl_path or not os.path.exists(stl_path)) and original_ext == '.stl' and os.path.exists(input_path):
            stl_path = input_path
        if for_3d_printing and stl_path and os.path.exists(stl_path):
            scale_factor = 1.0 # Blender ya se encarga de realizar la conversión a mm y lo indica en report.get('stl_scale', 1.0)
            slicing_results = _slice_with_prusa(stl_path, scale_factor)
            if slicing_results.get('success'):
                response.update({
                    'printingTimeMin': slicing_results['printingTimeMin'],
                    'filamentGrams': slicing_results['filamentGrams'],
                    'filamentVolumeCm3': slicing_results['filamentVolumeCm3'],
                })
            else:
                error_detail = slicing_results.get('error')
                logger.error("Error en PrusaSlicer CLI: %s", error_detail)
                response['slicingError'] = error_detail or 'No se pudo estimar el costo de impresión 3D.'
                response['printingTimeMin'] = 'Desconocido'
                response['filamentGrams'] = 0
                response['filamentVolumeCm3'] = 0

    elif not allowed:
        response['message'] = (
            'El modelo supera el límite de submallados. '
            'Por favor, ingrese un modelo con menos submallados.'
        )
    else:
        response['message'] = 'No se pudo exportar el modelo a GLB.'

    return JsonResponse(response, status=200)


# --- PROJECT ---

# Guardado de la versión del proyecto
@csrf_exempt
def save_project_version(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)

            project_record = data.get('projectRecord', {})
            submesh_records = data.get('submeshRecords', [])
            material_assignment_records = data.get('materialAssignmentRecords', [])
            version_record = data.get('versionRecord', {})

            is_draft = version_record.get('isDraft', True)

            # Impresiones de Prueba
            print(f"\nGUARDANDO VERSIÓN - ¿Es borrador? {is_draft}")
            print(f"----- Datos del Proyecto ----- \n{json.dumps(project_record, indent=2, ensure_ascii=False)}")
            print(f"----- Datos de Submallados ({len(submesh_records)}) ----- \n{json.dumps(submesh_records, indent=2, ensure_ascii=False)}")
            print(f"----- Datos de Asignación de Materiales ({len(material_assignment_records)}) ----- \n{json.dumps(material_assignment_records, indent=2, ensure_ascii=False)}")
            print(f"----- Datos de la Versión ----- \n{json.dumps(version_record, indent=2, ensure_ascii=False)}\n")

            return JsonResponse({
                'success': True,
                'message': 'Datos recibidos correctamente.',
                'processedAsDraft': is_draft
            }, status=200)
        
        except json.JSONDecodeError:
            logger.error("Error al decodificar JSON en save_project_version")
            return JsonResponse({'error': 'El cuerpo de la solicitud no es un JSON válido.'}, status=400)
        
        except Exception as e:
            logger.error(f"Error inesperado en save_project_version: {str(e)}")
            return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)
    
    return JsonResponse({'error': 'Método no permitido. Use POST'}, status=405)