from django.urls import path
from .views import(
    get_material_classifications,
    get_material_dimensions,
)

urlpatterns = [
    # Materials
    path('materials/classifications/', get_material_classifications, name='get_material_classifications'),
    path('materials/dimensions/', get_material_dimensions, name='get_material_dimensions'),
]