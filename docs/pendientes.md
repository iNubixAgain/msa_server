# Pendientes y limitaciones

## Prioridad inmediata

1. **Battle Tactics:** capturar el cierre nativo que ocurre después de
   `/battle_tactics/battle_start`. Se necesita reproducirlo en dos dispositivos
   y obtener `logcat`/tombstone antes de cambiar de nuevo el contrato por
   conjetura. La corrección se considera validada solo cuando ambos clientes
   llegan al combate y envían `/battle_tactics/battle_end`.

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
