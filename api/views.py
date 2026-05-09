import json
import logging
import random
import string
from datetime import datetime
from django.http import JsonResponse
from core.database_manager import get_db_connection
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import make_password, check_password
from django.core.mail import EmailMultiAlternatives
from django.core.cache import cache
from .emails import build_welcome_email, build_reset_email
logger = logging.getLogger(__name__)



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
            new_password = data.get('newPassword')

            saved_code = cache.get(f"reset_code_{email}")
            if not saved_code or saved_code != input_code:
                return JsonResponse({'error': 'El código es incorrecto o ha expirado'}, status=400)

            hashed_password = make_password(new_password)

            conn = get_db_connection()
            cursor = conn.cursor()

            query = "UPDATE teg_oltp.users SET Password = ? WHERE Email = ?"
            cursor.execute(query, (hashed_password, email))
            conn.commit()

            cache.delete(f"reset_code_{email}")
                
            return JsonResponse({'message': 'Contraseña restablecida con éxito'}, status=200)
        
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