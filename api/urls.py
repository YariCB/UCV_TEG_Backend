from django.urls import path
from .views import(
    register_user,
    login_user,
    get_material_classifications,
    get_material_dimensions,
)

urlpatterns = [
    # Auth
    path('auth/register/', register_user, name='register_user'),
    path('auth/login/', login_user, name='login_user'),

    # Materials
    path('materials/classifications/', get_material_classifications, name='get_material_classifications'),
    path('materials/dimensions/', get_material_dimensions, name='get_material_dimensions'),
]