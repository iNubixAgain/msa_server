# Arquitectura del servidor

## Flujo principal

`server.py` es el único punto de entrada. Configura logs y bloqueo de instancia,
inicia `ThreadingHTTPServer`, resuelve la identidad, limita el cuerpo de las
peticiones, captura diagnósticos opcionales, despacha a los controladores y
supervisa `online_runtime/luxon_server.exe`.

```text
Cliente MSA
  ├─ HTTP/TCP 42069 ─> server.py ─> msa_server/* ─> JSON en server_data/
  └─ Photon/UDP ─────> Luxon ─────> salas de batalla en memoria
```

Las salas son efímeras; perfiles, progreso, relaciones y resultados se
persisten. Los errores HTTP públicos son opacos y no exponen excepciones ni
rutas locales.

## Módulos

| Módulo | Responsabilidad |
| --- | --- |
| `config`, `clock`, `lifecycle`, `diagnostics` | Configuración, reloj JST, instancia única, logging y capturas HTTP asíncronas. |
| `storage`, `bootstrap`, `identity` | JSON atómico, valores iniciales, migración e identidades internas/públicas. |
| `core`, `users`, `profiles`, `decks`, `inventory` | Login, menú, perfil, mazos, unidades y mejoras. |
| `assets`, `content`, `gameplay` | Tablas maestras, archivos servidos, Story, World Map, prácticas, eventos y tiendas compatibles. |
| `battle`, `ultimate_mission`, `gacha` | Batalla individual/Team Battle, Ultimate Mission y resultados visuales de Crank. |
| `matchmaking`, `online`, `arena`, `pvp_history` | Salas públicas 1v1/2v2, Rank Match, rankings e historial PvP. |
| `friend_online`, `guild_online` | Salas privadas Friend 1v1 y Guild 2v2. |
| `social`, `guild`, `notifications`, `news` | Perfiles públicos, amistades, chat, Guild, correo interno y anuncios. |
| `battle_tactics`, `snatch_wars` | Draft/Tactics y contrato parcial de Snatch Wars. |
| `photon`, `sessions` | Ciclo de vida de Luxon y seguimiento de sesiones/batallas. |

Los controladores reciben datos independientes del socket y devuelven un
payload serializable. La lógica nueva debe permanecer en `msa_server/`;
`server.py` conserva transporte, ciclo de vida y despacho.

## Identidad

- `client-...` es la identidad interna derivada de la instalación y aísla las
  rutas bajo `server_data/clients/`.
- `msa-...` es la identidad pública utilizada por Social, Chat, perfiles y
  logs visibles.
- La IP solo describe una conexión; nunca es identidad persistente.

## Persistencia

```text
server_data/
  social.json
  guilds.json
  chat_world.json
  chat_private.json
  chat_guild.json
  news.json
  battle_ladder.json
  clients/<hash>/
    profile.json
    user_deck.json
    user_deck.backup.json
    user_units.json
    online.json
    arena.json
    battle.json
    battle_tactics.json
    ultimate_mission.json
    snatch_wars.json
    ingame_notifications.json
```

`storage.py` escribe mediante archivo temporal, `flush`, `fsync` y reemplazo
atómico. Hay bloqueos por cliente y bloqueos globales para documentos
compartidos. El repositorio conserva solo `server_data/.gitkeep`: cuentas,
mensajes, tokens, capturas y logs son estado privado de ejecución.

## Datos versionados

- `server_defaults/`: estados iniciales JSON reparables por cliente.
- `server_content/`: catálogo, tablas maestras y respuestas/archivos estáticos
  que el backend entrega.
- `online_runtime/`: ejecutable y configuración mínima del relay Photon.

El servidor público no depende del árbol decompilado del cliente. Las rutas de
contenido legado leen exclusivamente desde `server_content/files/`.
