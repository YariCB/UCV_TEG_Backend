import os
import glob
import json
import logging
import random
import string
import shutil
import subprocess
import re
import threading
import uuid
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

# Importaciones de ETLs
from core.ETL.orchestador import (
    sync_user_to_olap, sync_material_to_olap,
    sync_submeshes_to_olap, deactivate_project_in_olap
)


ALLOWED_MODEL_EXTENSIONS = {'.blend', '.obj', '.glb', '.stl'}
MAX_SUBMESHES = 15

DEFAULT_PRUSA_CONFIG = Path(__file__).resolve().parent / 'prusa_defaults.ini'
MODEL_EVALUATION_JOBS_DIR = Path(settings.MEDIA_ROOT) / 'model_evaluation_jobs'
MODEL_EVALUATION_PROGRESS_PREFIX = '__EVAL_PROGRESS__ '

_SAFE_SEGMENT_RE = re.compile(r'[^a-zA-Z0-9_-]')


def _now_iso():
    return datetime.now().isoformat(timespec='seconds')


def _job_status_path(job_id):
    return MODEL_EVALUATION_JOBS_DIR / job_id / 'status.json'


def _job_log_path(job_id):
    return MODEL_EVALUATION_JOBS_DIR / job_id / 'stdout.log'


def _read_json_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as file_handle:
            return json.load(file_handle)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None


def _write_json_file(file_path, payload):
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = file_path.with_suffix(f'{file_path.suffix}.tmp')
    with open(temp_path, 'w', encoding='utf-8') as file_handle:
        json.dump(payload, file_handle, ensure_ascii=False)
    os.replace(temp_path, file_path)


def _update_job_status(job_id, **updates):
    status_path = _job_status_path(job_id)
    current = _read_json_file(status_path) or {'jobId': job_id}
    current.update(updates)
    current['jobId'] = job_id
    current['updatedAt'] = _now_iso()
    _write_json_file(status_path, current)
    return current


def _parse_progress_marker(line):
    if not line.startswith(MODEL_EVALUATION_PROGRESS_PREFIX):
        return None

    raw_payload = line[len(MODEL_EVALUATION_PROGRESS_PREFIX):].strip()
    if not raw_payload:
        return None

    try:
        return json.loads(raw_payload)
    except json.JSONDecodeError:
        return None


def _emit_job_log(job_id, text):
    log_path = _job_log_path(job_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, 'a', encoding='utf-8') as log_file:
        log_file.write(f'{text}\n')


def _build_model_evaluation_response(
    report,
    uploaded_file_name,
    saved_filename,
    output_path,
    relative_folder,
    input_path,
    for_3d_printing,
    original_ext,
):
    if report.get('error'):
        raise RuntimeError(report['error'])

    submesh_count = int(report.get('submesh_count', 0))
    submeshes_detail = report.get('submeshes', [])
    allowed = submesh_count <= MAX_SUBMESHES

    original_base_name = os.path.splitext(uploaded_file_name)[0]
    temp_base_name = os.path.splitext(saved_filename)[0]
    for submesh in submeshes_detail:
        current_name = submesh.get('name') or submesh.get('submeshname', '')
        if temp_base_name in current_name:
            current_name = current_name.replace(temp_base_name, original_base_name)
        elif current_name == temp_base_name or not current_name:
            current_name = original_base_name

        submesh['name'] = current_name
        submesh['submeshname'] = current_name

        if not submesh.get('id') and submesh.get('index'):
            submesh['id'] = f"submesh-{submesh['index']}"
        if not submesh.get('index') and isinstance(submesh.get('id'), str):
            match = re.search(r'(\d+)$', submesh['id'])
            if match:
                submesh['index'] = int(match.group(1))

    response = {
        'allowed': allowed,
        'submeshCount': submesh_count,
        'submeshes': submeshes_detail,
        'originalName': uploaded_file_name,
        'gbbox': report.get('global_bbox', {'x': 0, 'y': 0, 'z': 0}),
    }

    report_output_path = report.get('output_path') or output_path
    if allowed and report.get('exported') and report_output_path and os.path.exists(report_output_path):
        exported_filename = os.path.basename(report_output_path)
        gltf_url = f"{settings.MEDIA_URL}{relative_folder}/{exported_filename}".replace('\\', '/')
        response.update({
            'gltfUrl': gltf_url,
            'gltfFileName': exported_filename,
        })
        stl_path = report.get('stl_output_path')
        if (not stl_path or not os.path.exists(stl_path)) and original_ext == '.stl' and os.path.exists(input_path):
            stl_path = input_path
        if for_3d_printing and stl_path and os.path.exists(stl_path):
            scale_factor = 1.0
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

    return response


def _run_model_evaluation_job(job_id, job_context):
    status_path = _job_status_path(job_id)
    log_prefix = f"[ModelJob:{job_id}]"

    def set_status(**updates):
        return _update_job_status(job_id, **updates)

    try:
        set_status(
            status='running',
            stage='initializing',
            progress=5,
            message='Iniciando evaluación del modelo en segundo plano.',
        )

        command = job_context['command']
        report_path = job_context['report_path']
        uploaded_file_name = job_context['uploaded_file_name']
        saved_filename = job_context['saved_filename']
        output_path = job_context['output_path']
        relative_folder = job_context['relative_folder']
        input_path = job_context['input_path']
        for_3d_printing = job_context['for_3d_printing']
        original_ext = job_context['original_ext']

        set_status(progress=10, stage='launching_blender', message='Lanzando Blender y analizando geometría.')

        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        collected_output = []
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip('\n')
            if line:
                collected_output.append(line)
                _emit_job_log(job_id, line)

            progress_data = _parse_progress_marker(line.strip()) if line else None
            if progress_data:
                updates = {
                    'status': 'running',
                    'stage': progress_data.get('stage', 'processing'),
                    'progress': int(progress_data.get('percent', 0)),
                    'message': progress_data.get('message', 'Procesando modelo 3D.'),
                }
                if 'submeshCount' in progress_data:
                    updates['submeshCount'] = progress_data.get('submeshCount')
                set_status(**updates)

        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(
                'No se pudo procesar el modelo 3D. '
                f'Detalle: {" | ".join(collected_output[-10:])}'
            )

        if not os.path.exists(report_path):
            raise RuntimeError('No se generó el reporte de evaluación')

        with open(report_path, 'r', encoding='utf-8') as report_file:
            report = json.load(report_file)

        response = _build_model_evaluation_response(
            report=report,
            uploaded_file_name=uploaded_file_name,
            saved_filename=saved_filename,
            output_path=output_path,
            relative_folder=relative_folder,
            input_path=input_path,
            for_3d_printing=for_3d_printing,
            original_ext=original_ext,
        )

        set_status(
            status='completed',
            stage='done',
            progress=100,
            message='El backend terminó de procesar el modelo.',
            result=response,
        )
        _emit_job_log(job_id, f"{log_prefix} completed")

    except Exception as exc:
        logger.exception('%s Falló la evaluación en segundo plano', log_prefix)
        set_status(
            status='failed',
            stage='failed',
            progress=100,
            message='La evaluación del modelo falló.',
            error=str(exc),
        )


def _start_model_evaluation_job(job_context):
    job_id = uuid.uuid4().hex
    job_dir = MODEL_EVALUATION_JOBS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    _update_job_status(
        job_id,
        status='queued',
        stage='queued',
        progress=0,
        message='Archivo recibido. Esperando procesamiento.',
        createdAt=_now_iso(),
        result=None,
        error=None,
    )

    worker = threading.Thread(target=_run_model_evaluation_job, args=(job_id, job_context), daemon=True)
    worker.start()
    return job_id


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
                RETURNING UserID
            """
            cursor.execute(query, (first_name, last_name, email, hashed_password, registration_date, is_active))
            new_user_id = cursor.fetchone()[0]
            conn.commit()

            # Ejecución de ETL para inserción de nuevo usuario en OLAP
            sync_user_to_olap(new_user_id)

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


# Actualización de perfil de usuario
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

            # Ejecución de ETL para actualización de usuario en OLAP
            sync_user_to_olap(user_id)

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

        # Ejecución de ETL para inserción de nuevo material en OLAP
        sync_material_to_olap(material_id)

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

        # Ejecución de ETL para actualización de material en OLAP
        sync_material_to_olap(material_id)

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
                    U.conversionfactor,
                    U_dens.abbreviation AS DensityAbb,
					U_gros.abbreviation AS ThicknessAbbr
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
                'conversionFactor': row[22],
                'densityUnitAbbreviation': row[23],
                'thicknessUnitAbbreviation': row[24]
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
        if not os.path.exists(actual_gcode_path) or os.path.getsize(actual_gcode_path) == 0:
            return {
                'success': False,
                'error': f"El motor de laminación no generó el archivo esperado. El modelo podría exceder el volumen de impresión. Log: {result.stdout or result.stderr}"
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
    # safe_version = _sanitize_path_segment(version_label or 'v1.0', 'v1.0')
    original_filename = os.path.splitext(uploaded_file.name)[0]
    safe_original_name = _SAFE_SEGMENT_RE.sub('_', original_filename)
    relative_folder = os.path.join('users', f'user_{safe_user_id}', 'projects', safe_project_id)
    full_folder = os.path.join(settings.MEDIA_ROOT, relative_folder)
    os.makedirs(full_folder, exist_ok=True)

    # Formato de nombre: date_userid_projectid_version.ext
    safe_filename = f"{timestamp}_{safe_user_id}_{safe_project_id}_{safe_original_name}{original_ext}"
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
        job_id = _start_model_evaluation_job({
            'command': command,
            'report_path': report_path,
            'uploaded_file_name': uploaded_file.name,
            'saved_filename': saved_filename,
            'output_path': output_path,
            'relative_folder': relative_folder,
            'input_path': input_path,
            'for_3d_printing': for_3d_printing,
            'original_ext': original_ext,
        })
    except Exception as exc:
        logger.exception('No se pudo iniciar la evaluación del modelo')
        return JsonResponse({'error': f'No se pudo iniciar la evaluación del modelo: {exc}'}, status=500)

    return JsonResponse({
        'jobId': job_id,
        'status': 'queued',
        'progress': 0,
        'message': 'La evaluación se ejecuta en segundo plano.',
        'statusUrl': f'/api/models/evaluate/status/{job_id}/',
        'originalName': uploaded_file.name,
    }, status=202)


@csrf_exempt
def evaluate_3d_model_status(request, job_id):
    if request.method != 'GET':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    status_path = _job_status_path(job_id)
    status_payload = _read_json_file(status_path)
    if not status_payload:
        return JsonResponse({'error': 'No se encontró el estado de la evaluación'}, status=404)

    response_status = 200
    if status_payload.get('status') == 'failed':
        response_status = 500

    return JsonResponse(status_payload, status=response_status)


# --- PROJECT ---

@csrf_exempt
def save_project_version(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)

            project_data = data.get('projectRecord', {})
            submeshes_data = data.get('submeshRecords', [])
            materials_data = data.get('materialAssignmentRecords', [])
            version_data = data.get('versionRecord', {})

            is_draft = version_data.get('isDraft', True)

            # Impresiones de Prueba
            print(f"\nGUARDANDO VERSIÓN - ¿Es borrador? {is_draft}")
            print(f"----- Datos del Proyecto ----- \n{json.dumps(project_data, indent=2, ensure_ascii=False)}")
            print(f"----- Datos de Submallados ({len(submeshes_data)}) ----- \n{json.dumps(submeshes_data, indent=2, ensure_ascii=False)}")
            print(f"----- Datos de Asignación de Materiales ({len(materials_data)}) ----- \n{json.dumps(materials_data, indent=2, ensure_ascii=False)}")
            print(f"----- Datos de la Versión ----- \n{json.dumps(version_data, indent=2, ensure_ascii=False)}\n")


            # Guardado en la BD

            project_id = project_data.get('projectId')
            is_draft_mode = version_data.get('isDraft', True)
            is_new_file = version_data.get('isNewFile', False)

            conn = get_db_connection()
            conn.autocommit = False
            cursor = conn.cursor()

            try:

                # Registro del Proyecto
                cursor.execute("""
                    INSERT INTO teg_oltp.project (projectid, userid, projectname, createdat, is3dprinting, isactive)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT (projectid) DO UPDATE
                    SET projectname = EXCLUDED.projectname,
                        is3dprinting = EXCLUDED.is3dprinting      
                """, (project_id,
                      project_data.get('userId'),
                      project_data.get('projectName'),
                      project_data.get('createdAt'),
                      project_data.get('is3Dprinting'),
                      project_data.get('isActive', True)
                ))

                # Obtención de la última versión registrada para el proyecto
                cursor.execute("""
                    SELECT versionnumber, isdraft
                    FROM teg_oltp.projectversion
                    WHERE projectid = ?
                    ORDER BY createdat DESC LIMIT 1;
                """, (project_id, ))
                last_version_row = cursor.fetchone()


                # Guardado de la versión (isDraft = false)

                if not is_draft_mode:
                    
                    # Identificación de versión a consolidar
                    version_number = last_version_row[0]
                    print(f"Consolidando versión final: {version_number} para proyecto {project_id}")
                    
                    cursor.execute("""
                        UPDATE teg_oltp.projectversion
                        SET isdraft = False
                        WHERE projectid = ? AND isdraft = True
                    """, (project_id,))

                    conn.commit()

                    # Ejecución del ETL para sincronización de submallados por versiones en OLAP
                    print(f"Ejecutando ETL de sincronización de submallados para proyecto {project_id} y versión {version_number}")
                    sync_submeshes_to_olap(project_id, version_number)

                    return JsonResponse({
                        'success': True,
                        "message": "Versión final consolidada",
                        "calculatedVersion": version_data.get('versionnumber')
                    }, status=200)
                

                # Estimación de costo de versión (isDraft = True)

                if not last_version_row:
                    # Se trata de un proyecto nuevo, versión inicial
                    next_version = 1.0
                    is_updating_draft = False
                    last_version_number = None
                
                else:
                    last_version_number = float(last_version_row[0])
                    raw_is_draft = last_version_row[1]
                    # Casteo a booleano
                    last_is_draft = str(raw_is_draft).lower() in ('true', '1', 't', 'y', 'yes')

                    # Si la versión anterior es borrador, se mantiene el número
                    if last_is_draft:
                        next_version = last_version_number
                        is_updating_draft = True
                    
                    # Si la versión anterior es final, se calcula el incremento
                    else:
                        is_updating_draft = False
                        # Incremento mayor: Hubo carga de nuevo archivo 3D
                        if is_new_file:
                            next_version = float(int(last_version_number)+1)
                        # Incremento menor: Mismo archivo 3D, cambio de materiales
                        else:
                            next_version = round(last_version_number + 0.1, 1)
                
                # Registro de la versión

                # Si se está actualizando el borrador anterior
                if is_updating_draft:
                    cursor.execute("""
                        UPDATE teg_oltp.projectversion
                        SET object3durl = ?, costsnapshot_usd = ?, createdat = ?, 
                            estimatedweight_g = ?, printingtime_min = ?, 
                            gbboxwidth_x = ?, gbboxheight_y = ?, gbboxdepth_z = ?
                        WHERE projectid = ? AND versionnumber = ?;
                    """, (
                        version_data.get('object3durl'),
                        version_data.get('costsnapshot_usd'),
                        version_data.get('createdat'),
                        version_data.get('estimatedweight_g'),
                        version_data.get('printingtime_min'),
                        version_data.get('gbboxwidth_x'),
                        version_data.get('gbboxheight_y'),
                        version_data.get('gbboxdepth_z'),
                        project_id,
                        next_version
                    ))
                
                # Si se está agregando un nuevo borrador
                else:
                    cursor.execute("""
                        INSERT INTO teg_oltp.projectversion (
                        projectid, versionnumber, object3durl, costsnapshot_usd, 
                        createdat, estimatedweight_g, printingtime_min, 
                        gbboxwidth_x, gbboxheight_y, gbboxdepth_z, isdraft
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, True);
                    """, (
                        project_id,
                        next_version,
                        version_data.get('object3durl'),
                        version_data.get('costsnapshot_usd'),
                        version_data.get('createdat'),
                        version_data.get('estimatedweight_g'),
                        version_data.get('printingtime_min'),
                        version_data.get('gbboxwidth_x'),
                        version_data.get('gbboxheight_y'),
                        version_data.get('gbboxdepth_z')
                    ))
                
                # Registro de Submallados y Materiales

                # Limpieza de asignaciones de materiales
                cursor.execute("""
                    DELETE FROM teg_oltp.materialassignment 
                    WHERE submeshid IN (
                        SELECT submeshid FROM teg_oltp.submesh 
                        WHERE projectid = ? AND versionnumber = ?
                    )
                """, (project_id, next_version))

                # Si se ha cargado un nuevo archivo, se trata de un nuevo proyecto
                # o la versión anterior ya fue consolidada (no se debe editar)
                if is_new_file or last_version_number is None:

                    # Limpieza de submallados del borrador actual
                    cursor.execute("""
                        DELETE FROM teg_oltp.submesh
                        WHERE projectid = ? AND versionnumber = ?
                    """, (project_id, next_version))

                    # Registro de submallados vinculados a la versión

                    for idx, mesh in enumerate(submeshes_data):

                        cursor.execute("""
                            INSERT INTO teg_oltp.submesh (
                                projectid, versionnumber, submeshname, volume_cm3,
                                area_cm2, bboxwidth_x, bboxheight_y, bboxdepth_z
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            RETURNING submeshid;
                        """, (
                            project_id,
                            next_version,
                            mesh.get('submeshName'),
                            mesh.get('volume_cm3'),
                            mesh.get('area_cm2'),
                            mesh.get('bboxwidth_x'),
                            mesh.get('bboxheight_y'),
                            mesh.get('bboxdepth_z')
                        ))

                        # Captura del identity
                        mesh['generated_id'] = cursor.fetchone()[0]

                    # Registro de asignaciones de materiales

                    for idx, mat in enumerate (materials_data):
                        target_submesh_id = submeshes_data[idx]['generated_id']

                        cursor.execute("""
                            INSERT INTO teg_oltp.materialassignment (
                            submeshid, materialid, appliedunitprice_usd, submeshcost_usd, estimatedweight_g
                        ) VALUES (?, ?, ?, ?, ?);
                        """, (
                            target_submesh_id,
                            mat.get('materialId'),
                            mat.get('appliedUnitPrice'),
                            mat.get('submeshCost_usd'),
                            mat.get('estimatedWeight_g')
                        ))

                # Cambio netamente de materiales, pero con incremento de versión
                elif not is_updating_draft:

                    # Limpieza de submallados del borrador actual
                    cursor.execute("""
                            DELETE FROM teg_oltp.submesh
                            WHERE projectid = ? AND versionnumber = ?
                    """, (project_id, next_version))

                    # Inserción de nuevas submallas
                    # Copia de geometría de la versión interior (evitando pérdida de información por recargas de React)
                    # OJO: Ya se ha corregido la causa de la pérdida de información por recargas. Se puede modificar este query y hacer un insert normal
                    cursor.execute("""
                        INSERT INTO teg_oltp.submesh (
                            projectid, versionnumber, submeshname, volume_cm3, area_cm2, bboxwidth_x, bboxheight_y, bboxdepth_z
                        )
                        SELECT projectid, ?, submeshname, volume_cm3, area_cm2, bboxwidth_x, bboxheight_y, bboxdepth_z
                        FROM teg_oltp.submesh
                        WHERE projectid = ? AND versionnumber = ?
                        ORDER BY submeshid ASC
                        RETURNING submeshid;
                    """, (next_version, project_id, last_version_number))

                    # Asignación de materiales a esta nueva versión
                    new_submesh_ids = cursor.fetchall()

                    for idx, mat in enumerate(materials_data):
                        if idx < len(new_submesh_ids):
                            target_submesh_id = new_submesh_ids[idx][0]

                            cursor.execute("""
                                INSERT INTO teg_oltp.materialassignment (
                                    submeshid, materialid, appliedunitprice_usd, submeshcost_usd, estimatedweight_g
                                ) VALUES (?, ?, ?, ?, ?);
                            """, (
                                target_submesh_id,
                                mat.get('materialId'),
                                mat.get('appliedUnitPrice'),
                                mat.get('submeshCost_usd'),
                                mat.get('estimatedWeight_g')
                            ))

                # Actualización de un borrador (se mantiene la versión)
                # Cambios netamente de la asignación de materiales
                else:

                    # Recuperación de los IDs de los submallados existentes
                    cursor.execute("""
                        SELECT submeshid
                        FROM teg_oltp.submesh
                        WHERE projectid = ? AND versionnumber = ?
                        ORDER BY submeshid ASC;
                    """, (project_id, next_version))

                    existing_submeshes = cursor.fetchall()

                    for idx, mat in enumerate(materials_data):
                        if idx < len(existing_submeshes):
                            s_id = existing_submeshes[idx][0]
                            cursor.execute("""
                                INSERT INTO teg_oltp.materialassignment (
                                    submeshid, materialid, appliedunitprice_usd, submeshcost_usd, estimatedweight_g
                                ) VALUES (?, ?, ?, ?, ?);
                            """, (
                                s_id, 
                                mat.get('materialId'), 
                                mat.get('appliedUnitPrice'), 
                                mat.get('submeshCost_usd'), 
                                mat.get('estimatedWeight_g')
                            ))

                #Commit de la transacción
                conn.commit()
                logger.info(f"[Transacción Exitosa] Proyecto {project_id} guardado. Versión asignada: {next_version}")

                # Ejecución de ETL de submallados por versión de proyecto para inserción/actualización en OLAP
                print("[ETL] Iniciando sincronización de submallados a OLAP...")
                sync_submeshes_to_olap(project_id, next_version)

                return JsonResponse({
                    'success': True,
                    'message': f'Versión {next_version} guardada exitosamente.',
                    'processedAsDraft': is_draft,
                    'validatedVersionLabel': next_version
                }, status=200)

            except Exception as tx_error:
                # Rollback en caso de error
                conn.rollback()
                logger.error(f"[Transacción Abortada] Rollback ejecutado debido a: {str(tx_error)}")
                return JsonResponse({'error': f'Error en la transacción SQL: {str(tx_error)}'}, status=500)
            
            finally:
                # Liberación de recursos del poll de PostgreSQL
                cursor.close()
                conn.close()
        
        
        except json.JSONDecodeError:
            logger.error("Error al decodificar JSON en save_project_version")
            return JsonResponse({'error': 'El cuerpo de la solicitud no es un JSON válido.'}, status=400)
        
        except Exception as e:
            logger.error(f"Error inesperado en save_project_version: {str(e)}")
            return JsonResponse({'error': f'Error inesperado: {str(e)}'}, status=500)
    
    return JsonResponse({'error': 'Método no permitido. Use POST'}, status=405)


# Obtención de proyectos de un usuario

def get_user_projects(request, user_id):
    print(f"\n===== [INICIO] get_user_projects para user_id: {user_id} =====")
    conn = get_db_connection()
    if conn is None:
        print("[-] Error: No se pudo establecer conexión con la base de datos.")
        return JsonResponse({'error': 'No se pudo conectar a la base de datos'}, status=500)
    
    try:
        cursor = conn.cursor()
        
        # Lectura de si el frontend pide un límite (para resultados de proyectos recientes)
        limit_param = request.GET.get('limit')
        limit_clause = ""
        if limit_param and limit_param.isdigit():
            limit_clause = f" LIMIT {int(limit_param)}"

        project_query = f"""
            WITH versiones_ordenadas AS (
                SELECT 
                    P.projectid, 
                    P.userid, 
                    P.projectname, 
                    P.createdat, 
                    P.is3dprinting, 
                    P.isactive, 
                    PV.createdat AS version_createdat,
                    ROW_NUMBER() OVER(PARTITION BY P.projectid ORDER BY PV.createdat DESC) as fila
                FROM teg_oltp.project P
                JOIN teg_oltp.projectversion PV ON P.projectid = PV.projectid
                WHERE P.userid = ?
                AND P.isactive = true 
                AND PV.isdraft = false
            )
            SELECT 
                projectid, 
                userid, 
                projectname, 
                createdat, 
                is3dprinting, 
                isactive, 
                version_createdat
            FROM versiones_ordenadas
            WHERE fila = 1
            ORDER BY version_createdat DESC
            {limit_clause};
        """
        cursor.execute(project_query, (user_id,))
        projects = cursor.fetchall()
        
        print(f"[+] Proyectos totales activos encontrados en BD para este usuario: {len(projects)}")
        
        response_data = []
        
        for proj in projects:
            p_id = proj[0]
            p_name = proj[2]
            is_3d_printing_bool = proj[4] in (True, 1, '1') or str(proj[4]).lower() == 'true'
            
            print(f"--> Procesando Proyecto ID: {p_id} | Nombre: '{p_name}'")
            
            # Versiones del proyecto
            version_query = """
                SELECT versionnumber, object3durl, costsnapshot_usd, createdat, 
                       estimatedweight_g, printingtime_min, gbboxwidth_x, gbboxheight_y, gbboxdepth_z, isdraft
                FROM teg_oltp.projectversion
                WHERE projectid = ?
                ORDER BY createdat ASC;
            """
            cursor.execute(version_query, (p_id,))
            versions = cursor.fetchall()
            
            # Verificación de versiones consolidadas
            has_published_version = any(ver[9] == '0' or ver[9] is False or ver[9] == 0 for ver in versions)
            
            if not has_published_version:
                continue
            
            # Estructura de versiones del proyecto
            formatted_versions = []
            latest_object_url = None
            latest_version_label = "v1.0"
            
            for ver in versions:
                v_url = ver[1]
                v_raw = ver[0]

                try:
                    v_num = float(v_raw)
                    v_label = f"v{v_num:.1f}"
                except (ValueError, TypeError):
                    v_label = f"v{v_raw}"
                
                # Guardado de los datos de la última versión válida procesada para la raíz
                latest_object_url = v_url
                latest_version_label = v_label
                
                is_draft_bool = not (ver[9] == '0' or ver[9] is False or ver[9] == 0)
                

                # Submallados de la versión del proyecto
                submesh_query = """
                    SELECT submeshid, submeshname, volume_cm3, area_cm2, bboxwidth_x, bboxheight_y, bboxdepth_z
                    FROM teg_oltp.submesh
                    WHERE projectid = ? AND versionnumber = ?;
                """
                cursor.execute(submesh_query, (p_id, v_num))
                submeshes = cursor.fetchall()
                
                # Estructuras de submallados
                formatted_submeshes = []
                for sub in submeshes:
                    s_id = sub[0]
                    
                    assignment_query = """
                        SELECT MA.materialid, MA.appliedunitprice_usd, MA.submeshcost_usd,
                            MA.estimatedweight_g, M.name, C.name, D.calculationmethod
                        FROM teg_oltp.materialassignment MA
                        JOIN teg_oltp.material M ON MA.materialid = M.materialid
                        JOIN teg_oltp.materialclassification C ON M.materialclassid = C.materialclassid
						JOIN teg_oltp.units U ON M.unitid = U.unitid
						JOIN teg_oltp.dimension D ON U.dimensionid = D.dimensionid
                        WHERE MA.submeshid = ?;
                    """
                    cursor.execute(assignment_query, (s_id,))
                    assignments = cursor.fetchone()
                    
                    material_data = None
                    if assignments:
                        material_data = {
                            'id': assignments[0],
                            'name': assignments[4],
                            'category': assignments[5],
                            'pricePerCm3': float(assignments[1]) if assignments[1] else 0.0,
                            'calculationMethod': assignments[6]
                        }
                    
                    formatted_submeshes.append({
                        'id': f"submesh-{s_id}",
                        'name': sub[1],
                        'volumeCm3': float(sub[2]) if sub[2] else 0.0,
                        'areaCm2': float(sub[3]) if sub[3] else 0.0,
                        'bbox_cm': {
                            'width_cm': float(sub[4]) if sub[4] else 0.0, # bboxwidth_x
                            'length_cm': float(sub[6]) if sub[6] else 0.0, # bboxdepth_z
                            'thickness_cm': float(sub[5]) if sub[5] else 0.0, # bboxheight_y
                        },
                        'bboxRawCm': {
                            'x': float(sub[4]) if sub[4] else 0.0,
                            'y': float(sub[5]) if sub[5] else 0.0,
                            'z': float(sub[6]) if sub[6] else 0.0,
                        },
                        'material': material_data
                    })
                
                
                formatted_versions.append({
                    'id': f"ver-{p_id}-{v_raw}",
                    'label': v_label,
                    'versionNumber': float(v_num) if 'v_num' in locals() else float(v_raw),
                    'fileName': v_url.split('/')[-1] if v_url else 'modelo.glb',
                    'object3dUrl': v_url,
                    'for3dPrinting': is_3d_printing_bool,
                    'isDraft': is_draft_bool,
                    'costSnapshot': float(ver[2]) if ver[2] else 0.0,
                    'estimatedWeightG': float(ver[4]) if ver[4] else 0.0,
                    'filamentGrams': float(ver[4]) if ver[4] else 0.0,
                    'printingTimeMin': float(ver[5]) if ver[5] else 0.0,
                    'gbboxwidth_x': float(ver[6]) if ver[6] else 0.0,
                    'gbboxheight_y': float(ver[7]) if ver[7] else 0.0,
                    'gbboxdepth_z': float(ver[8]) if ver[8] else 0.0,
                    'submeshes': formatted_submeshes
                })
            
            # Formato de la fecha del proyecto
            raw_date = proj[3]
            formatted_date = raw_date.strftime('%d %b %Y') if hasattr(raw_date, 'strftime') else str(raw_date)
            
            # Respuesta final
            response_data.append({
                'id': p_id,
                'name': p_name,
                'date': formatted_date,
                'status': 'Completado',
                'is3dprinting': is_3d_printing_bool,
                'object3dUrl': latest_object_url,
                'version': latest_version_label,
                'versions': formatted_versions
            })
            print(f"    [OK] Proyecto '{p_name}' mapeado con preview y versión string ({latest_version_label}).")
            
        print(f"===== [FIN] get_user_projects. Enviando {len(response_data)} proyectos al frontend. =====\n")
        return JsonResponse(response_data, safe=False, status=200)
        
    except Exception as e:
        print(f"[-] ERROR CRÍTICO en get_user_projects: {str(e)}")
        return JsonResponse({'error': f"Error en el servidor: {str(e)}"}, status=500)
    finally:
        cursor.close()
        conn.close()


# Conteo de proyectos totales para un usuario
def get_user_projects_count(request, user_id):
    conn = get_db_connection()
    if conn is None:
        print("[-] Error: No se pudo establecer conexión con la base de datos.")
        return JsonResponse({'error': 'No se pudo conectar a la base de datos'}, status=500)
    
    try:
        cursor = conn.cursor()
        
        count_query = """
            SELECT COUNT(DISTINCT P.projectid) 
            FROM teg_oltp.project P
            JOIN teg_oltp.projectversion PV ON P.projectid = PV.projectid
            WHERE userid = ? AND isactive = true AND isDraft = false;
        """
        cursor.execute(count_query, (user_id,))
        result = cursor.fetchone()
        total_projects = result[0] if result else 0
        
        print(f"[+] Conteo total de proyectos activos: {total_projects}")
        
        return JsonResponse({'total_projects': total_projects}, status=200)
        
    except Exception as e:
        print(f"[-] ERROR CRÍTICO en get_user_projects_count: {str(e)}")
        return JsonResponse({'error': f"Error en el servidor: {str(e)}"}, status=500)
    finally:
        cursor.close()
        conn.close()


# Eliminación lógica (desactivación) de proyectos
@csrf_exempt
def deactivate_project(request, project_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido. Use POST'}, status=405)
        
    conn = get_db_connection()
    if conn is None:
        return JsonResponse({'error': 'No se pudo conectar a la base de datos'}, status=500)
        
    try:
        cursor = conn.cursor()
        
        update_query = """
            UPDATE teg_oltp.project
            SET isactive = false
            WHERE projectid = ?;
        """        
        cursor.execute(update_query, (project_id,))

        # Verificación de existencia del proyecto
        if cursor.rowcount == 0:
            return JsonResponse({'error': 'El proyecto no fue encontrado'}, status=404)
        
        conn.commit()

        # Ejecución de ETL de submallados por versión de proyecto para actualización en OLAP
        deactivate_project_in_olap(project_id)
            
        logger.info(f"[Proyecto Desactivado] ID: {project_id} marcado como inactivo.")
        return JsonResponse({'success': True, 'message': 'Proyecto eliminado exitosamente.'}, status=200)
        
    except Exception as e:
        conn.rollback()
        logger.error(f"Error al desactivar el proyecto {project_id}: {str(e)}")
        return JsonResponse({'error': f"Error en el servidor: {str(e)}"}, status=500)
    finally:
        cursor.close()
        conn.close()