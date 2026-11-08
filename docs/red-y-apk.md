# Direcciones HTTP, Photon y APK

## Cuatro destinos que deben coincidir

| Destino | Configuración | Puerto local habitual |
| --- | --- | --- |
| HTTP: login, tablas, datos y matchmaking | Endpoint del APK y MSA_HOST/PORT/PUBLIC_HOST/PUBLIC_PORT | TCP 42069 |
| Photon NameServer: primer contacto | Constante nativa del APK y NameServer.external_address | UDP 5058 |
| Photon MasterServer: negociación | MasterServer.external_address en Luxon | UDP 5055 |
| Photon GameServer: batalla | GameServer.external_address en Luxon | UDP 5056 |

El puerto público puede diferir del local. TCP público 51516 hacia TCP local
42069 requiere MSA_PORT=42069, MSA_PUBLIC_PORT=51516 y APK usando **51516**.
Anunciar un destino no crea un túnel ni abre el firewall. `0.0.0.0` es una
interfaz de escucha, no una dirección para jugadores. `127.0.0.1` en el teléfono
señala al teléfono y solo corresponde a un interceptor local deliberado.

## LAN, VPS y túneles

Obtén la IPv4 del adaptador activo con `ipconfig`. Para LAN, PC y teléfonos
usan la misma red, sin aislamiento Wi-Fi de invitados; reserva la IP por DHCP.
Para VPS usa su IP pública o dominio. Detrás de NAT, configura redirecciones
o un túnel con soporte **UDP** además de HTTP/TCP. CGNAT puede impedir entrada
directa. Los destinos gratuitos de un túnel pueden cambiar.

Permite el puerto HTTP público TCP y los tres puertos públicos Photon UDP en
el host, router y proveedor cuando corresponda. La interfaz opcional 5088 de
Luxon no es el servidor HTTP del juego y no necesita exponerse.

Ejemplo mínimo de `online_runtime/config.yml`, sustituyendo la IP por la tuya:

```yaml
Servers:
  NameServer:
    - port: 5058
      external_address: 192.168.1.100:5058
  MasterServer:
    - port: 5055
      external_address: 192.168.1.100:5055
  GameServer:
    - port: 5056
      external_address: 192.168.1.100:5056
EnableIPv6: false
NoBanner: true
MaxConnections: 0
MaxGamePeers: 0
TickTimeBudget: 2000
HTTP:
  enabled: false
  address: 127.0.0.1
  port: 5088
```

Con túneles, cada external_address contiene su asignación pública real; port
sigue siendo el puerto local de ese servicio. No copies sin revisar
config.example.yml: tiene servicios y opciones adicionales, incluida interfaz web.

## Direcciones del cliente preparado

El clon público **no contiene** APK, Apktool, firmador, tools/,
client_endpoint.json ni scripts de recompilación. El propietario necesita su
workspace privado compatible con 7.13.0. No basta cualquier APK de otra versión.

Haz una copia del proyecto. Estas son las representaciones del cliente privado
revisado; otras variantes pueden tener rutas y estructuras distintas:

| Archivo privado | Función |
| --- | --- |
| client_endpoint.json | Fuente local de HTTP/NameServer, raíces y slots nativos |
| lib/arm64-v8a/libAppMain.so | HTTP ASCII y NameServer UTF-32LE, ARM64 |
| lib/armeabi-v7a/libAppMain.so | Las mismas direcciones, ARM32 |
| res/xml/network_security_config.xml | Política HTTP y dominio permitido |
| smali_classes*/<paquete-del-cliente>/offline/OfflineServer.smali | URLs del interceptor, si existe |
| smali/<paquete-del-cliente>/MainActivity.smali | Arranque del interceptor local, si existe |

Los slots binarios tienen capacidad fija: verifica longitud, terminador NUL,
valor anterior y ambas arquitecturas. No reemplaces bytes sin conocer el slot
ni reutilices offsets de otra compilación. Si HTTPS no cabe, usa un dominio
más corto; omitir el esquema no conserva HTTPS automáticamente.

Fragmento de intención para conexión directa (no es una configuración completa):

```json
{
  "scheme": "http",
  "host": "192.168.1.100",
  "port": 42069,
  "native_host": "192.168.1.100",
  "native_port": 42069,
  "start_offline_server": false,
  "photon_nameserver": "192.168.1.100:5058",
  "photon_nameserver_native": "192.168.1.100:5058"
}
```

Conserva en tu archivo real las raíces, offsets, capacidades y demás claves
verificadas. Declarar start_offline_server en JSON no modifica por sí solo
MainActivity. El cliente híbrido puede resolver tablas localmente aunque el
login vaya al VPS; comprueba su procedencia antes de cambiar tablas en el VPS.

En el workspace privado, si ya tienes estas herramientas mantenidas:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\configure_client_endpoint.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\configure_client_endpoint.ps1 -Check
# Después de actualizar también NameServer en las dos bibliotecas:
python .\tools\check_client_endpoints.py
```

El configurador HTTP no modifica Photon NameServer. El parcheador privado de
NameServer necesita la dirección binaria anterior exacta, nueva dirección y
las dos rutas de biblioteca; se verifica capacidad UTF-32LE. Un dominio puede
tener una representación IPv4 diferente por capacidad, que debe registrarse
en photon_nameserver_native. MasterServer y GameServer no se editan en el APK:
los anuncia Luxon.

Ejemplo de invocación del parcheador privado, sustituyendo el valor anterior
por el que realmente contiene tu binario (no una dirección de un informe viejo):

```powershell
python .\tools\patch_photon_nameserver.py --source 'HOST_ANTERIOR:PUERTO' --host '192.168.1.100:5058' .\juego_decompilado\lib\arm64-v8a\libAppMain.so .\juego_decompilado\lib\armeabi-v7a\libAppMain.so
python .\tools\check_client_endpoints.py
```

Si el script informa que no cabe o no hay coincidencia única, detente y
revisa el binario/slot. No fuerces una sustitución ni elimines el terminador.

## Decompilar, compilar y firmar

Solo en el workspace privado que incluye scripts y JAR probados:

```powershell
# Solo para obtener otro proyecto; nunca sobreescribir el actualizado.
.\decompilar_apk.ps1 -Entrada 'original.apk' -Salida 'proyecto_nuevo'
.\recompilar_apk.ps1 -SoloVerificar
.\recompilar_apk.ps1
.\firmar_apk.ps1 -Entrada 'MSA-unsigned.apk'
```

Esos comandos no están en un clon público limpio. Revisa sus parámetros y
versión. La regeneración opcional del catálogo offline puede cambiar unidades;
no se necesita para cambiar únicamente direcciones. Verifica paquete,
versionCode/versionName, ambas ABI, alineación y firma del APK resultante.

Si administras manualmente las mismas herramientas privadas, los equivalentes
básicos son estos; conserva la configuración y las validaciones del proyecto:

```powershell
java -Xmx1024m -jar .\apktool.jar d .\original.apk -o .\proyecto_nuevo -p .\apktool-framework
java -Xmx1024m -jar .\apktool.jar b -f -j 1 -p .\apktool-framework .\juego_decompilado -o .\MSA-unsigned.apk
java -Xmx1024m -jar .\uber-apk-signer.jar --apks .\MSA-unsigned.apk --out .\signed --allowResign
# Usa el nombre real que mostró el firmador:
java -jar .\uber-apk-signer.jar --apks .\signed\NOMBRE_FIRMADO.apk --onlyVerify
```

No emplees la opción de borrado forzado al decompilar sobre un proyecto
existente. El build de Apktool puede modificar archivos de trabajo; no lo
ejecutes sobre tu única copia. Estos ejemplos no instalan nada en el teléfono.

Conserva la clave de firma. Android necesita un certificado compatible para
actualizar una instalación; otra clave puede obligar a una instalación distinta
y afectar progreso local. El firmador privado revisado usa clave debug por
defecto: no la confundas con una clave propia de distribución. No publiques
claves ni contraseñas. Login HTTP y una partida real son la validación final.

## Reinicio o recompilación

| Cambio | Reiniciar servidor | Recompilar/firmar APK |
| --- | --- | --- |
| Python o configuración cargada al inicio | Sí | No |
| HTTP o NameServer embebidos | Si cambia el despliegue | Sí |
| Solo dirección anunciada de Master/GameServer | Sí, Luxon | No |
| Tablas descargadas del backend | Invalidar cachés/publicar versión coherente | No si el cliente las descarga |
| Tablas interceptadas en el APK | Cambiar VPS no basta | Puede requerirse |
| Apariencia de botón forzado por parche nativo | El API puede bloquear acceso | Puede requerirse para ocultarlo |

No uses un reloj global de 2018/2020 para limitar unidades: alteraría temporadas
y eventos. El roster necesita disponibilidad, catálogo/hashes/versión y
validación de mazos y batallas. Consulta [versiones y políticas](versiones-y-politicas.md).
