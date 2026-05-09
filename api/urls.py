from django.urls import path
from .views import(
    register_user,
    login_user,
    send_reset_code,
    verify_reset_code,
    reset_password,
    get_material_classifications,
    get_material_dimensions,
    
)

urlpatterns = [
    # Auth
    path('auth/register/', register_user, name='register_user'),
    path('auth/login/', login_user, name='login_user'),
    path('auth/send-code/', send_reset_code, name='send_reset_code'),
    path('auth/verify-code/', verify_reset_code, name='verify_code'),
    path('auth/reset-password/', reset_password, name='reset_password'),
    
    # Materials
    path('materials/classifications/', get_material_classifications, name='get_material_classifications'),
    path('materials/dimensions/', get_material_dimensions, name='get_material_dimensions'),
]