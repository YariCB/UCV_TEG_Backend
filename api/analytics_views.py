import os
import time
import json
import jwt
from django.http import JsonResponse
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

METABASE_SITE_URL = os.environ.get("MB_SITE_URL")
METABASE_SECRET_KEY = os.environ.get("MB_SECRET_KEY")
METABASE_DASHBOARD_ID = os.environ.get("MB_DASHBOARD_ID")

@csrf_exempt
def get_metabase_embed_url(request):
    print("Estoy en get_metabase_embed_url")
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido. Use POST'}, status=405)
        
    try:
        data = json.loads(request.body)
        project_id = data.get('projectId')
        version_number = data.get('versionNumber')
        theme = data.get('theme', 'light')

        print("Datos recibidos:", data)
        
        if not project_id or version_number is None:
            return JsonResponse({'error': 'Faltan parámetros obligatorios (projectId, versionNumber)'}, status=400)
            
        # Payload estructurado según la especificación de Signed Embedding de Metabase
        payload = {
            "resource": {"dashboard": int(METABASE_DASHBOARD_ID)},
            "params": {
                "project_id": str(project_id),
                "current_version": float(version_number)
            },
            "exp": round(time.time()) + (60 * 10)  # El token expira automáticamente en 10 minutos
        }
        print("Payload generado para Metabase:", payload)
        
        # Firma el token usando el algoritmo HS256 estándar de Metabase
        token = jwt.encode(payload, METABASE_SECRET_KEY, algorithm="HS256")
        print("Token generado:", token)

        # Construye la URL segura de incrustación añadiendo parámetros visuales mediante hash URL
        embed_url = f"{METABASE_SITE_URL}/embed/dashboard/{token}#bordered=false&titled=false&background=false"
        print("URL de incrustación generada:", embed_url)

        return JsonResponse({'dashboardUrl': embed_url}, status=200)
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Formato JSON inválido en la petición'}, status=400)
    except Exception as e:
        return JsonResponse({'error': f'Error interno al generar URL de Metabase: {str(e)}'}, status=500)