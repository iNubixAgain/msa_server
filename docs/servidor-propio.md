# Servidor propio: de un clon a una conexión verificada

Esta guía corresponde al código **incluido en main** de Mambo Sociedad Anónima
(MSA). El recorrido soportado es Windows x86-64, tanto en un equipo local como
en un VPS Windows. El proceso HTTP usa Python; el relay incluido es un EXE de
Windows. Para un VPS Linux se necesita una variante con supervisor Wine probado:
este main no lo implementa y ejecutar python3 por sí solo no basta.

## 1. Preparar los requisitos

Necesitas Git, Python 3.10+ en PATH, una carpeta escribible y un cliente Android
compatible que puedas configurar. Si solo operas el backend, no necesitas Java
ni Android SDK. Si vas a modificar el APK, esos requisitos están en
[red y APK](red-y-apk.md) y [herramientas](herramientas.md).

Desde PowerShell:

```powershell
git clone https://github.com/iNubixAgain/msa_server.git
cd msa_server
git log -1 --oneline
python --version
python -m venv .venv
.\.venv\Scripts\python.exe --version
```

El servicio de esta versión usa la biblioteca estándar. No requiere MySQL,
Redis, una cuenta de API, una base de datos externa ni pip install para arrancar.
No confundas las dependencias de herramientas privadas con las del backend.

Comprueba los archivos antes de continuar:

```powershell
Test-Path .\server.py
Test-Path .\msa_server\config.py
Test-Path .\server_content\file_list.json
Test-Path .\server_content\files
Test-Path .\server_defaults\user_units.json
Test-Path .\online_runtime\luxon_server.exe
Test-Path .\online_runtime\config.yml
```

Todos deben devolver True. No inicies desde una descarga parcial del repo.
server_data contiene el estado de jugadores y debe ser escribible por el
usuario que ejecuta el servicio. No copies datos de otro operador para crear
un servidor nuevo. Las carpetas defaults/content son necesarias para preparar
cuentas nuevas y entregar datos al cliente.

## 2. Elegir las direcciones antes de editar

Completa tu propia ficha de despliegue. Estos valores son ejemplos:

| Dato | LAN | VPS con IP directa |
| --- | --- | --- |
| HTTP local | 0.0.0.0:42069 | 0.0.0.0:42069 |
| HTTP que utiliza Android | http://192.168.1.100:42069 | http://IP_PUBLICA:42069 |
| NameServer que utiliza Android | 192.168.1.100:5058 | IP_PUBLICA:5058 |
| MasterServer anunciado por relay | 192.168.1.100:5055 | IP_PUBLICA:5055 |
| GameServer anunciado por relay | 192.168.1.100:5056 | IP_PUBLICA:5056 |

Para LAN, ejecuta ipconfig y toma la IPv4 del adaptador realmente conectado.
Los teléfonos deben poder comunicarse con ese equipo; una red de invitados
puede aislarlos. Reserva la dirección en DHCP si quieres que permanezca estable.
Una IP LAN no permite acceder desde fuera de casa.

Para VPS usa su IP pública/dominio y revisa el firewall del proveedor. Para un
host detrás de un router, configura TCP y UDP entrantes o un túnel adecuado.
CGNAT puede impedir entrada directa. Un túnel HTTP no transporta automáticamente
las batallas UDP. Con túneles anota cuatro asignaciones públicas distintas si
el proveedor así las entrega; pueden tener puertos diferentes a los locales.

## 3. Configurar el relay

Edita online_runtime/config.yml con el ejemplo mínimo de [red y APK](red-y-apk.md).
Cada sección NameServer/MasterServer/GameServer conserva su port local y lleva
su dirección pública accesible en external_address. Cambia las tres, no solo
NameServer. La interfaz HTTP opcional del relay debe permanecer desactivada;
5088 no es el endpoint HTTP del backend.

No ejecutes luxon_server.exe manualmente antes de server.py: Python lo inicia,
lee su salida y supervisa su ciclo de vida. Una instancia aparte puede ocupar
los puertos o dividir las salas entre procesos.

## 4. Permitir tráfico de entrada

En PowerShell **como administrador**, solo en el equipo que alojará MSA:

```powershell
New-NetFirewallRule -DisplayName 'MSA HTTP' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 42069
New-NetFirewallRule -DisplayName 'MSA UDP' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5055,5056,5058
```

Si ya existen reglas equivalentes, revísalas en lugar de repetir el comando.
En un VPS habilita además esos puertos en su panel de red. Con NAT abre las
asignaciones públicas hacia los puertos locales correctos. Si eliges HTTP en
51516 en vez de 42069, ajusta la regla correspondiente; los ejemplos no hacen
traducción de puertos por sí solos. En LAN puedes restringir el alcance de
origen a tu subred en la configuración de firewall.

## 5. Configurar el cliente Android

Necesitas que el HTTP embebido y el Photon NameServer apunten a tus destinos.
MasterServer y GameServer los anuncia el relay. El clon del backend no incluye
el proyecto Android ni sus herramientas de modificación.

Sigue [el procedimiento de APK](red-y-apk.md): verifica ambas ABI, URLs del
interceptor si existe, política HTTP, NameServer, firma y alineación. Una
conexión directa no debe apuntar a 127.0.0.1 en el teléfono. Si hay un
interceptor local deliberado, verifica qué solicitudes reenvía al servidor.

Un APK ya configurado para esos mismos destinos no necesita recompilarse por
cada actualización de Python. Cambiar HTTP o NameServer embebidos sí requiere
recompilar y firmar. Conserva la clave anterior para actualizar instalaciones
sin un cambio de certificado. No reinstales ni borres datos como primer paso
para resolver un fallo de red.

## 6. Iniciar HTTP y relay

En la raíz del repo, en una terminal normal, reemplaza la IP de ejemplo:

```powershell
$env:MSA_HOST = '0.0.0.0'
$env:MSA_PORT = '42069'
$env:MSA_PUBLIC_HOST = '192.168.1.100'
$env:MSA_PUBLIC_PORT = '42069'
.\.venv\Scripts\python.exe .\server.py
```

Mantén esa terminal abierta. El puerto anunciado debe ser el que utiliza el
cliente: si el TCP público es 51516 y reenvía a local 42069, cambia solamente
MSA_PUBLIC_PORT a 51516 y usa ese puerto público en el cliente.

Como alternativa edita los valores de start.bat y ejecútalo con
`cmd /c start.bat`. Su Python procede de PATH y sus líneas set sobrescriben
host/puertos; las variables anteriores no reemplazan las líneas del BAT.
Para usar el Python del entorno virtual con certeza, usa el comando explícito.

Si usas TLS directo, configura ambas rutas MSA_TLS_CERT y MSA_TLS_KEY antes
del arranque y prepara un cliente HTTPS con certificado confiable. Encender
TLS no transforma URLs HTTP anteriores. Consulta [despliegue](despliegue.md)
para proxy, esquema de descarga y protección de credenciales.

## 7. Verificar antes de anunciar disponibilidad

Desde otra terminal del mismo host:

```powershell
Invoke-RestMethod http://127.0.0.1:42069/__offline_status
Invoke-RestMethod http://127.0.0.1:42069/__health
Get-Content .\ServerLog.log -Tail 40 -Wait
```

Si configuraste TLS, usa su URL confiable correspondiente. Los diagnósticos
solo aceptan loopback. offline_status informa aunque Photon esté caído;
health devuelve 200 si el proceso relay vive y 503 si no. No uses el 200 como
prueba de conectividad UDP desde internet.

Prueba por etapas en teléfonos:

1. Abrir el cliente configurado y completar registro/login.
2. Completar descarga de tablas sin errores de versión/hash.
3. Verificar inventario, perfil y persistencia tras salir y volver a entrar.
4. Con dos teléfonos, entrar a matchmaking 1v1 y completar batalla y resultado.
5. Con cuatro participantes reales, repetir en 2v2 si vas a ofrecerlo.
6. Probar las funciones sociales y los modos que realmente habilitarás.

HTTP correcto y matchmaking no acreditan una batalla: si se queda entrando,
revisa los tres destinos UDP y el firewall. Para selectores incompletos,
compara master_ver, inventario y caché por instalación. Ver
[diagnóstico por etapas](despliegue.md#diagnóstico-por-etapas).

## 8. Operar, administrar y respaldar

Usa Ctrl+C en la terminal del servidor y espera el cierre de HTTP/Luxon.
No borres server.instance.lock para forzar otra instancia. Las salas son
efímeras: reiniciar interrumpe partidas. Para ejecutar sin una sesión de
escritorio abierta necesitas un supervisor/servicio Windows configurado por
el operador con el mismo usuario, directorio y variables; el repo no instala
ese servicio automáticamente. Prueba el reinicio completo del host.

La moderación usa admin.py: [inventario de herramientas](herramientas.md).
Los datos de cuentas y sus backups son privados. Con el backend detenido,
respalda todo server_data y conserva configuración, contenido y commit de
código compatibles. En un VPS existente, nunca sustituyas server_data por la
carpeta vacía de un clon. Antes de actualizar revisa el diff, pruebas y
[procedimiento de recuperación](despliegue.md#copias-y-actualizaciones).

## 9. Qué significa funcional en esta versión

La instalación es operativamente verificada cuando inicia, los teléfonos
conectan, descargan datos, conservan progreso y completan los modos probados.
No implica que todos los modos, tiendas, economía o recompensas estén
implementados. [Estado](estado.md) y [pendientes](pendientes.md) describen lo
parcial y la evidencia histórica. No presentes un evento con respuesta
simulada como funcional por haber recibido HTTP 200.

Las políticas de la variante privada del VPS (roster histórico, bloqueo de
modos y buffs) no se instalan con este clon: [versiones y políticas](versiones-y-politicas.md).
No existe un comando universal que active todas esas funciones en main.
