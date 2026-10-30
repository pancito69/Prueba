# Validación del piloto

## Comprobado

- **68 pruebas automatizadas aprobadas** con `unittest`: cálculo en gramos/cm,
  divisor 5000, cada límite de talla, huecos, entradas inválidas, conservación de
  precisión decimal, CSV/XLSX, cargas idénticas, duplicados, confianza explícita,
  coincidencia de presentación/empaque, fuentes contradictorias, propuestas,
  rechazo, aprobación, exportación, vigencia y revocación de referencias.
  La versión 2 agrega diagnóstico y CSV por motivos, dirección de comparación,
  cobro declarado sin equivalencias supuestas, precisión, umbral configurable,
  observación de longitud, decisiones agrupadas atómicas y migración real desde
  un esquema v1 preservando originales, aprobaciones y exportación.
  La versión 3 añade reglas por categoría sin imputación, subcategoría/presentación,
  deshabilitado versionado, migración v2 auténtica, investigación por producto,
  evidencia por campo, conversión de unidades, mínimos no exportables, abstención,
  contradicciones históricas, reimportaciones, snapshots obsoletos y reintentos
  auditados que respetan aprobaciones y rechazos previos.
  Nueve pruebas adicionales verifican arranque: exclusión entre procesos,
  liberación al fallar, SQLite ocupado sin pérdida de datos, migración con progreso
  que no se repite al reiniciar, rechazo de puerto ocupado y comprobación de la
  instancia propia antes de anunciar la URL.
- Navegador Chromium: demo, importación CSV con mapeo, propuesta manual,
  aprobación M → S, descarga CSV, reimportación sin duplicados, reglas y vista
  móvil de 390 px. Sin errores JavaScript observados.
  En v2: demo de 16 registros, filtro de subidas S → M, diagnóstico/exportación,
  aprobación de 2 propuestas con evidencia compartida, cambio de umbral que
  invalida aprobaciones, rack de 350 cm con observación sin error automático y
  mapeo de talla de cobro actual sin confundirla con la clase anterior del Excel.
  En v3: carga con categorías, regla manual, exportación de casos/instrucciones,
  importación completa y parcial, revisión humana del mínimo, texto CSV pegado,
  acumulación de campos posteriores y conservación de aprobaciones al editar
  una regla de plausibilidad. Las capturas contienen datos ficticios.
- Instancia limpia iniciada: interfaz, JavaScript, API de salud y resumen
  respondieron HTTP 200. El catálogo operativo se dejó vacío.
  El iniciador actualizado se probó con servidor real y una base temporal: la
  dirección se anunció tras HTTP 200 de la instancia correcta; la interfaz respondió
  y el cierre liberó el proceso. Los bloqueos entre procesos se ejecutaron en Linux;
  la rama `msvcrt` de Windows usa su bloqueo nativo y no se ejecutó en esta máquina.

- Migración aislada desde v2 auténtica: **400.000 registros sintéticos**, respaldo
  de 260.411.392 bytes y **400 aprobaciones conservadas**. La migración completó
  en 25,29 s sin bloqueo. Esto no identifica al proceso que bloqueó la base del
  equipo del usuario; su traza falló antes de migrar, en el esquema inicial.
- Carga sintética aislada mediante `python scripts/benchmark.py --rows 400000`:
  **400.000 procesados, 400.000 persistidos, 400.000 clasificados S, 0 errores**.
  En v3 se evaluaron **100 reglas ficticias de categoría** durante la carga.
  Tiempo medido de importación/cálculo: **37,22 segundos** en esta máquina.
  Consultas posteriores sobre esa base sintética: resumen **3,58 s**, diagnóstico
  **4,48 s** y primera página **3,03 s**. Son mediciones locales, sin investigación
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
No se conectaron proveedores de IA, sistemas de catálogo externos ni facturación.
El flujo de investigación exporta archivos para uso manual en un chat; importa
declaraciones de evidencia, no certifica la existencia o veracidad de las fuentes.
No hay
autenticación ni roles: es un piloto para evaluación local.
