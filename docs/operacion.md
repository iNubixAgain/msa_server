# Operación, pruebas y publicación

## Configuración

`start.bat` define los valores de despliegue usados normalmente. También se
pueden establecer antes de ejecutar `python server.py`:

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
start.bat
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
dispositivo real. El 3 de septiembre de 2026 pasaron las 64 pruebas del
workspace completo; 50 corresponden al conjunto publicable del servidor.

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
