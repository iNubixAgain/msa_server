# Operación, pruebas y publicación

Guías revisadas el 8 de noviembre de 2026 para main `ddbe6e1c`:
[red y APK](red-y-apk.md), [despliegue y respaldo](despliegue.md) y
[políticas por versión](versiones-y-politicas.md). Las direcciones de los
ejemplos se sustituyen por las de cada instalación. El paquete privado del
VPS y los scripts del cliente no se distribuyen en este main.

## Configuración

`start.bat` define y sobrescribe host público y puertos. Para usar variables
del entorno, ejecuta directamente `python server.py`; alternativamente edita
las líneas `set` del archivo antes de usarlo:

| Variable | Predeterminado en Python | Uso |
| --- | --- | --- |
| `MSA_HOST` | `0.0.0.0` | Interfaz HTTP local. |
| `MSA_PORT` | `42069` | Puerto HTTP. |
| `MSA_PUBLIC_HOST` | `online.nubix.playit.plus` | Host anunciado al cliente. |
| `MSA_PUBLIC_PORT` | `51516` | Puerto público anunciado. |
| `MSA_MASTER_VERSION` | `7130017` | Revisión del catálogo de tablas. |
| `MSA_MAX_REQUEST_BODY` | `8388608` | Límite de cuerpo HTTP en bytes. |
| `MSA_SESSION_TIMEOUT` | `120` | Inactividad antes de cerrar una sesión lógica. |
| `MSA_CAPTURE_HTTP_DIAGNOSTICS` | `0` | Guarda capturas POST sanitizadas cuando vale `1`. |
| `MSA_DIAGNOSTIC_QUEUE_SIZE` | `512` | Capacidad de la cola de capturas. |
| `MSA_PHOTON_PACKET_LOGS` | `0` | Habilita temporalmente trazas de paquetes Photon. |

Puertos del despliegue actual:

| Servicio | Protocolo/puerto local |
| --- | --- |
| Backend HTTP | TCP `42069` |
| Photon NameServer | UDP `5058` |
| Photon MasterServer | UDP `5055` |
| Photon GameServer | UDP `5056` |

Los destinos externos de Photon se configuran en
`online_runtime/config.yml`. Los endpoints gratuitos pueden cambiar; no se
deben documentar como permanentes.

## Inicio, salud y detención

```powershell
cmd /c start.bat
Invoke-RestMethod http://127.0.0.1:42069/__health
Get-Process python,luxon_server -ErrorAction SilentlyContinue
```

`/__health` devuelve 200 cuando Photon está vivo y 503 cuando no lo está;
`/__offline_status` informa el estado aunque Photon no esté disponible. Ambos
endpoints rechazan solicitudes remotas. Una segunda instancia no inicia si
existe un proceso válido asociado a `server_data/server.instance.lock`.

Detén el servidor con `Ctrl+C`; el backend drena la cola de diagnósticos y
cierra Luxon. Los cambios de Python, configuración o contenido requieren
reiniciar `start.bat`. No requieren recompilar ningún APK.

## Logs y privacidad

- `ServerLog.log`: HTTP, ciclo de vida, errores y salida relevante de Photon.
- `UserLog.log`: sesiones y batallas usando el ID público `msa-...`.
- `server_data/ChatLog.log`: vista diagnóstica consolidada del chat.
- `Nicknames.log`: cambios de apodo.
- `server_data/requests/`: capturas opcionales de peticiones.

Estos archivos pueden contener datos privados y están excluidos de Git. Las
trazas repetitivas `RaiseEvent`/`DispatchEvent` se filtran salvo que se active
`MSA_PHOTON_PACKET_LOGS=1`.

## Pruebas

```powershell
python -m unittest discover -s tests -v
```

La suite pública usa carpetas temporales. No modifica `server_data/`, no inicia
el servidor de producción y no equivale a validar interfaz o Photon en un
dispositivo real. El 8 de noviembre de 2026 se ejecutó desde una copia limpia
de main: **73 pruebas aprobadas**. Los recuentos de 64/50 en informes anteriores
describen otra revisión y no son el resultado de esta auditoría.

## Traslado de cuenta entre dispositivos

El servidor implementa el contrato de MSA 7.13.0 en `msa_server/accounts.py`:

- `POST /setting/upload_save_data`: formulario con `save_data` Base64. Se
  conserva exactamente el texto recibido, con límite de 4 MiB y validación
  del envoltorio Base64; no se interpreta el formato binario interno.
- `POST /title/takeover`: formulario con `secret_key`; requiere un respaldo
  previo y devuelve `password`. La credencial activa (`terminal_id` o cookie)
  autentica la emisión. Se valida la presencia de `secret_key` sin exigir que
  coincida con formularios anteriores: puede cambiar tras restaurar o emitir
  otra contraseña. Emitir otra contraseña invalida la anterior. Los campos
  `secret_salt`/`secret_verifier` de dispositivos de versiones anteriores ya
  no bloquean la emisión; no es necesario borrar el registro de cuentas.
- `POST /title/transfer`: formulario con `password` y `secret_key`; devuelve
  `terminal_id` nuevo y `save_data`. Aquí `secret_key` es la clave propia de la
  instalación destino, no la clave anterior ni una segunda contraseña. Una
  instalación nueva puede llamar sin `terminal_id`; el servidor lo emite al
  completar el traslado. No se fusiona su progreso anterior.

Estos endpoints no aceptan secretos en la URL. La contraseña tiene 12
caracteres alfanuméricos y es reutilizable sin caducidad. Solo generar otra
para la misma cuenta invalida la anterior. Cada traslado revoca los accesos
anteriores. Durante 10 minutos, el mismo destino y `secret_key` pueden
repetir la última solicitud sin volver a rotar la credencial si se perdió la
respuesta; esa ventana no limita la vigencia de la contraseña.
Las contraseñas existentes aún presentes en el registro también se pueden
reutilizar; no se recuperan las que versiones anteriores ya eliminaron.
Hay un límite de 5 intentos por
minuto por IP y por contraseña existente. La clave
secreta se verifica mediante scrypt con sal; las contraseñas y credenciales de
dispositivo se indexan mediante hashes. Los cuerpos sensibles se excluyen de la captura
diagnóstica y las URLs completas no se escriben en el registro HTTP.

La transferencia conserva el identificador interno, el ID público y los
archivos del jugador. Revoca las credenciales anteriores para las siguientes
peticiones al backend, serializando el cambio con las operaciones en curso.
No desconecta por sí sola una conexión Photon ni impide jugar localmente sin
contactar el backend. Se rechaza si la cuenta figura en una sala online;
como las salas se conservan temporalmente, puede ser necesario esperar hasta
su caducidad de 10 minutos, incluso después de terminar una partida.

El protocolo devuelve HTTP 200 con `error_code`: `10103001` para una
contraseña/clave inválida, `401` para credencial revocada, `400` para
entrada inválida, `409` para respaldo ausente o sala pendiente y `429` para
límite de intentos. El texto y la navegación que muestra el cliente ante esos
errores todavía requieren validación visual.

La migración de cuentas existentes es gradual, al recibir su credencial
habitual. `server_data/accounts.json` es privado y contiene las asociaciones,
revocaciones y material criptográfico; los respaldos se guardan en el
`save_data.json` del jugador. Respaldar y restaurar **todo `server_data/` de
forma coherente**: eliminar o restaurar por separado el registro puede perder
revocaciones. Un registro corrupto falla de forma cerrada. Ejecutar una sola
instancia escritora: los bloqueos son de proceso, no distribuidos. La
compatibilidad con identidades antiguas no autentica retrospectivamente sus
credenciales; antes del primer traslado conserva el modelo de confianza
heredado.

### Transporte y validación pendiente

No usar contraseñas de transferencia sobre HTTP público: quien observe el
tráfico podría capturar también las credenciales de dispositivo. Para TLS
directo configurar `MSA_TLS_CERT` (certificado/cadena PEM) y `MSA_TLS_KEY`
(clave privada PEM) antes de iniciar el servidor. Ambas variables son
obligatorias si se habilita TLS; el mínimo es TLS 1.2. No versionar la clave.
El certificado debe ser confiable para el cliente y corresponder al dominio
configurado en su endpoint. Habilitar TLS en el servidor no reconfigura un
APK existente ni convierte en HTTPS sus URLs HTTP.

Las pruebas automatizadas cubren conservación de identidad/perfil, revocación,
reintentos, concurrencia, expiración, límites, validación y exclusión de
secretos de diagnósticos. Sus respaldos son fixtures opacos, no partidas
nativas restauradas. Queda probar en dos dispositivos MSA 7.13.0 la emisión,
introducción de contraseña, restauración completa, revocación del anterior y
conexión TLS. Si un cliente intercepta estos RPC en su servidor offline, debe
redirigirlos al backend para beneficiarse de la revocación. No se ha compilado
ni instalado un APK como parte de esta implementación.

## Baneos locales de cuentas e instalaciones conocidas

Desde la carpeta del servidor, usar el ID público exacto `msa-...` del jugador:

```powershell
python admin.py status msa-ID_DEL_JUGADOR
python admin.py ban msa-ID_DEL_JUGADOR --reason "Trampas verificadas"
python admin.py list
python admin.py unban msa-ID_DEL_JUGADOR --reason "Revisión de la sanción"
```

`--operator "Nombre"` permite identificar al moderador; por defecto se registra
el usuario del sistema operativo. Solo quien tenga acceso local al servidor
debe ejecutar estos comandos. No hay API pública de administración.

La sanción es permanente hasta `unban`. Bloquea la cuenta y todas sus
credenciales registradas, incluso las antiguas revocadas, comprobando tanto
`terminal_id` como la cookie. Rechaza las siguientes peticiones HTTP y las
transferencias hacia o desde una cuenta bloqueada. No borra progreso ni cambia
la contraseña: al desbanear se recupera el acceso con las credenciales que
seguían vigentes. No se reactivan terminales revocados por una transferencia.
Las salas HTTP pendientes con un participante baneado se retiran en la
siguiente consulta de matchmaking, sin sancionar a sus otros participantes.
Las salas marcadas como iniciadas conservan su caducidad normal.

No se banea por IP, modelo, sistema operativo ni huellas compartidas. Presentar
una cookie bloqueada junto a otra credencial rechaza esa petición, pero no
transfiere la sanción a la otra cuenta. No se propagan sanciones entre cuentas
por haber usado supuestamente el mismo teléfono. Solo se aceptan objetivos
con credenciales registradas (o una sanción previa); un ID desconocido falla
sin crear un baneo. Revisar siempre el ID y la evidencia antes de sancionar.

**Límite:** no es un bloqueo del hardware. Borrar datos, reinstalar o falsificar
identificadores puede permitir crear una instalación nueva. Tampoco expulsa
una conexión Photon ya abierta, cancela una operación HTTP que ya pasó la
comprobación ni impide jugar offline. El APK puede mostrar su error genérico
de conexión (`error_code: 401`), no un mensaje personalizado de baneo.

El estado y el historial administrativo se guardan en el archivo privado
`server_data/bans.json`, mediante reemplazo atómico y bloqueo entre procesos
administrativos. El CLI no escribe `accounts.json`. Los cambios se leen en
las peticiones sin reiniciar; para cargar inicialmente este nuevo código sí
es necesario reiniciar el backend. No requiere recompilar el APK.

Respaldar todo `server_data/` coherentemente y restringir sus permisos al
operador del servidor. No editar ni borrar `bans.json`: eliminarlo quita las
sanciones; un archivo corrupto rechaza peticiones hasta restaurar una copia
válida. Un error de bloqueo administrativo exige reintentar cuando termine el
otro comando. No publicar estos datos ni su historial. Las pruebas usan
directorios temporales; queda validar visualmente el rechazo y la recuperación
en dos dispositivos reales, así como el comportamiento de una partida Photon
ya iniciada.

## Publicar solo el servidor

`.gitignore` usa una lista permitida: todo se ignora primero y después solo se
habilitan código, pruebas, datos de runtime y estos documentos. Antes de
publicar, revisa:

```powershell
git status --short
git ls-files
git diff --cached --name-only
```

No deben aparecer APK, árboles decompilados, herramientas de parcheo, scripts
de firma/compilación, logs, datos reales de jugadores, material de análisis ni
pruebas que requieran binarios del cliente. Los archivos que ya estuvieron
versionados deben quitarse del índice; añadirlos a `.gitignore` no basta.

No ejecutes `git clean -fdx`: borraría materiales locales ignorados y estado de
trabajo que Git no puede recuperar.
