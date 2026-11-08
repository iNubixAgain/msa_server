# Pendientes y limitaciones

Revisión operativa del 8 de noviembre de 2026: la suite pública aprobó 73
pruebas; sigue pendiente validar arranque/red desde dispositivos en cada
despliegue. main ejecuta Luxon directamente como EXE de Windows: el soporte
Wine de variantes privadas no está incorporado aquí. No usar una guía de
otro paquete como prueba de que `python3 server.py` basta en Linux.

La corrección privada de selectores limitados a 2016 exige comprobar una
instalación afectada tras descargar tablas. No hay una causa única confirmada
con respuesta/captura del VPS; véase [políticas por versión](versiones-y-politicas.md).

## Prioridad inmediata

1. **Battle Tactics:** validar en dos dispositivos la adaptación de reloj de
   `/battle_tactics/battle_start`. El análisis nativo encontró que el combate
   resuelve temporada 0 con la fecha actual y desreferencia un accessor nulo;
   el inicio ahora sincroniza una fecha válida de temporada 23 (ver
   `Telemetrización.md`). Reiniciar el backend para cargar la corrección;
   no requiere recompilar el APK. Se considera validada en dispositivo cuando
   ambos clientes llegan al combate y envían `/battle_tactics/battle_end`.
   Si persiste el cierre, capturar `logcat`/tombstone y comprobar si otra
   petición restauró el reloj durante la carga.

## Validación pendiente en dispositivo

- Batalla individual offline y Team Battle.
- Ultimate Mission y resultados visuales de Crank.
- Notificaciones/correo para amistad, privado y Guild.

## Limitaciones de diseño actuales

- Persistencia JSON sin base de datos dedicada ni transacciones entre varios
  documentos.
- Salas de matchmaking en memoria: un reinicio descarta salas activas.
- Tráfico HTTP sin TLS ni firma del cliente.
- Photon/Luxon es un binario Windows x86-64; desplegarlo en otro sistema
  requiere reemplazo o compatibilidad adicional.
- `save_data` no se interpreta y permanece como blob opaco.

## Criterio de evidencia

Un endpoint con pruebas automáticas se marca como “implementado”. Solo pasa a
“confirmado en dispositivo” después de recorrer la escena real, verificar el
intercambio Photon cuando aplique y observar la petición de cierre/resultado.
Este criterio evita volver a declarar completo un flujo que únicamente supera
el contrato HTTP.
