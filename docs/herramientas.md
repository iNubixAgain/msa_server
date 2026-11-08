# Herramientas: qué incluye el repositorio y cómo usarlas

Ejecuta los comandos desde la raíz del servidor. En Windows puedes sustituir
python por `.\.venv\Scripts\python.exe` para usar su entorno virtual.
Esta lista distingue lo distribuido en main de herramientas del workspace
privado; un comando documentado como privado no existe en un clon nuevo.

## Incluidas en GitHub

| Herramienta | Función | Uso y efecto |
| --- | --- | --- |
| server.py | Entrada HTTP; configura instancia única, logs, relay, sesiones y despacho | `python server.py`; abre puertos y mantiene el servicio hasta Ctrl+C |
| start.bat | Arranque Windows con variables y selección de Python de PATH | `cmd /c start.bat`; editar host público y puertos antes de usarlo |
| admin.py | Moderación local por ID público | status/list consultan; ban/unban escriben sanciones; no usa una API remota |
| online_runtime/luxon_server.exe | Relay de sesiones UDP | Lo inicia server.py; requiere config.yml y no se debe duplicar manualmente |
| online_runtime/config.yml | Configuración activa de escucha y destinos anunciados | Se lee al iniciar Luxon; requiere reinicio para aplicar cambios |
| online_runtime/config.example.yml | Referencia ampliada de opciones del runtime | No es un archivo mínimo listo para copiar; revisar servicios web/proxies/puertos |
| tests/ | Pruebas de contratos y utilidades del backend | `python -m unittest discover -s tests -v`; datos temporales, no partida nativa |
| .gitignore | Lista de archivos publicables | Protege materiales locales, secretos y persistencia; no limpia archivos ya versionados |

### Moderación: admin.py

```powershell
python .\admin.py --help
python .\admin.py list
python .\admin.py status msa-ID_DEL_JUGADOR
python .\admin.py ban msa-ID_DEL_JUGADOR --reason 'Motivo verificado'
python .\admin.py unban msa-ID_DEL_JUGADOR --reason 'Revision de la sancion'
```

Usa el ID público exacto de la cuenta, no su IP ni apodo. `--operator 'Nombre'`
identifica al operador en ban/unban. status/list son consultas; ban/unban
modifican server_data/bans.json. No borra perfiles ni inventarios y no cambia
credenciales revocadas. Las sanciones se consultan sin reiniciar, pero una
conexión relay ya iniciada no se expulsa automáticamente. El CLI requiere
acceso al host; no publiques esos comandos como endpoint administrativo.
La descripción completa de límites está en [operación](operacion.md).

Este main no incluye subcomandos `admin.py balance` ni `admin.py stats`.
No copies comandos de otra rama suponiendo que están disponibles aquí.

### Diagnósticos HTTP

| Recurso | Resultado esperado | Límite |
| --- | --- | --- |
| /__offline_status | Estado HTTP, contenido y métricas disponibles del relay | Solo loopback; no acredita que un cliente remoto llegue |
| /__health | 200 si el proceso relay está vivo, 503 si no | Solo loopback; no ejecuta una batalla ni prueba NAT |
| ServerLog.log | Inicio, peticiones, errores y salida relevante de Luxon | Rotación 20 MiB y cinco copias en esta revisión |
| UserLog.log / Nicknames.log | Sesiones y cambios de nombres | Datos privados; revisar solo para el diagnóstico autorizado |
| server_data/requests/ | Capturas POST opcionales | Activar MSA_CAPTURE_HTTP_DIAGNOSTICS=1 temporalmente; no publicar cuerpos ni secretos |

MSA_PHOTON_PACKET_LOGS=1 habilita trazas repetitivas del relay. Guarda esas
variables en el entorno del arranque y reinicia si necesitas cargarlas.
No habilites trazas de paquetes de forma permanente sin medir su volumen.

## Utilidades del sistema que usa el operador

| Comando/herramienta | Para qué sirve |
| --- | --- |
| git clone / status / log / diff | Obtener versión, identificar commit y revisar modificaciones |
| python -m venv | Aislar el intérprete del servidor de otros proyectos |
| ipconfig | Encontrar IP del adaptador activo para una prueba LAN |
| Test-Path | Comprobar que runtime, configuración y datos iniciales existen |
| Invoke-RestMethod | Consultar salud desde el propio host |
| Get-Content -Tail 40 -Wait | Seguir el log en otra terminal |
| Get-Process python,luxon_server | Inspeccionar procesos; no prueba que los teléfonos se conecten |
| Get-NetTCPConnection -LocalPort 42069 | Ver escucha/conexiones HTTP locales |
| Get-NetUDPEndpoint -LocalPort 5055,5056,5058 | Ver sockets UDP locales; no prueba accesibilidad externa |
| New-NetFirewallRule | Crear reglas de entrada; requiere administrador y puertos adecuados |
| Panel de red del VPS/router/túnel | Configurar acceso público y traducciones; no lo modifica server.py |

## Scripts privados para el cliente Android

No se distribuyen en este repo, junto con el APK, los JAR y el árbol Android.
Son herramientas del propietario de un cliente compatible. No son necesarias
para operar un backend cuando el APK ya apunta a sus destinos correctos.

| Herramienta privada | Función | Uso y precaución |
| --- | --- | --- |
| client_endpoint.json | Fuente de direcciones HTTP/NameServer y slots por ABI | Conservar offsets/capacidades verificados; no se genera al clonar main |
| tools/configure_client_endpoint.ps1 | Actualiza HTTP nativo, Smali y política de red desde esa configuración | `-Check` verifica sin actualizar; no parchea NameServer |
| tools/check_client_endpoints.py | Comprueba HTTP y NameServer ARM32/ARM64 y modo del interceptor | Solo lectura; necesita el proyecto y configuración privados |
| tools/patch_photon_nameserver.py | Cambia NameServer UTF-32LE en bibliotecas nativas | Requiere source/host exactos, capacidad suficiente y ambas rutas de biblioteca |
| decompilar_apk.ps1 | Extrae un APK a una carpeta nueva con Apktool | Entrada obligatoria; rechaza sobrescribir una carpeta existente; SoloVerificar no extrae |
| recompilar_apk.ps1 | Valida y recompila el proyecto preparado | SoloVerificar comprueba requisitos; el build genera el APK sin firmar |
| firmar_apk.ps1 | Alinea/firma y verifica con el firmador privado | Entrada/DirectorioSalida configurables; cada salida separada; revisar certificado usado |
| apktool.jar | Motor de decompilación/compilación invocado por los scripts | Java requerido; respetar versión y framework cache compatibles |
| uber-apk-signer.jar | Alineación, firma y verificación | Puede usar clave debug por defecto; conservar la clave necesaria para actualizaciones |
| Android SDK: aapt2 / apksigner / zipalign | Validar paquete/versión, firma y alineación | Build-tools del SDK; los scripts pueden buscarlos en una instalación local |

Para comandos completos, valores de configuración y verificaciones por ABI,
consulta [red y APK](red-y-apk.md). No asumas que una ruta Smali, offset o
capacidad sigue siendo válida en otra compilación del cliente.

### Herramientas de contenido opcionales del workspace privado

| Herramienta | Función |
| --- | --- |
| tools/patch_rank_match_schedule.py | Preparación histórica de horarios/tablas competitivas; cambia contenido, no solo direcciones |
| tools/add_offline_unit_slots.py | Preparación del catálogo/inventario offline de una variante concreta |
| tools/prepare_extra_unit_catalog.py | Instalación/restauración coordinada de una extensión privada de unidades |
| tools/validate_extra_unit_masters.py | Comprueba capacidad, integridad y coherencia de maestros de esa extensión |
| tools/prepare_mostro_ui_assets.py | Prepara recursos visuales para una unidad privada de sustitución |
| tools/prepare_mostro_build_up.py | Prepara inventario y recursos de mejora de esa unidad |
| tools/prepare_mostro_native_menu.py | Ajusta su enumeración y compatibilidad en menús nativos |
| tools/prepare_mostro_action_viewer.py | Prepara el visor de acciones de esa variante |
| tools/analyze_msa_unit_menu_contract.py | Analiza asociaciones de tablas/recursos que necesita el menú |
| tools/validate_mostro_menu.py / tools/validate_msd_menu_units.py | Validaciones específicas de esos recursos/unidades importados |
| editar_balance.bat y editores de balance/estadísticas | Edición de reglas o publicación de stats en ramas que incluyen esos módulos; no disponibles en este main |
| eliminar_gremios.py | Limpieza privada de gremios con simulación/respaldo; no es una tarea de arranque |
| eliminar_buffs.py | Limpieza física opcional de buffs guardados en la variante privada; no necesaria cuando esa variante los neutraliza al responder |
| abrir_puertos_online.bat | Helper privado para reglas Windows de TCP/UDP; requiere elevación y puede reemplazar reglas con su nombre |

Los scripts prepare/patch son mantenimiento de un cliente concreto y pueden
sobrescribir datos o binarios. No son un requisito para tener un servidor
normal. No ejecutes una cadena de preparación antigua como solución a una IP
incorrecta. El modo PrepararContenido del recompilador privado es explícito;
la comprobación de integridad puede rechazar un árbol ya modificado.

## Módulos internos: no son comandos administrativos

| Grupo | Qué hace |
| --- | --- |
| config, clock, lifecycle, diagnostics | Variables, reloj compatible, bloqueo de instancia y escritura de diagnósticos |
| identity, accounts, moderation | Identidades, transferencias y sanciones |
| storage, bootstrap | Persistencia atómica, reparación e inicialización por cliente |
| core, users, profiles, inventory, decks | Login, contratos de usuario, unidades y mazos |
| assets, content, gameplay | Descargas, contenido estático y contratos de escenas |
| matchmaking, photon, sessions | Salas, supervisor del relay y sesiones |
| online, arena, friend_online, guild_online, pvp_history | Participantes, resultados, rankings y modos online |
| battle, battle_tactics, ultimate_mission, gacha, snatch_wars | Controladores de modos; varios son parciales |
| social, guild, notifications, news | Relaciones, gremios, chat, avisos y noticias |

No los ejecutes individualmente esperando otro servicio. server.py conserva
el transporte y despacho; la lógica nueva se mantiene en msa_server/.
Para ampliar comandos, crea un CLI con validación y pruebas, no una edición
manual de cuentas mientras el backend escribe.
