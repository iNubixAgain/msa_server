# Tablas maestras y procedencia de datos

Guía para la base pública revisada. Las tablas del cliente 7.13.0 son artefactos binarios;
no se modifican como JSON directamente. Los publicadores/editor de estadísticas
de otras ramas no forman parte de esta revisión.

## Descarga

1. `/title/get_file_list` lee `server_content/file_list.json` y devuelve
   catálogo y `master_ver` (`MSA_MASTER_VERSION` al arrancar).
2. Cada entrada describe URL, file_name, hash, file_size y destino.
3. `/title/get_master_table?table[]=NOMBRE` entrega la tabla correspondiente
   en Base64 dentro del contrato JSON. La representación fuente usa archivos
   bajo `server_content/files/`, incluido el nombre canónico por tabla.
4. Las rutas el prefijo de descargas definido en `msa_server/assets.py` y el prefijo legado sirven archivos
   según su nombre. `/title/get_pack_info` anuncia dónde descargarlos.

Revisar la URL exacta del catálogo y los artefactos que sirven esas rutas;
los nombres canónicos y los de contenido/cache no siempre son intercambiables.
Cambiar bytes sin actualizar tamaño y hash puede rechazar toda la descarga.
Cambiar solo el número de versión no genera artefactos nuevos.

El cliente puede conservar tablas en caché o interceptar descargas mediante
su propio OfflineServer. Login remoto no demuestra que use las tablas del VPS.
Verifica procedencia y la versión realmente descargada en la instalación
afectada. No borres cuentas para diagnosticar una descarga antigua.

## Familias de unidades

| Tabla | Uso |
| --- | --- |
| m_unit | Definición general, rareza e ítem de evolución |
| m_unit_misc | Recursos visuales y mapeo menu_unit/unit_info |
| m_unit_usable | start_time, disponibilidad y grupo de aparición |
| m_unit_info | Datos de rol y ataques |
| m_unit_ver / m_unit_slot | Versiones y slots compatibles |
| m_unit_level_status_* / m_unit_evol_status_* | Estadísticas por nivel/evolución |
| m_status_seed, m_status_seed_level, m_status_seed_param | Mejoras y parámetros de status_seed |
| m_string_unit_name / m_string_unit_info | Nombres y descripciones localizadas |

Una ID poseída no garantiza su aparición en el selector: intervienen fecha,
metadatos, mapeos visuales y compatibilidad nativa. Una variante interna puede
tener una fecha futura y carecer de metadatos coleccionables. No uses un ID
mínimo/máximo como sustituto del historial de lanzamiento.

## Modificación y publicación

Trabaja sobre una copia. Necesitas un codificador compatible con la versión
exacta del cliente; no introduzcas un formato o clave aleatorios. Conserva las
filas/referencias que esperan los accesores nativos y valida el resultado
descifrado después de generarlo.

Publica en conjunto tabla, nombres de descarga/cache, catálogo, hash, tamaño
y revisión. Guarda los originales para recuperación y reinicia el backend
cuando corresponda para descartar cachés. Prueba primero una instalación
existente y otra nueva. Recuperar una tabla anterior puede requerir una
revisión mayor que la ya instalada, aunque el contenido vuelva a ser viejo.

En main no existe una política de año máximo: una implementación completa
necesita también filtrar inventarios, mazos persistidos y batallas. La variante
privada hasta la unidad de referencia se explica en [políticas](versiones-y-politicas.md), sin
presentarla como código incluido en este clon.

## Temporadas y eventos

El backend y el cliente usan tablas de temporada, horarios y eventos además
de respuestas dinámicas del menú. No reactives todas las fechas ni alteres
el reloj global para habilitar un modo: puede activar contenido incompatible.
Los parches nativos de menú pueden ignorar un horario aunque las tablas estén
expiradas; bloquear el API y ocultar un botón son trabajos distintos.

Consulta [endpoints](Telemetrización.md) para el contrato histórico de
modo táctico y [estado](estado.md) para la validación pendiente. No se
certifica una batalla real por una respuesta HTTP exitosa.
