# Versiones del servidor y políticas de juego

Revisión: 8 de noviembre de 2026. Esta guía separa la capacidad del código de
la política elegida para un despliegue. La documentación pública no debe hacer
que un clon de main parezca contener funciones que solo existen en otro paquete.

| Variante inspeccionada | Alcance |
| --- | --- |
| main, base ddbe6e1c | Backend modular, configuración pública histórica, logging activo y supervisor Windows; no incluye roster.py ni mode_availability.py |
| Workspace local Balance-Test | Cambios sin commit, dirección LAN y módulos/editores adicionales; no se publican por inferencia |
| VPS_BACKUP privado | Roster hasta Nova, controles de modos, neutralización de buffs y corrección de selector; no es una carpeta del repositorio público |

No copies server_data ni el APK al Git para trasladar una variante. Revisa sus
módulos, tablas, catálogo, defaults y pruebas como un cambio propio. La
presencia de una guía no instala esa implementación.

## Objetivo y límites

El servidor recupera los contratos necesarios para utilizar un cliente 7.13.0
compatible y ofrece estado por jugador y salas. No es un emulador completo de
toda la lógica nativa ni una economía plenamente autoritativa. Es posible
implementar un contrato HTTP sin que una escena o batalla funcione todavía.
La evidencia histórica de dispositivo no sustituye la prueba de la versión
que se va a desplegar.

## Variante histórica del VPS

El corte confirmado por el propietario fue **15 de enero de 2020 inclusive**,
hasta Nova (Tácticas de Subyugación), ID 1324; no diciembre de 2020. Incluye las
unidades del mismo día. Se eligió una lista explícita desde las fechas de la
tabla original, no todas las IDs menores que 1324.

El paquete privado mantuvo 871 IDs permitidas y bloqueó las demás entradas,
incluidas variantes internas ya no disponibles. No todas esas IDs equivalen
a 871 unidades coleccionables; siguen las exclusiones internas del cliente.
La disponibilidad de las permitidas se normalizó a 2000 y la de las bloqueadas
a 2037, conservando la lista histórica para que la normalización no cambie el
corte. La revisión 7131002 y nuevos nombres de caché de seis tablas buscan
actualizar instalaciones con selectores incompletos. El reporte de usuarios
que veían solo 2016 no tuvo una causa única confirmada por captura del VPS.

Ese paquete también bloquea Rank Match, Battle Tactics y Extra Ops (United
Front retirado del menú y rutas rechazadas). Los botones forzados por el APK
pueden seguir visibles. Online normal 1v1/2v2 permanece independiente.
Estos bloqueos no están implementados en main ddbe6e1c.

## Buffs y datos existentes

La variante VPS rechaza nuevas mejoras con BUFF Items y neutraliza las
posiciones 4, 5, 7 y 8 de status_seed (tipos 5, 6, 8 y 9) al entregar
inventarios y construir batallas. Conserva las otras mejoras y los datos
originales en disco. No hace falta recorrer miles de cuentas para quitar el
efecto de los buffs; cargar los módulos y reiniciar invalida las cachés.

El script privado eliminar_buffs.py es una opción para limpieza física:
vista previa por defecto, aplicación con servidor detenido, bloqueo de
instancia y copias antes de modificar. No está incluido en este main y no se
debe ejecutar un comando ausente de un clon público. No borrar todo status_seed
ni tablas de buffeo: afectaría otras mejoras o accesores nativos.

## Qué cambia cada carpeta

- server_content: tablas cifradas, archivos y catálogo de descargas. Una edición
  requiere bytes, hash, tamaño, nombre de caché y versión coherentes.
- server_defaults: estado base y fuente de reparación/mezcla. No garantiza por
  sí sola que desaparezcan unidades de todas las cuentas existentes.
- msa_server: políticas y validación de lo permitido, mazos, payloads y batallas.
- server_data: cuentas y persistencia privada; no se sustituye al actualizar.

Una restricción completa necesita selector y validación del backend. Unidades
ocultas pueden seguir en mazos guardados si no se filtran. Cambiar únicamente
la fecha del sistema o borrar inventarios no es una implementación del roster.
La vía sin recompilar requiere que el APK consulte esas tablas del backend;
las copias embebidas en un interceptor local no se actualizan desde el VPS.
