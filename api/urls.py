from django.urls import path
from .views import(
    get_material_classifications,
)

urlpatterns = [
    # Materials
    path('materials/classifications/', get_material_classifications, name='get_material_classifications'),
]