-- OLAP

-- Limpieza de Fact
TRUNCATE TABLE teg_olap.fact_costestimation RESTART IDENTITY;

-- Limpieza de DimSubmeshVersion
TRUNCATE TABLE teg_olap.dimsubmeshversion RESTART IDENTITY CASCADE;


-- OLTP

-- Limpieza de asignación de materiales
TRUNCATE TABLE teg_oltp.materialassignment RESTART IDENTITY;

-- Limpieza de submallados
TRUNCATE TABLE teg_oltp.submesh RESTART IDENTITY CASCADE;

-- Limpieza de versiones
TRUNCATE TABLE teg_oltp.projectversion RESTART IDENTITY CASCADE;

-- Limpieza de proyectos
TRUNCATE TABLE teg_oltp.project RESTART IDENTITY CASCADE;