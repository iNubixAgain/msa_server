# Metal Slug Attack Server

Servidor comunitario compatible con **Metal Slug Attack 7.13.0**. HTTP gestiona
registro, perfiles, inventarios, mazos, social y matchmaking; Photon/Luxon
transporta las partidas por UDP. La economía y varios modos siguen siendo
parciales: consulta el [estado funcional](docs/estado.md).

El operador aporta un cliente compatible. Este repositorio distribuye servidor,
contenido servido, valores iniciales, runtime y pruebas. APK, árboles decompilados,
herramientas privadas, credenciales y datos de jugadores quedan fuera de Git.

## Instalación e inicio en Windows

Requisitos: Windows x86-64, Git y Python **3.10 o posterior** en PATH.
El backend de esta rama utiliza la biblioteca estándar; Java y Android SDK
solo son necesarios en el equipo que prepara el APK.

```powershell
git clone https://github.com/iNubixAgain/msa_server.git
cd msa_server
python --version
Test-Path .\online_runtime\luxon_server.exe
Test-Path .\server_content\file_list.json
```

Configura las cuatro direcciones siguiendo [red y APK](docs/red-y-apk.md).
Para LAN, reemplaza la IP del ejemplo por la IPv4 activa de `ipconfig` y edita
los tres destinos de `online_runtime/config.yml` antes de arrancar:

```powershell
$env:MSA_HOST = '0.0.0.0'
$env:MSA_PORT = '42069'
$env:MSA_PUBLIC_HOST = '192.168.1.100'
$env:MSA_PUBLIC_PORT = '42069'
python server.py
```

Python inicia y supervisa Luxon. No abras otra instancia por separado.
También puedes editar `start.bat` y ejecutar `cmd /c start.bat`: ese archivo
sobrescribe el host público y los puertos, aunque hayas definido otros antes.
Las direcciones históricas del repositorio no configuran tu propia red.

Desde una segunda terminal del mismo equipo:

```powershell
Invoke-RestMethod http://127.0.0.1:42069/__offline_status
Invoke-RestMethod http://127.0.0.1:42069/__health
Get-Content .\ServerLog.log -Tail 40 -Wait
```

`/__health` devuelve 200 si el proceso Photon vive; no acredita una partida.
Ambos diagnósticos solo aceptan loopback. Usa `Ctrl+C` en la terminal del
servidor para detener HTTP y Luxon ordenadamente.

## Guías

- [Red y APK](docs/red-y-apk.md): cuatro destinos, LAN, túneles/NAT y flujo privado del cliente.
- [Despliegue](docs/despliegue.md): VPS, backups, actualización y diagnóstico.
- [Versiones y políticas](docs/versiones-y-politicas.md): main, desarrollo y variante histórica del VPS.
- [Operación](docs/operacion.md): variables, logs, TLS, cuentas y moderación.
- [Arquitectura](docs/arquitectura.md): módulos, identidad y persistencia.
- [Estado](docs/estado.md) y [pendientes](docs/pendientes.md): evidencia y limitaciones.
- [Endpoints](docs/Telemetrización.md): inventario estático; su auditoría histórica no garantiza todos los modos.
- [Contribución](docs/contribucion.md): rama, revisión y publicación.

## Pruebas y revisión

```powershell
python -m unittest discover -s tests -v
```

Las pruebas públicas usan datos temporales y contratos HTTP. Descarga nativa,
interfaz y partidas Photon requieren dispositivos. Estas guías se contrastaron
el **8 de noviembre de 2026** con main `ddbe6e1c`; no incorporan los cambios
sin commit de otra rama ni despliegan la copia privada del VPS.
