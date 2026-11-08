# Alcance de la distribución

La presentación de este proyecto utiliza nombres genéricos de servidor y
cliente Android. No se presenta como producto oficial ni como un paquete
completo de una aplicación comercial.

La edición de nombres, comentarios o documentación no demuestra autoría ni
otorga permisos de distribución. Esta revisión no certifica que todos los
archivos estén libres de derechos de terceros.

## Inventario que requiere comprobar procedencia

| Material | Situación de la revisión |
| --- | --- |
| Código Python del backend | Se revisaron menciones explícitas; no se acreditó una cadena completa de autoría/licencias |
| server_content/files y respuestas estáticas | Contenido binario y datos de protocolo ya versionados; permisos de redistribución no acreditados en esta revisión |
| online_runtime/luxon_server.exe | Ejecutable de terceros; confirmar origen, licencia y condiciones antes de redistribuir |
| server_defaults | Datos iniciales; revisar su procedencia junto con las tablas |
| APK, recursos gráficos/audio, árbol decompilado y herramientas privadas | No forman parte del cambio publicado |

No se eliminaron avisos de licencia ni atribuciones para aparentar otra
procedencia. Antes de distribuir binarios o contenidos de terceros, el
mantenedor debe documentar los permisos aplicables o preparar una distribución
de código que requiera que el operador aporte esos componentes por separado.
Esta rama no retira componentes de runtime existentes porque hacerlo cambia
el procedimiento de instalación y necesita una entrega específica.

## Compatibilidad técnica

Hay identificadores, rutas HTTP y nombres de campos fijados por el protocolo
del cliente. Se conservan cuando son necesarios para compatibilidad. Cambiar
una URI a un nombre genérico sin modificar el cliente rompería descargas.
Esos valores técnicos no se utilizan como marca en las guías operativas.

La bienvenida genérica se usa cuando el servidor crea noticias por defecto.
No se reescriben noticias, mensajes o logs históricos de operadores/jugadores.
Si ya existe una noticia guardada, el operador debe revisar su texto desde
su administración habitual; cambiar el valor por defecto no modifica ese dato.

Git conserva versiones anteriores. Los cambios de esta rama no eliminan
contenidos del historial ni de clones, forks o versiones ya publicadas.
Una limpieza de historial requiere un plan separado con inventario y
coordinación; no se hace mediante un push forzado implícito.
