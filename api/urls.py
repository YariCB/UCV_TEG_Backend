from django.urls import path
from .views import(
    register_user,
    login_user,
    send_reset_code,
    verify_reset_code,
    reset_password,
    get_user_profile,
    update_user_profile,
    evaluate_3d_model,
    get_material_classifications,
    get_material_dimensions,
    get_dimension_units,
    create_material,
    get_user_materials,
    deactivate_material,
    update_material,
    save_project_version,
    get_user_projects,
    get_user_projects_count,
    deactivate_project
)

urlpatterns = [
    # Auth
    path('auth/register/', register_user, name='register_user'),
    path('auth/login/', login_user, name='login_user'),
    path('auth/send-code/', send_reset_code, name='send_reset_code'),
    path('auth/verify-code/', verify_reset_code, name='verify_code'),
    path('auth/reset-password/', reset_password, name='reset_password'),
    path('auth/profile/', get_user_profile, name='get_user_profile'),
    path('auth/profile/update/', update_user_profile, name='update_user_profile'),
    
    # Materials
    path('materials/classifications/', get_material_classifications, name='get_material_classifications'),
    path('materials/dimensions/', get_material_dimensions, name='get_material_dimensions'),
    path('materials/units/<int:dimension_id>/', get_dimension_units, name='get_dimension_units'),
    path('materials/create/', create_material, name='create_material'),
    path('materials/<int:material_id>/deactivate/', deactivate_material, name='deactivate_material'),
    path('materials/<int:material_id>/update/', update_material, name='update_material'),
    path('materials/<int:user_id>/', get_user_materials, name='get_user_materials'),

    # Model processing
    path('models/evaluate/', evaluate_3d_model, name='evaluate_3d_model'),

    # Projects & Versions
    path('projects/save-version/', save_project_version, name='save_project_version'),
    path('projects/user/<int:user_id>/', get_user_projects, name='get_user_projects'),
    path('projects/user/<int:user_id>/count/', get_user_projects_count, name='get_user_projects_count'),
    path('projects/deactivate/<str:project_id>/', deactivate_project, name='deactivate_project'),
]