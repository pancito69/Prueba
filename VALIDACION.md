# Validación del piloto

## Comprobado

- **23 pruebas automatizadas aprobadas** con `unittest`: cálculo en gramos/cm,
  divisor 5000, cada límite de talla, huecos, entradas inválidas, conservación de
  precisión decimal, CSV/XLSX, cargas idénticas, duplicados, confianza explícita,
  coincidencia de presentación/empaque, fuentes contradictorias, propuestas,
  rechazo, aprobación, exportación, vigencia y revocación de referencias.
- Navegador Chromium: demo, importación CSV con mapeo, propuesta manual,
  aprobación M → S, descarga CSV, reimportación sin duplicados, reglas y vista
  móvil de 390 px. Sin errores JavaScript observados.
- Instancia limpia iniciada: interfaz, JavaScript, API de salud y resumen
  respondieron HTTP 200. El catálogo operativo se dejó vacío.
- Carga sintética aislada mediante `python scripts/benchmark.py --rows 400000`:
  **400.000 procesados, 400.000 persistidos, 400.000 clasificados S, 0 errores**.
  Tiempo medido de importación/cálculo: **25,54 segundos** en esta máquina.

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
