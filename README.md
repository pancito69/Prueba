# Talla · Control logístico

Piloto funcional para cargar un catálogo, calcular tallas, detectar diferencias,
registrar evidencia, aprobar correcciones y exportarlas. Interfaz en español.

## Versión 2: diagnóstico, prioridad y revisión por grupos

- **Diagnóstico** desglosa casos por motivo, exporta el detalle y separa el cálculo
  del estado de la evidencia. "Calculados sin alertas" no significa medidas
  verificadas. Una fila puede tener varios motivos: no sumes esos conteos como
  SKUs únicos. Los grupos de investigación exigen identidad explícita.
- **Dirección** permite priorizar subidas, bajadas, misma talla o sin base. Con
  `billed_size` válido se compara la talla de cobro declarada con la propuesta
  vigente o, si falta, con el cálculo del catálogo. Sin talla declarada, solo una
  propuesta permite comparar con el cálculo original. Sin ambas bases queda
  desconocida: no se estima una "talla real" ni una pérdida monetaria.
- `billed_size` es opcional y debe usar exactamente la tabla de cooperados. No se
  reconoce como equivalente la columna de clase logística antigua del Excel.
  Un valor no reconocido genera un motivo explícito; no se adivina su equivalencia.
- Una dimensión **>300 cm es una observación**, no una alerta de error por sí sola.
  Cortinas, racks y otros productos pueden ser largos. Siempre verificar las
  medidas del paquete despachado, no las del producto desplegado. No se infieren
  excepciones por palabras del título ni se inventan rangos por categoría.
- La relación volumétrico/real es una **heurística configurable** en Reglas,
  inicialmente 100. Sirve para investigar, no demuestra que las medidas estén mal.
  Cambiar su umbral recalcula y deja obsoletas propuestas y aprobaciones anteriores.
- **Revisiones → Por grupo** reúne propuestas pendientes con igual identidad,
  evidencia, valores, referencia y comparación. Puedes seleccionar hasta 500 del
  mismo grupo por decisión. Se conserva auditoría individual; el lote es atómico:
  una propuesta obsoleta impide aplicar toda esa selección. Los registros sin
  identidad suficiente siguen individuales. Una alerta sin propuesta no se
  convierte automáticamente en un grupo aprobable.
- **IA y costos** informa cuántos productos son identificables y cuántas filas
  carecen de evidencia. IA externa sigue desconectada: cero consultas del piloto.

### Actualizar conservando tu catálogo

1. Detén la versión anterior con Ctrl+C y haz una copia de la carpeta `data/`.
2. Descarga el nuevo código de la misma rama y reemplaza los archivos de la
   aplicación, **conservando `data/` y `.venv/`**. No necesitas volver a importar.
3. Ejecuta `iniciar.bat` o `python iniciar.py` de nuevo. La primera apertura
   actualiza los motivos y puede tardar con un catálogo grande. Conserva datos
   originales, decisiones y hashes equivalentes; crea `data/talla.pre-v2.sqlite3`
   antes de la migración si había SKUs. Esta copia también contiene datos privados.
4. Abre Diagnóstico y revisa el desglose de la base actualizada. Los conteos pueden
   cambiar porque longitud >300 cm ya no obliga por sí sola a revisar.

El diagnóstico del catálogo de 646.396 SKUs ejecutado en tu equipo **no está en
esta máquina**. Los cambios se validaron con datos ficticios y migraciones de
prueba; no se afirma haber recalculado ni verificado esas medidas reales.

### Opciones para incorporar IA después

| Opción | Costos y alcance |
|---|---|
| Reglas y referencias reutilizadas | Sin consultas a proveedores de IA; usa tu equipo y evidencia disponible. |
| Investigación asistida con tu cuenta de chat | Trabajo fuera de la herramienta, sujeto a límites del plan; registrar fuentes comprobadas y medidas del empaque. |
| API de un proveedor | Cuenta y facturación propias; posible costo de modelo, búsqueda y extracción. Una suscripción de ChatGPT no incluye saldo API. |
| Modelo local | Puede evitar tarifas por consulta, pero requiere hardware, memoria y mantenimiento; la búsqueda y calidad de las fuentes siguen siendo necesarias. |

La integración automática aún no existe. La estrategia recomendada es identificar
productos únicos, reutilizar referencias compatibles y guardar resultados antes
de investigar nuevos casos. Después activar un piloto acotado con presupuesto y
límite de solicitudes acordados, control de reintentos y prioridad en posibles
subidas. No hay una estimación monetaria responsable sin conocer cuántos productos
necesitan investigación, el proveedor y los límites elegidos.

**El catálogo real `Seller_Activos (2).xlsx` no fue procesado:** el adjunto supera
el límite de transferencia de la sesión. La aplicación permite cargarlo desde
tu equipo (hasta 512 MiB). Los datos del botón **Probar demo** son ficticios.

## Ejecutar

Requiere Python 3.12. **Inicio sencillo:** descomprime el proyecto, abre una
terminal en la carpeta y ejecuta `python iniciar.py`. En Windows puedes abrir
`iniciar.bat` con doble clic si instalaste Python con su lanzador `py`.
La primera ejecución instala las dependencias desde Internet en `.venv`.
Abre la dirección indicada y pulsa **Probar demo** o **Cargar catálogo**.

Instalación manual equivalente, desde esta carpeta:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

En Windows activa el entorno con `.venv\Scripts\activate`. Abre en tu navegador
la dirección local que muestra Uvicorn (puerto 8000).

En el entorno preparado las dependencias ya están disponibles; puedes ejecutar
directamente el último comando. Usa un solo proceso Uvicorn (`--workers 1`):
el piloto tiene una cola de trabajo local. Cada tarea en la nube ya está aislada;
usa este checkout existente, sin crear worktrees adicionales.

## Primer recorrido

1. Pulsa **Probar demo** para cargar 16 registros ficticios, incluyendo una
   diferencia M → S sustentada en una referencia ficticia declarada verificada.
2. Abre **Revisiones** y selecciona la cafetera del seller Mercado Centro.
3. Compara medidas y fuente. La referencia usa `example.com`, **no es evidencia
   real ni se consultó en Internet**. Aprueba solo para probar el flujo demo.
4. Exporta las correcciones aprobadas y comprueba que el CSV contiene esa fila.
5. Para datos reales, pulsa **Cargar catálogo**, selecciona CSV o XLSX, confirma
   el mapeo de columnas y ejecuta el análisis.
6. Usa la bandeja para registrar propuestas con evidencia del mismo producto,
   presentación y paquete despachado. Aprobarlas solo habilita su exportación;
   el piloto **no escribe en tu catálogo externo ni modifica cobros**.

Para separar demo de operación, inicia otra instancia con un directorio vacío:

```bash
TALLA_DATA_DIR=/ruta/a/datos-reales python -m uvicorn app:app --host 127.0.0.1 --port 8000
```

## Columnas y referencias

La interfaz permite mapear nombres propios del catálogo. También se puede
descargar una plantilla. Campos obligatorios: `seller`, `sku`, `weight_g`,
`height_cm`, `length_cm`, `width_cm`. Peso en **gramos**, dimensiones en **cm**.
Valores faltantes o inválidos quedan pendientes; no se sustituyen por cero.
Seller + SKU identifica la fila; si la presentación cambia debe informarse.

Opcionales: `name`, `gtin`, `brand`, `model`, `variant`, `presentation`,
`packaging`, `reference_verified`, `source_url`, `evidence_kind`.

Para contrastar otros sellers se exige:

- GTIN válido (con dígito de control) o marca + modelo; nombres parecidos no bastan.
- Variante, presentación y empaque explícitos e iguales. `Única` sirve como
  variante cuando efectivamente no existen variantes. No rellenar con supuestos.
- Referencia marcada `true` en `reference_verified`, URL http(s) y
  `evidence_kind` = `empaque_envio` o `medicion_logistica`.
- Confirmación expresa del operador al importar referencias como verificadas.
- Valores calculables y sin alertas de plausibilidad. Referencias contradictorias
  se retienen aunque una mayoría coincida. Un consenso no verifica datos.

Las referencias son **declaradas por el operador**, no certificadas por la app.
Su URL se registra pero no se descarga. Todas las propuestas requieren aprobación.
No existe búsqueda externa/IA conectada en esta versión. La investigación y su
validación se realizan fuera de la herramienta y la evidencia se registra en ella.

Una ficha de medidas del producto no respalda medidas del envío. Verifica
explícitamente la misma presentación y el mismo empaque antes de aprobar.

## Reglas exactas

```text
peso_real_kg = weight_g / 1000
peso_volumetrico_kg = height_cm * length_cm * width_cm / 5000
peso_clasificacion = max(peso_real_kg, peso_volumetrico_kg)
```

Se utiliza `Decimal`, sin redondear antes de clasificar. La clase original del
Excel no se utiliza ni se mapea por nombre.

Por defecto se respeta **literalmente** la tabla proporcionada:

| Desde kg | Hasta kg | Talla |
|---:|---:|---|
| 0 | 0,5 | XXXS |
| 0,51 | 1 | XXS |
| 1,1 | 3 | XS |
| 3,1 | 8 | S |
| 8,1 | 25 | M |
| 25,1 | 40 | L |
| 40,1 | 50 | XL |
| 50,1 | 260 | XXL |
| 260,1 | 300 | XXXL |
| 300,1 | 500 | XXXXL |

Los huecos (ej. 1,05 kg), datos físicos no positivos y pesos >500 kg quedan
pendientes. **Reglas** permite elegir explícitamente límites superiores continuos;
esa decisión debe acordarse con negocio antes del cobro. Cambiar reglas recalcula
el catálogo e invalida propuestas/aprobaciones anteriores. Los cálculos no
constituyen una tarifa ni contemplan multibultos, recargos o cobros históricos.

## Cargas recurrentes y persistencia

- CSV iterado y XLSX en modo de lectura por filas, primera hoja. Encabezados en
  primera fila; XLSX con fórmulas necesita valores guardados por Excel. GTIN/SKU
  deben guardarse como texto para preservar ceros iniciales.
  XLSX puede cargar en memoria su tabla interna de textos compartidos; para
  archivos muy grandes se recomienda CSV. El benchmark de 400.000 filas verifica
  CSV sintético, no la memoria o estructura del Excel real.
- Se admiten coma o punto decimal sin separadores de miles; CSV con coma decimal
  requiere campos entre comillas o delimitador punto y coma. Un `1,200` significa
  **1,2**, no 1200. Se informa esta convención en el mapeo de carga.
- Importación en segundo plano, transacciones por 1000 filas, paginación en servidor
  y SQLite con WAL. Los archivos originales se conservan en `data/uploads`.
- Hash de datos relevantes para omitir filas sin cambios. Fila repetida dentro de
  un archivo se informa como error; no sobrescribe silenciosamente a la primera.
- Una actualización reemplaza datos de esa clave e invalida sus propuestas.
  Reenviar una carga idéntica conserva los resultados y las aprobaciones.
  Las cargas deben enviarse en orden: este piloto no dispone de versiones o fechas
  de origen para distinguir un archivo antiguo de uno nuevo diferente.
- Si una referencia cambia o contradice otra verificada, sus propuestas dejan de
  ser exportables. Las correcciones efectivas conservan los valores originales.
  Corregir una referencia revoca el uso de sus datos originales para otros sellers.
  Los datos corregidos necesitan una nueva validación antes de ser otra referencia.
- Las filas ausentes en una nueva carga no se eliminan automáticamente.
- Un reinicio marca trabajos interrumpidos como fallidos. Vuelve a cargar o
  reprocesar el archivo: los bloques confirmados permanecen y se omiten al repetir.
- `data/talla.sqlite3`, archivos de carga y sus copias contienen datos comerciales.
  Para respaldo consistente usa la API backup de SQLite; conserva también uploads.

## Validación

```bash
python -m unittest discover -s tests -v
```

Las pruebas requieren `httpx==0.28.1` (`python -m pip install -r requirements-dev.txt`).
Incluyen reglas, importación, evidencia, propuestas, aprobación, exportación e
invalidación. Para generar y ejecutar una carga sintética aislada:

```bash
python scripts/benchmark.py --rows 400000
```

El benchmark usa datos ficticios y un directorio temporal, no el catálogo operativo.
Su duración describe la máquina actual, no garantiza un SLA de producción.

## Alcance del piloto

Funciona localmente y tiene persistencia real, pero **no tiene autenticación ni
roles**. No lo publiques en Internet ni lo uses como aprobación multiusuario de
producción. Para la puesta en operación hacen falta autenticación/roles, permisos,
respaldo y retención acordados, fuentes confiables, revisión del piloto con datos
reales y el formato/API del sistema destino. El motor de referencias y la cola
local pueden migrarse a PostgreSQL y trabajadores externos según la concurrencia.

No se enviaron datos del catálogo a proveedores de IA. La futura conexión de
investigación externa requiere definir fuentes, presupuesto y tratamiento de datos;
no se simula como una capacidad ya implementada.
