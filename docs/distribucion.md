# Alcance de la distribución

La presentación de este proyecto utiliza nombres genéricos de servidor y
cliente Android. No se presenta como producto oficial ni como un paquete
completo de una aplicación comercial.

La edición de nombres, comentarios o documentación no demuestra autoría ni
otorga permisos de distribución. El mantenedor declara contar con permiso para redistribuir los componentes
incluidos. Esa declaración no transfiere al proyecto la autoría de terceros
ni constituye una auditoría legal independiente.

## Inventario que requiere comprobar procedencia

| Material | Situación de la revisión |
| --- | --- |
| Código Python del backend | Se revisaron menciones explícitas; no se acreditó una cadena completa de autoría/licencias |
| server_content/files y respuestas estáticas | Material incluido bajo el permiso de redistribución declarado por el mantenedor; conservar condiciones y atribuciones aplicables |
| online_runtime/luxon_server.exe | Ejecutable de terceros conservado bajo el permiso declarado por el mantenedor; no se atribuye su autoría al servidor |
| server_defaults | Datos iniciales; revisar su procedencia junto con las tablas |
| APK, recursos gráficos/audio, árbol decompilado y herramientas privadas | No forman parte del cambio publicado |

No se eliminaron avisos de licencia ni atribuciones para aparentar otra
procedencia. El mantenedor debe conservar la documentación de esos permisos y cumplir
las condiciones de cada componente. Esta publicación conserva el runtime y
las tablas por su declaración explícita de permiso de redistribución.

## Compatibilidad técnica

Hay identificadores, rutas HTTP y nombres de campos fijados por el protocolo
del cliente. Se conservan cuando son necesarios para compatibilidad. Cambiar
una URI a un nombre genérico sin modificar el cliente rompería descargas.
Esos valores técnicos no se utilizan como marca en las guías operativas.

La bienvenida genérica se usa cuando el servidor crea noticias por defecto.
No se reescriben noticias, mensajes o logs históricos de operadores/jugadores.
Si ya existe una noticia guardada, el operador debe revisar su texto desde
su administración habitual; cambiar el valor por defecto no modifica ese dato.

Esta publicación comienza con un único commit inicial. Las referencias
antiguas de Pull Requests en GitHub y las copias descargadas por terceros
pueden conservar versiones anteriores. El respaldo histórico del mantenedor
se conserva de forma privada y no forma parte de esta publicación.
