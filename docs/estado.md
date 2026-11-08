# Estado funcional del servidor

## Revisión pública del 8 de noviembre de 2026

Sobre main `ddbe6e1c`, **73 pruebas aprobadas** con
`python -m unittest discover -s tests -v` en Windows. Se revisaron contratos,
configuración y guías; no se inició el servicio operativo ni se realizaron
nuevas pruebas visuales o Photon. Los resultados de dispositivo descritos
abajo son evidencia histórica, no una certificación del despliegue actual.

Las restricciones de roster hasta Nova, los modos desactivados y buffs
neutralizados pertenecen a la variante privada del VPS, no a este main.
Consulta [versiones y políticas](versiones-y-politicas.md).

Fecha de auditoría: **3 de septiembre de 2026**. Se contrastaron los módulos de
`msa_server/`, las pruebas automatizadas y, cuando existía evidencia reciente,
los registros de dispositivo. Una prueba de contrato HTTP no se presenta como
validación visual ni como una batalla Photon real.

## Confirmado en dispositivos reales

| Área | Estado verificado |
| --- | --- |
| Inicio y datos base | Registro, tutorial, `top_menu`, tablas maestras, perfiles y mazos aislados con Supports/Assist. |
| Online público | Matchmaking 1v1 y 2v2, selección de mazo, relay Photon/Luxon y finalización de batallas. |
| Rank Match | Matchmaking, batalla y recepción de `battle_end`; los registros del 2 de septiembre de 2026 contienen varias partidas completas. |
| Social y Guild | Perfiles ajenos, solicitudes y amistades, bloqueos, administración de Guild y chat mundial, privado y de Guild. |
| Extra Ops | Marathon 296 (tipo 1) abre y procesa el resultado de batalla. |

## Implementado y cubierto automáticamente

La suite completa del workspace pasó **64 pruebas** el 3 de septiembre de
2026. Catorce de esas pruebas inspeccionan materiales privados del cliente y no
forman parte del repositorio público; una copia pública limpia ejecuta **50
pruebas del servidor**.

La cobertura del servidor incluye:

- identidad estable por `terminal_id`, con ID interno `client-...` e ID público
  `msa-...`;
- reparación de estados iniciales, aislamiento por cliente, escritura atómica
  y recuperación de JSON corrupto;
- contratos de batallas individuales, Team Battle, Ultimate Mission y Crank;
- Online 1v1/2v2, Rank Match, rankings, historial FIFO de cinco partidas y
  puntos/rangos;
- Friend Battle 1v1 y Guild Online 2v2 con salas privadas e invitaciones;
- Social, listas negras, concurrencia de chat, Guild e indicadores de correo;
- Draft, persistencia, matchmaking y contrato HTTP de Battle Tactics;
- Extra Ops Marathon, noticias, tablas maestras, diagnósticos y supervisor de
  Photon.

## Implementado pero aún pendiente de validación real

- Friend Battle 1v1.
- Guild Online 2v2 con cuatro miembros reales.
- Batallas individuales offline y Team Battle de tres mazos.
- Ultimate Mission y Crank visual.
- Buzón/notificaciones dentro del juego en todos sus flujos.

## Regresión abierta

Battle Tactics no se considera actualmente completado en dispositivo. En los
últimos intentos reales ambos jugadores completaron Draft, matchmaking,
`decide_room`, entrada a la sala Photon y `/battle_tactics/battle_start` con
HTTP 200; el proceso cliente se cerró durante la transición al combate y nunca
envió `/battle_tactics/battle_end`. El contrato automatizado pasa, pero falta
validar en dispositivo la adaptación del reloj de inicio: se identificó una
búsqueda de temporada por fecha que acaba leyendo un puntero nulo después de
2023. `battle_start` ahora entrega una fecha de temporada 23; las demás
respuestas y los registros conservan la fecha real. Véase `Telemetrización.md`.

## Funciones parciales

- Snatch Wars devuelve contratos y abre la escena, pero no genera un tablero
  jugable.
- Gacha/Crank devuelve resultados visuales sin descontar moneda ni modificar
  inventario.
- La economía y las tiendas no son plenamente autoritativas ni
  transaccionales.
- `save_data` se conserva como un blob opaco.
- Traslado de cuenta: emisión de contraseña, consumo, conservación de identidad
  y revocación del dispositivo anterior implementados y probados por HTTP.
  Pendiente restauración nativa y experiencia de errores en dos dispositivos;
  configuración y restricciones en `operacion.md`.
