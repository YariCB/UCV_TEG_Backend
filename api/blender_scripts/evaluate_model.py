import argparse
import json
import os
import sys
import traceback
import importlib
import addon_utils
import mathutils
import bpy # API de Blender Python, disponible dentro del entorno ejecutor de Blender
import bmesh # Módulo de Blender para manipulación de mallas, parte de la API bpy


# Creación del archivo JSON estructurado que Django espera como resultado del análisis de Blender
def write_report(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as report_file:
        json.dump(payload, report_file)


# Habilitación de addons necesarios para importación/exportación de formatos
# 3D en Blender, con manejo de versiones y compatibilidad
def enable_addon(module_name):
    if module_name in bpy.context.preferences.addons:
        return True
    try:
        addon_utils.enable(module_name, default_set=True, persistent=True)
    except Exception:
        try:
            bpy.ops.preferences.addon_enable(module=module_name)
        except Exception:
            pass

    if module_name in bpy.context.preferences.addons:
        return True

    try:
        module = importlib.import_module(module_name)
        if hasattr(module, 'register'):
            module.register()
    except Exception:
        return False

    return module_name in bpy.context.preferences.addons


# Obtención dinámica del operador de exportación glTF/GLB según la versión
# de la API activa en la instancia de Blender
def get_gltf_operator():
    if hasattr(bpy.ops.export_scene, 'gltf'):
        return bpy.ops.export_scene.gltf
    return None


# Función auxiliar para obtener las propiedades disponibles de un operador de Blender
def get_operator_props(operator):
    try:
        return set(operator.get_rna_type().properties.keys())
    except Exception:
        return set()


# Función de importación de archivos wavefront OBJ, con manejo de opciones
# según la versión del addon disponible
def import_obj(filepath):
    # Importación de archivos OBJ con Blender 4.0+, que utiliza un importador nativo optimizado en C++
    if hasattr(bpy.ops.wm, 'obj_import'):
        operator = bpy.ops.wm.obj_import
    else:
        # Respaldo para Blender 3.x, con base en el script de Python tradicional
        enable_addon('io_scene_obj')
        operator = bpy.ops.import_scene.obj

    props = get_operator_props(operator)
    kwargs = {'filepath': filepath}
    # Inyección condicional de parámetros para preservar texturas, materiales y jerarquías
    if 'use_image_search' in props:
        kwargs['use_image_search'] = True
    if 'import_mtl' in props:
        kwargs['import_mtl'] = True
    if 'use_materials' in props:
        kwargs['use_materials'] = True
    if 'use_split_objects' in props:
        kwargs['use_split_objects'] = True
    if 'use_split_groups' in props:
        kwargs['use_split_groups'] = True

    operator(**kwargs)


# Inyección de parámetros de optimización para asegurar que las mallas web mantengan
# colores, normales, tangentes y coordenadas UV en el visualizador Three.js
def apply_gltf_export_options(props, kwargs):
    if 'export_materials' in props:
        kwargs['export_materials'] = 'EXPORT'
    if 'export_colors' in props:
        kwargs['export_colors'] = True
    if 'export_texcoords' in props:
        kwargs['export_texcoords'] = True
    if 'export_normals' in props:
        kwargs['export_normals'] = True
    if 'export_tangents' in props:
        kwargs['export_tangents'] = True
    return kwargs


# Función de exportación a GLB para entornos web, con manejo de compatibilidad
# entre versiones de Blender y addons
def export_glb(output_path):
    enable_addon('io_scene_gltf2')
    operator = get_gltf_operator()
    if not operator:
        raise RuntimeError('No se encontró el exportador glTF en Blender.')

    candidate_path = f"{os.path.splitext(output_path)[0]}.glb"
    os.makedirs(os.path.dirname(candidate_path), exist_ok=True)

    last_error = None
    props = get_operator_props(operator)
    attempts = []
    if 'export_format' in props:
        attempts.append({'export_format': 'GLB'})
    attempts.append({})

    for extra_kwargs in attempts:
        kwargs = {'filepath': candidate_path, **extra_kwargs}
        if 'export_apply' in props:
            kwargs['export_apply'] = True
        kwargs = apply_gltf_export_options(props, kwargs)
        try:
            result = operator(**kwargs)
            if isinstance(result, set) and 'FINISHED' not in result:
                raise RuntimeError(f"Exportación cancelada: {result}")
            if not os.path.exists(candidate_path):
                raise RuntimeError('El exportador no generó el archivo esperado.')
            export_format = extra_kwargs.get('export_format', 'AUTO')
            return candidate_path, export_format
        except Exception as exc:
            last_error = exc
            continue

    if last_error:
        raise RuntimeError(f"No se pudo exportar el modelo a GLB. Detalle: {last_error}") from last_error
    raise RuntimeError('No se pudo exportar el modelo a GLB.')


# Función principal de importación de modelos 3D, con soporte para múltiples formatos
def import_model(input_path):
    extension = os.path.splitext(input_path)[1].lower()

    # Caso nativo: carga directa de la BD de bloques de Blender
    if extension == '.blend':
        bpy.ops.wm.open_mainfile(filepath=input_path)
        return

    # Limpieza de la escena
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # Evaluación y enrutamiento según formato de manufactura/modelado
    if extension == '.obj':
        import_obj(input_path)
    elif extension in {'.glb', '.gltf'}:
        if not hasattr(bpy.ops.import_scene, 'gltf'):
            enable_addon('io_scene_gltf2')
        bpy.ops.import_scene.gltf(filepath=input_path)
    elif extension == '.stl':
        if hasattr(bpy.ops.wm, 'stl_import'):
            bpy.ops.wm.stl_import(filepath=input_path)
        else:
            enable_addon('io_mesh_stl')
            bpy.ops.import_mesh.stl(filepath=input_path)
    else:
        raise ValueError(f"Unsupported extension: {extension}")


# Función de conteo de submallados
# definidos como el número de objetos de tipo MESH en la escena
def count_mesh_objects():
    return len([obj for obj in bpy.context.scene.objects if obj.type == 'MESH'])


def get_mesh_local_dimensions(mesh):
    if not mesh or not mesh.vertices:
        return 0.0, 0.0, 0.0

    min_x = min(v.co.x for v in mesh.vertices)
    max_x = max(v.co.x for v in mesh.vertices)
    min_y = min(v.co.y for v in mesh.vertices)
    max_y = max(v.co.y for v in mesh.vertices)
    min_z = min(v.co.z for v in mesh.vertices)
    max_z = max(v.co.z for v in mesh.vertices)

    return max_x - min_x, max_y - min_y, max_z - min_z


# Verificación de errores en mallas para determinar necesidad de reparaciones
def check_mesh_needs_repair(obj):
    if obj.type != 'MESH':
        return False
        
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    
    # Bordes abiertos (aristas que pertenecen a una sola cara)
    has_open_edges = any(e.is_boundary for e in bm.edges)
    # No-manifold (aristas compartidas por 0 o más de 2 caras)
    has_non_manifold = any(not e.is_manifold for e in bm.edges)
    
    bm.free()
    return has_open_edges or has_non_manifold


# Limpieza y reparación de mallas para corregir problemas de
# mallas abiertas y normales invertidas que puedan afectar cálculos
def clean_and_repair_mesh(obj):

    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    # Aplicación de escala y rotación
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

    # Modo edición para aplicar herramientas de malla
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')

    # Eliminación de vértices duplicados o colapsados
    bpy.ops.mesh.remove_doubles(threshold=0.0001)

    # Recálculo de normales hacia afuera
    bpy.ops.mesh.normals_make_consistent(inside=False)

    # Sellado de agujeros abiertos
    bpy.ops.mesh.fill_holes(sides=0)

    # Retorno seguro a modo Objeto
    bpy.ops.object.mode_set(mode='OBJECT')


def analyze_model_submeshes(filename=""):
    submeshes_info = []
    scene = bpy.context.scene
    unit_settings = scene.unit_settings
    depsgraph = bpy.context.evaluated_depsgraph_get()

    # Detección del factor de escala de la escena
    scene_scale = unit_settings.scale_length

    mesh_objects = [obj for obj in scene.objects if obj.type == 'MESH']

    # Reparación geométrica
    if parsed_args.for_3d_printing:
        print(f"[Backend] Evaluando estado geométrico de {len(mesh_objects)} objetos...", flush=True)
        for obj in mesh_objects:
            if check_mesh_needs_repair(obj):
                print(f"[Backend] Iniciando pase de saneamiento geométrico en '{obj.name}'...", flush=True)
                try:
                    clean_and_repair_mesh(obj)
                except Exception as e:
                    print(f"[Backend Warning] No se pudo reparar automáticamente el objeto {obj.name}: {str(e)}", flush=True)
            else:
                print(f"-> El objeto '{obj.name}' cuenta con una malla cerrada y sana. Se omite la limpieza.")

    # Extracción de la extensión del archivo
    ext_tuple = os.path.splitext(filename.lower())
    file_ext = ext_tuple[1] if len(ext_tuple) > 1 else ""
    needs_millimeters_fix = False
    
    if mesh_objects:
        max_dim_global = 0.0
        for obj in mesh_objects:
            obj_eval = obj.evaluated_get(depsgraph)
            obj_mesh = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            dim_x, dim_y, dim_z = get_mesh_local_dimensions(obj_mesh)
            max_dim_global = max(max_dim_global, dim_x, dim_y, dim_z)
            obj_eval.to_mesh_clear()
        
        # Si es un STL (siempre mm en impresión 3D) o si el conjunto completo supera las 50 unidades
        if file_ext == '.stl' or (max_dim_global > 50.0 and scene_scale == 1.0):
            needs_millimeters_fix = True
            
        # Si el archivo es un STL, se asumime por estándar de impresión 3D que viene en mm
        # Si es otro formato, se delega en el umbral heurístico mayor a 50 unidades
        if not needs_millimeters_fix and max_dim_global > 25.0:
            print(f"[Backend Warning] Objeto detectado con dimensión máxima de {max_dim_global} unidades sin fix de mm. Podría causar un bucle de corte extenso.", flush=True)

    def to_cm(value):
        if needs_millimeters_fix:
            return value * 0.1
        return value * scene_scale * 100.0

    for idx, obj in enumerate(mesh_objects):
        obj_eval = obj.evaluated_get(depsgraph)
        mesh_eval = obj_eval.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        dim_x, dim_y, dim_z = get_mesh_local_dimensions(mesh_eval)
        bm = bmesh.new()
        if mesh_eval:
            bm.from_mesh(mesh_eval)

        open_edges = 0
        is_closed = False
        volume_method = 'none'
        area_internal = 0.0
        if bm.faces:
            bmesh.ops.triangulate(bm, faces=bm.faces)
            bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            area_internal = sum(face.calc_area() for face in bm.faces)
            open_edges = sum(1 for e in bm.edges if not e.is_manifold)
            is_closed = open_edges == 0
            if is_closed:
                volume_internal = abs(bm.calc_volume())
                volume_method = 'closed'
            else:
                hull_bm = bmesh.new()
                hull_bm.from_mesh(mesh_eval)
                try:
                    bmesh.ops.convex_hull(hull_bm, input=hull_bm.verts, use_existing_faces=True)
                    volume_internal = abs(hull_bm.calc_volume())
                    volume_method = 'convex_hull'
                except Exception:
                    volume_internal = 0.0
                    volume_method = 'failed'
                hull_bm.free()
        else:
            volume_internal = 0.0

        bm.free()
        if mesh_eval:
            obj_eval.to_mesh_clear()

        if needs_millimeters_fix:
            volume_cm3 = volume_internal * 0.001
            area_cm2 = area_internal * 0.01
        else:
            real_cubic_meters = volume_internal * (scene_scale ** 3)
            volume_cm3 = real_cubic_meters * 1000000.0
            area_cm2 = area_internal * (scene_scale ** 2) * 10000.0

        dim_x_cm = to_cm(dim_x)
        dim_y_cm = to_cm(dim_y)
        dim_z_cm = to_cm(dim_z)
        dims_cm = [dim_x_cm, dim_y_cm, dim_z_cm]
        dims_cm = sorted(dims_cm, reverse=True)
        bbox_cm = {
            'length': round(dims_cm[0], 2),
            'width': round(dims_cm[1], 2),
            'thickness': round(dims_cm[2], 2),
        }
        bbox_cm_raw = {
            'x': round(dim_x_cm, 2),
            'y': round(dim_y_cm, 2),
            'z': round(dim_z_cm, 2),
        }
        
        submeshes_info.append({
            'id': f"submesh-{idx + 1}",
            'index': idx + 1,
            'name': obj.name,
            'bbox_cm': bbox_cm,
            'bbox_cm_raw': bbox_cm_raw,
            'volume_cm3': round(volume_cm3, 2),
            'area_cm2': round(area_cm2, 2),
            'is_closed': is_closed,
            'open_edges': open_edges,
            'volume_method': volume_method,
        })
    
    return submeshes_info, needs_millimeters_fix


def export_stl(output_path, scale=1.0):
    if hasattr(bpy.ops.wm, 'stl_export'):
        operator = bpy.ops.wm.stl_export
    else:
        enable_addon('io_mesh_stl')
        operator = bpy.ops.export_mesh.stl

    props = get_operator_props(operator)
    kwargs = {'filepath': output_path}
    if 'global_scale' in props:
        kwargs['global_scale'] = scale
    elif 'scale' in props:
        kwargs['scale'] = scale
    elif 'scale_factor' in props:
        kwargs['scale_factor'] = scale

    result = operator(**kwargs)
    if isinstance(result, set) and 'FINISHED' not in result:
        raise RuntimeError(f"Exportacion STL cancelada: {result}")


# Función para llevar al suelo (Z=0) todas las mallas de la escena
def ground_all_mesh_objects():
    mesh_objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    if not mesh_objects:
        return

    global_min_z = None
    
    # Encontrar el vértice más bajo en el espacio global de toda la escena
    for obj in mesh_objects:
        matrix = obj.matrix_world
        for vertex in obj.data.vertices:
            global_z = (matrix @ vertex.co).z
            if global_min_z is None or global_z < global_min_z:
                global_min_z = global_z

    # Si el objeto no está en el suelo, desplazarlo verticalmente
    if global_min_z is not None and global_min_z != 0.0:
        for obj in mesh_objects:
            obj.location.z -= global_min_z
        
        # Hacer que el cambio de localización sea nativo en los vértices (Apply Transformation)
        bpy.ops.object.select_all(action='DESELECT')
        for obj in mesh_objects:
            obj.select_set(True)
        if mesh_objects:
            bpy.context.view_layer.objects.active = mesh_objects[0]
            bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)


# Funciones para manejo de objetos más grandes que la cama de la impresora 3D

# Corte destructivo de un objeto utilizando un plano bisector y sellado 
# de aristas para mantener mallas cerradas
# plane_co: Coordenadas del plano
# plane_no: Normal del plano
# clear_outer: Si True, elimina la parte del objeto que queda fuera del plano
# clear_inner: Si True, elimina la parte del objeto que queda dentro del plano
def bisect_object(obj, plane_co, plane_no, clear_outer, clear_inner):

    bpy.ops.object.mode_set(mode='OBJECT')
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    # Transformación del plano del espacio global al espacio de objeto
    matrix_inv = obj.matrix_world.inverted()
    local_plane_co = matrix_inv @ plane_co
    local_plane_no = (matrix_inv.to_3x3().transposed() @ plane_no).normalized()

    geom = bm.verts[:] + bm.edges[:] + bm.faces[:]

    # Operador topológico de bisección
    bmesh.ops.bisect(
        bm,
        geom=geom,
        plane_co=local_plane_co,
        plane_no=local_plane_no,
        clear_outer=clear_outer,
        clear_inner=clear_inner,
        use_snap_mesh=False,
    )

    # Capping (localización de aristas abiertas tras el corte y rellenarlas)
    open_edges = [e for e in bm.edges if not e.is_manifold]
    if open_edges:
        try:
            bmesh.ops.edgeloop_fill(bm, edges=open_edges)
        except Exception:
            pass # Si la topología es demasiado compleja

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


# Función para cortar mallas secuencialmente en X e Y hasta que
# todas las piezas individuales quepan en las dimensiones de la cama
def auto_slice_objects_for_printing(max_size_cm=22.0, needs_millimeters_fix=False):
    scene = bpy.context.scene
    scale_factor = 0.1 if needs_millimeters_fix else (scene.unit_settings.scale_length * 100.0)
    max_size_bu = max_size_cm / scale_factor

    # Procesamiento de ejes X (0) e Y (1) consecutivamente
    for axis_idx in [0, 1]:
        plane_no = mathutils.Vector((1.0 if axis_idx == 0 else 0.0, 1.0 if axis_idx == 1 else 0.0, 0.0))

        objects_to_process = [obj for obj in scene.objects if obj.type == 'MESH']

        while objects_to_process:
            obj = objects_to_process.pop(0)

            # Consolidación de transformaciones para lectura de la bbox en espacio global
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

            bbox = obj.bound_box
            min_val = min(v[axis_idx] for v in bbox)
            max_val = max(v[axis_idx] for v in bbox)
            size = max_val - min_val

            # Tolerancia de punto flotante para evitar cortes innecesarios
            if size > (max_size_bu + 1e-4):
                cut_val = min_val + max_size_bu
                plane_co = mathutils.Vector((0.0, 0.0, 0.0))
                plane_co[axis_idx] = cut_val

                # Duplicación del objeto para preservar la otra sección
                bpy.ops.object.select_all(action='DESELECT')
                obj.select_set(True)
                bpy.context.view_layer.objects.active = obj
                bpy.ops.object.duplicate()
                obj_part2 = bpy.context.active_object
                obj_part2.name = f"{obj.name}_part2"

                # Cortes complementarios
                bisect_object(obj, plane_co, plane_no, clear_outer=True, clear_inner=False)
                bisect_object(obj_part2, plane_co, plane_no, clear_outer=False, clear_inner=True)

                # Evaluación y limpieza de remanentes vacíos
                for part in [obj, obj_part2]:
                    if len(part.data.vertices) == 0 or len(part.data.faces) == 0:
                        bpy.ops.object.select_all(action='DESELECT')
                        part.select_set(True)
                        bpy.ops.object.delete()
                    else:
                        # Re-encolar la pieza resultante si aún requiere cortes
                        if part not in objects_to_process:
                            objects_to_process.append(part)



# Función principal con la lógica secuencial del pipeline

def main(args):
    input_ext = os.path.splitext(args.input)[1].lower()
    import_model(args.input)
    submeshes_detail, needs_millimeters_fix = analyze_model_submeshes(args.filename or args.input)
    submesh_count = len(submeshes_detail)

    exported = False
    output_path = None
    export_format = None
    stl_output_path = None

    # Verificación de cumplimiento del límite de submallados
    if args.max_submeshes <= 0 or submesh_count <= args.max_submeshes:
        # Exportación a GLB para visualización web
        output_path, export_format = export_glb(args.output)
        exported = True
        if args.for_3d_printing:
            export_scale = 1.0 if needs_millimeters_fix else 1000.0
            base_output_path = os.path.splitext(args.output)[0]
            stl_output_path = f"{base_output_path}.stl"
            try:
                stl_output_path = os.path.abspath(stl_output_path)

                # Verificación de dimensiones y necesidad de segmentación de pieza
                sys.stdout.write("[Blender] Ejecución de análisis de dimensiones para segmentación automática (Límite: 22x22 cm)... \n")
                print("[Blender] Ejecución de análisis de dimensiones para segmentación automática (Límite: 22x22 cm)... \n", flush=True)
                auto_slice_objects_for_printing(max_size_cm=22.0, needs_millimeters_fix=needs_millimeters_fix)

                ground_all_mesh_objects()
                export_stl(stl_output_path, scale=export_scale)
                sys.stdout.write(f"[Blender] Gemelo STL generado con éxito en: {stl_output_path}\n")
                print(f"[Blender] Gemelo STL generado con éxito en: {stl_output_path}\n", flush=True)

                # Actualización de datos para reporte final JSON
                submeshes_detail, needs_millimeters_fix = analyze_model_submeshes(args.filename or args.input)
                submesh_count = len(submeshes_detail)
            
            except Exception as e:
                sys.stderr.write(f"[Blender] Error exportando STL para laminación: {str(e)}\n")
                print(f"[Blender] Error exportando STL para laminación: {str(e)}\n", flush=True)
                stl_output_path = None


    # Datos de bounding box global enlazados al espacio métrico local
    
    global_corners = []
    for obj in bpy.context.scene.objects:
        if obj.type == 'MESH':
            loc, rot, _ = obj.matrix_world.decompose()
            # Nueva matriz de transformación con escala neutra (1.0, 1.0, 1.0)
            matrix_no_scale = mathutils.Matrix.Translation(loc) @ rot.to_matrix().to_4x4()
            # Esquinas multiplicadas por la matriz sin escala deformada del importador
            for corner in obj.bound_box:
                global_corners.append(matrix_no_scale @ mathutils.Vector(corner))

    if global_corners:
        # Tamaño bruto en el espacio tridimensional uniforme
        raw_x = max(p.x for p in global_corners) - min(p.x for p in global_corners)
        raw_y = max(p.y for p in global_corners) - min(p.y for p in global_corners)
        raw_z = max(p.z for p in global_corners) - min(p.z for p in global_corners)

        # Factor de conversión a centímetros
        factor = 0.1 if needs_millimeters_fix else (bpy.context.scene.unit_settings.scale_length * 100.0)

        global_bbox = {
            'x': round(raw_x * factor, 2),
            'y': round(raw_y * factor, 2),
            'z': round(raw_z * factor, 2)
        }
    else:
        global_bbox = {'x': 0, 'y': 0, 'z': 0}


    # Generación del reporte final de sincronización de datos con Django
    write_report(args.report, {
        'global_bbox': global_bbox,
        'submesh_count': submesh_count,
        'submeshes': submeshes_detail,
        'exported': exported,
        'output_path': output_path if exported else None,
        'export_format': export_format if exported else None,
        'stl_output_path': stl_output_path if exported else None,
        'needs_millimeters_fix': needs_millimeters_fix,
        'stl_scale': 1.0 if needs_millimeters_fix else 1000.0,
    })


if __name__ == '__main__':
    # Recuperación y parseo de argumentos de línea de comandos (CLI)
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--max-submeshes', type=int, default=10)
    parser.add_argument('--filename', default='')
    parser.add_argument('--for-3d-printing', action='store_true')

    # Pase de argumentos a Blender de '--' para evitar conflictos con sus propios parámetros de ejecución
    parsed_args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])

    try:
        main(parsed_args)
    except Exception as exc:
        # Captura de excepciones imprevistas
        write_report(parsed_args.report, {
            'submesh_count': 0,
            'exported': False,
            'error': str(exc),
            'traceback': traceback.format_exc(),
        })
        raise
