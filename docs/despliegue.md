# Despliegue, mantenimiento y diagnóstico

## Preparar un host nuevo

El main auditado ejecuta luxon_server.exe directamente desde PhotonSupervisor.
La ruta soportada por ese código es **Windows x86-64**, incluida una VM/VPS
Windows. Ejecutar python3 server.py en Linux no convierte el ejecutable Windows
en nativo. Algunas variantes privadas añaden Wine: requieren supervisor,
Wine/prefix, permisos y runtime verificados en conjunto; ese soporte no está
en este main. Tampoco se distribuye aquí start.sh ni una unidad systemd.

1. Clona el repositorio en una carpeta con permisos de escritura del usuario
   que operará MSA. Verifica Python 3.10+, contenido y ejecutable Photon.
2. Conserva server_content, server_defaults, msa_server, server.py y
   online_runtime. server_data se crea/usa como estado privado; no es una BD.
3. Configura las cuatro direcciones según [red y APK](red-y-apk.md), firewall
   y posibles traducciones NAT/túnel. El host anunciado debe ser accesible
   desde los teléfonos, no solo desde el VPS.
4. Inicia con python server.py y variables explícitas, o edita start.bat.
   No ejecutes Luxon aparte ni arranques dos backends sobre los mismos datos.
5. Consulta los diagnósticos desde loopback y prueba login en un teléfono.
6. Prueba una partida con dos dispositivos y su resultado; para 2v2,
   cuatro participantes reales. Un 200 HTTP no prueba el canal UDP.

Para operación persistente, el servicio/supervisor del host debe tener el
mismo usuario, carpeta de trabajo y variables del arranque validado. Debe
permitir cierre ordenado y reiniciar con demora cuando falle. Verifica un
reinicio completo de la VM antes de asumir que la sesión sobrevive a desconectar
el escritorio remoto. El repositorio no instala un servicio automáticamente.

## TLS y rutas de descarga

El backend acepta TLS directo con MSA_TLS_CERT y MSA_TLS_KEY, ambas rutas PEM;
el certificado debe corresponder al dominio y ser confiable para Android.
La clave es privada. Activarlo no cambia un APK que sigue usando http://.
Un proxy debe conservar el Host público correcto: get_pack_info lo usa para
construir la URL de descarga. Revisa esa respuesta si login funciona pero las
tablas se descargan de una dirección interna. La terminación TLS externa
requiere verificar también que el backend anuncie el esquema correcto;
no se debe asumir soporte automático de X-Forwarded-Proto.

## Copias y actualizaciones

Antes de actualizar, registra el commit y la variante desplegada. main,
Balance-Test y los paquetes privados del VPS no son intercambiables. Mantén
un backup del código/configuración de esa versión y de su política/tablas.

Para una copia coherente de jugadores, detén el backend ordenadamente y copia
**todo server_data/** junto con las revisiones privadas que contiene. Incluye
accounts.json, bans.json y credenciales/revocaciones: restaurar solo carpetas
de clientes puede romper transferencias. Protege ese respaldo fuera de Git.
Conserva también configuración del host, tabla/catálogo y material TLS aparte.

En un despliegue que realmente sigue main y tiene worktree limpio:

```powershell
git status --short
git fetch origin
# Con backend detenido y respaldo completado:
git switch main
git pull --ff-only origin main
python -m unittest discover -s tests -v
```

Si hay modificaciones locales o paquetes privados, revisa el diff y prepara
una integración explícita; no uses reset --hard, clean ni sobrescribas datos.
Vuelve a iniciar usando la configuración guardada y verifica salud, login,
catálogo y partidas. Un reinicio descarta salas efímeras y desconecta partidas.
No reemplaces server_data con una carpeta vacía tomada de un clon o ZIP.

Para recuperar, vuelve al código/configuración/tableado compatibles respaldados
y restaura los datos coherentemente con el servicio detenido. Las tablas ya
cacheadas por teléfonos pueden necesitar una versión **más nueva** para
volver a descargarse; bajar el número de versión no basta.

## Diagnóstico por etapas

| Síntoma | Revisar |
| --- | --- |
| El proceso no arranca | Python, carpeta de trabajo, contenido, exe Photon, instancia activa, puerto ocupado |
| Salud local 503 | Proceso Luxon, ruta/executable/configuración; no equivale a un fallo del catálogo |
| Salud local 200 pero teléfono no llega | HTTP del APK, IP/puerto público, firewall, NAT, aislamiento Wi-Fi |
| Login sí, tablas no | get_pack_info, Host/esquema, get_file_list, hashes, tamaño y origen local/remoto |
| Solo algunos ven unidades antiguas | Misma versión de APK, inventario entregado, filtros del menú, master_ver y caché de tablas por instalación |
| Matchmaking sí, batalla no | Los tres destinos UDP, asignación por servicio y firewall; HTTP 200 no verifica UDP |
| Un modo abre pero falla | Compatibilidad nativa/tablas y contrato de esa escena; consultar límites funcionales |
| No aparecen logs | Variante desplegada y configure_logging; algunos paquetes privados los desactivan |

Revisa el log de esa sesión solo cuando corresponda al problema. En main,
ServerLog.log rota a 20 MiB con cinco copias y la salida Photon se integra al
logging. Enciende capturas POST/paquetes solo durante el diagnóstico y no las
publiques. No solicites contraseñas, cookies ni cuentas completas para reportar
un fallo; basta ruta, hora, error y metadatos anonimizados.

## Publicación

La rama de documentación no despliega ni actualiza el VPS. Cambios mediante
Issue, rama corta, commit y PR. Solo se publican los archivos revisados;
jamás git push --all, APK, tools/, logs o server_data real. El cliente y su
firma siguen bajo responsabilidad del propietario.
