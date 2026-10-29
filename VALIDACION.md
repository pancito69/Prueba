# Validación del piloto

## Comprobado

- **34 pruebas automatizadas aprobadas** con `unittest`: cálculo en gramos/cm,
  divisor 5000, cada límite de talla, huecos, entradas inválidas, conservación de
  precisión decimal, CSV/XLSX, cargas idénticas, duplicados, confianza explícita,
  coincidencia de presentación/empaque, fuentes contradictorias, propuestas,
  rechazo, aprobación, exportación, vigencia y revocación de referencias.
  La versión 2 agrega diagnóstico y CSV por motivos, dirección de comparación,
  cobro declarado sin equivalencias supuestas, precisión, umbral configurable,
  observación de longitud, decisiones agrupadas atómicas y migración real desde
  un esquema v1 preservando originales, aprobaciones y exportación.
- Navegador Chromium: demo, importación CSV con mapeo, propuesta manual,
  aprobación M → S, descarga CSV, reimportación sin duplicados, reglas y vista
  móvil de 390 px. Sin errores JavaScript observados.
  En v2: demo de 16 registros, filtro de subidas S → M, diagnóstico/exportación,
  aprobación de 2 propuestas con evidencia compartida, cambio de umbral que
  invalida aprobaciones, rack de 350 cm con observación sin error automático y
  mapeo de talla de cobro actual sin confundirla con la clase anterior del Excel.
- Instancia limpia iniciada: interfaz, JavaScript, API de salud y resumen
  respondieron HTTP 200. El catálogo operativo se dejó vacío.
- Carga sintética aislada mediante `python scripts/benchmark.py --rows 400000`:
  **400.000 procesados, 400.000 persistidos, 400.000 clasificados S, 0 errores**.
  Tiempo medido de importación/cálculo en v2: **26,79 segundos** en esta máquina.
  Consultas posteriores sobre esa base sintética: resumen **1,55 s**, diagnóstico
  **1,98 s** y primera página **1,81 s**. Son mediciones locales, sin investigación
  de fuentes ni uso de IA externa.

## Límites de esa evidencia

El benchmark utiliza CSV ficticio de seis columnas y medidas homogéneas. No mide
investigación externa, calidad de fuentes, revisión humana ni el rendimiento
del Excel adjunto. No es una promesa de tiempo para catálogos reales.

El archivo `Seller_Activos (2).xlsx` superó el límite de transferencia de la
sesión (32 MiB) y no fue abierto ni procesado. Las capturas muestran datos demo.
La aplicación admite cargas desde el equipo hasta 512 MiB, pero el XLSX real
todavía requiere validación de estructura y memoria.

El iniciador ofrece el mismo arranque documentado en un entorno virtual local;
la interfaz y API se probaron en Linux. El lanzador `.bat` no se ejecutó en Windows.
No se conectaron IA, sistemas de catálogo externos ni facturación. No hay
autenticación ni roles: es un piloto para evaluación local.
