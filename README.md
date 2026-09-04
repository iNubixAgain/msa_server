# Metal Slug Attack Server

Backend comunitario para Metal Slug Attack 7.13.0. Implementa el transporte
HTTP, persistencia JSON, contenido estático y los contratos de juego que usa el
cliente, además de supervisar el relay UDP Photon/Luxon para las batallas
online.

Este repositorio público contiene únicamente componentes necesarios para
ejecutar, probar y mantener el servidor. No incluye APK, código decompilado,
parches, herramientas ni documentación del cliente Android.

## Requisitos e inicio

- Windows x86-64.
- Python 3.10 o posterior; el backend usa solo la biblioteca estándar.
- `online_runtime/luxon_server.exe` y su `config.yml` para el modo online.

Desde PowerShell o CMD:

```powershell
start.bat
```

`start.bat` configura el endpoint público anunciado por el backend, inicia
`server.py` y este, a su vez, administra el proceso de Luxon. Para detenerlos,
usa `Ctrl+C` y espera el cierre normal.

## Comprobación rápida

Con el servidor iniciado, el diagnóstico solo acepta conexiones locales:

```powershell
Invoke-RestMethod http://127.0.0.1:42069/__health
```

Pruebas del servidor:

```powershell
python -m unittest discover -s tests -v
```

Las pruebas usan directorios temporales y no modifican `server_data/`.

## Documentación

- [Estado funcional](docs/estado.md): qué está confirmado, probado únicamente
  de forma automática o pendiente.
- [Arquitectura](docs/arquitectura.md): módulos, despacho, identidades,
  persistencia y datos incluidos.
- [Contribución](docs/contribucion.md): flujo Git, responsabilidades, criterios
  de cierre y plantilla para solicitar trabajo.
- [Operación](docs/operacion.md): configuración, puertos, logs, pruebas y
  publicación del repositorio.
- [Pendientes y limitaciones](docs/pendientes.md): regresiones abiertas,
  funciones parciales y prioridades.
- [Telemetrización de endpoints](docs/Telemetrización.md): inventario del API
  del cliente, cobertura del backend y procedencia de sus datos.

Última auditoría de documentación: **4 de septiembre de 2026**.
