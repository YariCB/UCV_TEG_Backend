-- TIPOS DE MATERIALES
INSERT INTO teg_oltp.materialclassification(name)
VALUES('Filamento'),
('Madera'),
('Metal'),
('Papel'),
('Plástico'),
('Vidrio');


-- DIMENSIONES
INSERT INTO teg_oltp.dimension(
name, calculationmethod, description)
VALUES('Masa', 'CALC_BY_WEIGHT', 'Filamentos y resinas. Calcular (gramos usados / peso base) * costo'),
('Área', 'CALC_BY_AREA', 'Planchas de madera/metal. Calcular (área usada / área base) * costo'),
('Volumen', 'CALC_BY_VOLUME', 'Resinas, líquidos, aceites. Calcular (volumen usado / volumen base) * costo'),
('Longitud', 'CALC_BY_LENGTH', 'Cables, perfiles, tubos. Calcular (largo usado / largo base) * costo'),
('Conteo', 'CALC_BY_UNIT', 'Tornillos, botones, bases. Calcular cantidad * costo'),
('Densidad', 'DATA_ONLY', 'Propiedad del material. Sirve de factor de conversión para filamentos y materiales de volumen.');


-- UNIDADES

-- Inserción de Unidades para Masa (DimensionID 1) -> Base: Gramo (g)
INSERT INTO teg_oltp.units (dimensionid, name, abbreviation, conversionfactor, isbase) VALUES
(1, 'Gramo', 'g', 1, TRUE),
(1, 'Kilogramo', 'kg', 1000, FALSE),
(1, 'Libra', 'lb', 453.592, FALSE),
(1, 'Onza', 'oz', 28.3495, FALSE);
-- Inserción de Unidades para Área (DimensionID 2) -> Base: Metro Cuadrado (m^2)
INSERT INTO teg_oltp.units (dimensionid, name, abbreviation, conversionfactor, isbase) VALUES
(2, 'Metro Cuadrado', 'm^2', 1, TRUE),
(2, 'Centímetro Cuadrado', 'cm^2', 0.0001, FALSE),
(2, 'Milímetro Cuadrado', 'mm^2', 0.000001, FALSE),
(2, 'Pulgada Cuadrada', 'in^2', 0.00064516, FALSE);
-- Inserción de Unidades para Volumen (DimensionID 3) -> Base: Centímetro Cúbico (cm^3) 
INSERT INTO teg_oltp.units (dimensionid, name, abbreviation, conversionfactor, isbase) VALUES
(3, 'Centímetro Cúbico', 'cm^3', 1, TRUE),
(3, 'Metro Cúbico', 'm^3', 1000000, FALSE),
(3, 'Milímetro Cúbico', 'mm^3', 0.001, FALSE);
-- Inserción de Unidades para Longitud (DimensionID 4) -> Base: Metro (m)
INSERT INTO teg_oltp.units (dimensionid, name, abbreviation, conversionfactor, isbase) VALUES
(4, 'Metro', 'm', 1, TRUE),
(4, 'Centímetro', 'cm', 0.01, FALSE),
(4, 'Milímetro', 'mm', 0.001, FALSE),
(4, 'Pulgada', 'in', 0.0254, FALSE),
(4, 'Pie', 'ft', 0.3048, FALSE);
-- Inserción de Unidades para Conteo (DimensionID 5) -> Base: Unidad
INSERT INTO teg_oltp.units (dimensionid, name, abbreviation, conversionfactor, isbase) VALUES
(5, 'Unidad', 'unidad', 1, TRUE);
-- Densidad (Dimensión 6) - Base: g/cm^3
INSERT INTO teg_oltp.units (dimensionid, name, abbreviation, conversionfactor, isbase) VALUES
(6, 'Gramo por Centímetro Cúbico', 'g/cm^3', 1, true),
(6, 'Kilogramo por Metro Cúbico', 'kg/m^3', 0.001, false),
(6, 'Libra por Pie Cúbico', 'lb/ft^3', 0.0160185, false),
(6, 'Onza por Pulgada Cúbica', 'oz/in^3', 1.72999, false);