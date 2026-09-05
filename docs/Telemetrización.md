# Telemetrización y trazabilidad de endpoints del cliente

Fecha de verificación: **4 de septiembre de 2026**. Alcance: Metal Slug
Attack 7.13.0, el interceptor offline incluido en el cliente preparado y el
backend actual de este repositorio.

Este documento es el mapa contractual del servidor. No es una captura de una
sesión concreta ni afirma que todas las rutas hayan sido recorridas en un
dispositivo. El inventario estático contiene **391 rutas de RPC** presentes en
el ELF de producción y una ruta adicional, `/title/get_master_table`,
confirmada por el catálogo de archivos y por el interceptor offline: **392
rutas lógicas del cliente en total**.

## Cómo leer el informe

El servidor normaliza la barra final, por lo que `/gacha/top/` en el binario y
`/gacha/top` en Python son la misma ruta. En las tablas siguientes, cada acción
se concatena con el prefijo: `arena` + `battle_start` significa
`/arena/battle_start`.

Estados del backend:

- **F**: contrato específico, con lógica o estado propio.
- **P**: respuesta estructurada pero funcionalidad parcial, vacía o simulada.
- **C**: compatibilidad superficial; evita un error de protocolo, pero no
  implementa la operación solicitada.
- **G**: únicamente cae en la respuesta genérica de cualquier `POST`
  desconocido (`{"result":0,"status":0}`); no equivale a soporte funcional.

La extracción estática acredita que el cliente conoce una URI, no que la
pantalla correspondiente siga siendo alcanzable ni que la ruta se invoque en
cada sesión. Los RPC del juego son normalmente `POST`; las descargas de
contenido se expresan como URLs y el backend acepta `GET` o `POST` en sus
controladores. Un `GET` desconocido recibe 404, mientras que un `POST`
desconocido recibe actualmente el resultado genérico.

## Flujo real y procedencia

```text
escena/controlador nativo
        |
        +-- consulta tablas ya descargadas y cacheadas por el cliente
        |
        +-- crea RPC HTTP ---------------------------------------+
                 |                                               |
                 +-- OfflineServer lo intercepta                 |
                 |     -> assets offline / respuestas embebidas  |
                 |                                               |
                 +-- backend externo -> server.py                |
                                      -> controlador msa_server/*
                                      -> server_data / defaults /
                                         server_content / memoria

batalla online: HTTP crea/decide la sala -> respuesta con relay -> Photon UDP
```

Hay cuatro orígenes de datos que no deben confundirse:

1. **Maestros del cliente:** las 285 tablas se anuncian mediante
   `/title/get_file_list` y se descargan con `/title/get_master_table`. El
   cliente preparado puede obtenerlas de su bundle `offline/files`; el backend
   externo sirve la copia de `server_content/files`.
2. **Estado dinámico:** perfiles, inventario, mazos, gremios, mensajes,
   rankings e historiales proceden de JSON bajo `server_data/`.
3. **Estado inicial:** `server_defaults/` se copia o mezcla al crear un
   jugador; no es una tabla maestra.
4. **Estado efímero:** colas y salas están en memoria. Photon/Luxon transporta
   la batalla en tiempo real y no es una tabla ni otro endpoint HTTP.

## Inventario exhaustivo de RPC

La columna “backend” describe el nivel más alto alcanzado por la familia. Las
excepciones mixtas se detallan en la misma celda.

| Prefijo | Acciones conocidas por el cliente | Backend y propietario actual |
| --- | --- | --- |
| `arena` | `battle_end`, `battle_history`, `battle_start`, `create_or_enter_room`, `decide_room`, `honor_history`, `leave_room`, `ranking_top`, `top` | **F**, `ArenaController`; sala en memoria y `arena.json` por jugador. |
| `battle_tactics` | `battle_end`, `battle_history`, `battle_start`, `buy`, `create_or_enter_room`, `decide_room`, `end_draft`, `leave_room`, `ranking_top`, `set_deck`, `start_draft`, `top` | **F/P**, `BattleTacticsController`; Draft, sala, ranking e historial persistente. La tienda y validación económica siguen siendo parciales. |
| `boot_camp` | `get_reward`, `start`, `top` | **P**, `GameplayController`; contrato vacío, sin progreso completo. |
| `build_up` | `buy_deck`, `customize_affiliation_up`, `set_deck`, `top`, `up` | **F/P**, `GameplayController`; `set_deck` persiste, el resto devuelve principalmente el usuario. |
| `daily_sale_shop` | `buy` | **G**, fallback genérico. |
| `dress_up_shop` | `buy`, `top` | **G**, fallback genérico. |
| `event/extension` | `assign`, `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `raid`, `ranking_top`, `regist_user`, `top` | **P**, respuesta de evento vacía en `GameplayController`. |
| `event/marathon` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `get_reward`, `quest`, `raid`, `regist_user`, `top` | **P**, `GameplayController`; mapa mínimo reproducible, sin economía completa. |
| `event/marathon2nd` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `get_reward`, `quest`, `raid`, `regist_user`, `top` | **P**, mismo contrato mínimo de Marathon. |
| `event/mass` | `battle_continue`, `battle_end`, `battle_start`, `build_up`, `buy_relief_supplies`, `entry`, `history`, `ranking_top`, `set_deck`, `set_tactics`, `top` | **F/P**, `SnatchWarsController`; estado de gremio y evento persistente, con partes de combate/economía simuladas. |
| `event/melee` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `raid`, `regist_user`, `top` | **G**; no hay controlador ni tablas `m_melee_*` en el catálogo 7.13.0. |
| `event/pile` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `get_reward`, `quest`, `raid`, `regist_user`, `top` | **P**, respuesta de evento vacía en `GameplayController`. |
| `event/recapture` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `buy`, `raid`, `regist_user`, `set_deck`, `top` | **P**, respuesta de evento vacía. |
| `event/score_attack` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `guild_ranking_top`, `raid`, `ranking_top`, `regist_user`, `top` | **P**, respuesta de evento vacía; no existe familia maestra dedicada en el catálogo actual. |
| `event/tforce` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `gacha_play`, `gacha_reset`, `insight`, `map_return`, `map_start`, `march`, `raid`, `regist_user`, `set_deck`, `top` | **P**, respuesta de evento vacía. |
| `event/tline` | `battle_continue`, `battle_end`, `battle_history`, `battle_start`, `buy`, `buy_relief_supplies`, `friendship_gj`, `raid`, `ranking_top`, `regist_user`, `reserve`, `summon`, `top`, `used_buy` | **P**, respuesta de evento vacía. |
| `event/united/main` | `buy`, `friend`, `regist_user`, `top`, `used_buy` | **P**, respuesta de evento vacía. |
| `event/united/pt_attack` | `battle_end`, `battle_start`, `create_or_enter_room`, `create_room`, `decide_room`, `leave_room` | **P** HTTP; no implementa matchmaking específico de United. |
| `event/united/pt_battle` | `battle_end`, `battle_start`, `create_or_enter_room`, `create_room`, `decide_room`, `leave_room` | **P** HTTP; no implementa matchmaking específico de United. |
| `event/united/solo_attack` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `raid` | **P**, respuesta de evento vacía. |
| `event_info` | `get_news` | **F**, `NewsController`; lee noticias configuradas en el servidor. |
| `friend` | `relation`, `search`, `sever`, `top` | **F**, `SocialController`; relaciones persistentes. |
| `friend_online_1on1` | `battle_end`, `battle_start`, `create_room`, `decide_room`, `leave_room` | **F**, `FriendOnlineController`; invitación social y sala efímera. |
| `gacha` | `box_reset`, `fever_get_bonus`, `free_play`, `get_vip_bonus`, `play`, `premium_get_reward`, `select_play`, `ticket_play`, `top` | **F/P**, `GachaController`; genera resultados, pero la economía y todas las variantes históricas no están completas. |
| `guild` | `break_guild`, `build_guild`, `camp_soldier`, `command_member`, `delete_request`, `edit_guild_info`, `get_guild_bonus`, `gj`, `join_approval`, `join_guild`, `join_onetime_guild`, `request_enrollment`, `return_soldier`, `search_guild` | **F**, `GuildController`; estado global de gremios. `search_guild` acepta los campos nativos `guild_name`, `guild_id` y `offset`, además de aliases internos. |
| `guild_battle` | `area_keep`, `bid`, `playable`, `raid_end`, `raid_start`, `stock`, `top` | **F/P**, `GuildController`; estructura nativa y estado, combate/economía parciales. |
| `guild_coop` | `member_coop_battle_end`, `member_coop_battle_start`, `member_coop_request`, `stage_keep`, `top` | **G**, sin controlador. |
| `guild_online_1on1` | `battle_end`, `battle_start`, `create_room`, `decide_room`, `leave_room` | **G**, sin controlador; solo está implementada la variante de gremio 2v2. |
| `guild_online_2on2` | `battle_end`, `battle_start`, `create_room`, `decide_room`, `leave_room` | **F**, `GuildOnlineController`; sala efímera y relay Photon. |
| `guild_shop` | `buy`, `top`, `update` | **P**, `GameplayController`; tienda vacía. |
| `integrated_shop` | `top` | **G**, fallback genérico. |
| `labo` | `battle_end`, `history` | **P**, resultado e historial vacíos. |
| `level_up_shop` | `top` | **G**, pese a existir `m_level_up_shop`. |
| `limited_offer` | `buy`, `top` | **G**, pese a existir la familia maestra de ofertas. |
| `loto` | `get_ticket_list` | **C**, respuesta de usuario sin lista funcional. |
| `mail` | `get_present`, `top` | **F**, `InGameNotificationController`; mensajes visibles y estado de lectura/entrega. |
| `mars_shop` | `buy`, `exchange` | **G**, fallback genérico. |
| `medal_shop` | `buy`, `prepare`, `top` | **G**, fallback genérico. |
| `merchant_of_death` | `buy`, `top`, `update` | **G**, fallback genérico. |
| `msp_shop` | `buy`, `top` | **G**, fallback genérico. |
| `mypage` | `add_black_list`, `buy_user_lv`, `chat`, `get_message_info` | **F** para mensajes/chat/lista negra mediante `SocialController`; marcas nativas por canal corregidas el 5/9/2026, pendientes de validación visual; `buy_user_lv` es **G**. |
| `normal_shop` | `buy`, `top`, `update` | **G**, fallback genérico. |
| `off_1on1` | `battle_lose`, `battle_start`, `battle_win`, `get_enemy`, `ranking`, `reset_chance`, `reset_top`, `set_defence_deck`, `top` | **F/P**, `BattleController`; escalera, rival, mazo e historial; economía parcial. |
| `off_1on1_btl_shop` | `buy`, `top`, `update` | **P**, tienda vacía. |
| `off_team` | `battle_lose`, `battle_prolong`, `battle_start`, `battle_win`, `get_enemy`, `prepare_battle_start`, `ranking`, `reset_chance`, `reset_top`, `set_defence_deck`, `set_offence_deck`, `top` | **F/P**, `TeamBattleController`; escalera, rivales y mazos; economía parcial. |
| `off_team_btl_shop` | `buy`, `top`, `update` | **P**, tienda vacía. |
| `online_1on1` | `battle_end`, `battle_start`, `create_or_enter_room`, `decide_room`, `history`, `leave_room`, `report`, `top` | **F**, `OnlineController`; matchmaking HTTP, historial y Photon. |
| `online_2on2` | `battle_end`, `battle_start`, `create_or_enter_room`, `decide_room`, `history`, `leave_room` | **F**, `OnlineController`; matchmaking HTTP, historial y Photon. |
| `pow` | `action`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `change_enemy`, `rescue`, `start`, `top` | **P**, contrato estructural sin progreso completo. |
| `practice_1on1` | `battle_lose`, `battle_start`, `battle_win` | **P**, payload inicial estático y cierres mínimos. |
| `practice_own` | `battle_end`, `history` | **P**, historial vacío. |
| `present` | `ad_movie_grant`, `grant`, `review_grant` | **C**, respuesta de usuario; no hay concesión económica completa. |
| `ranking` | `get_online_ranking`, `get_unit_ranking` | **F** para ranking online en `OnlineController`; ranking de unidades es **C**. |
| `search` | `get`, `start`, `top` | **P**, objeto de búsqueda vacío. |
| `setting` | `change_setting`, `kpi`, `remove_black_list`, `top`, `upload_push`, `upload_save_data`, `withdrawal_guild` | **F/P** entre `CoreController`, `SocialController` y `GuildController`; KPI/push son aceptación mínima. `upload_save_data` persiste el respaldo Base64 opaco mediante `accounts.py`. |
| `sneak` | `battle_decide`, `battle_lose`, `battle_start`, `battle_win`, `reset`, `reset_pay`, `top` | **G**, sin controlador de campaña Sneak. |
| `sneak_shop` | `buy`, `top`, `update` | **P**, tienda vacía. |
| `stamina_shop` | `buy`, `top` | **P**, tienda vacía. |
| `story` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `raid`, `reset_challenge_num`, `top` | **P**, mapa/historia estáticos y resultado mínimo. |
| `support` | `buy_relief_supplies`, `buy_support_item`, `top` | **C**, respuesta de usuario sin economía completa. |
| `title` | `delete_account`, `get_file_list`, `get_master_table`*, `get_medal_info`, `get_pack_info`, `get_push_setting`, `login`, `push`, `regist_user`, `takeover`, `transfer` | **F/P** para registro, login, catálogo, tablas, pack, push y medallas mediante `CoreController`/`AssetController`; `delete_account` es **G**. `takeover` y `transfer` implementan emisión y consumo de credenciales, respaldo y revocación en `accounts.py`; pendientes de validación nativa en dos dispositivos. Véase `operacion.md`. |
| `top_menu` | `get_daily_reward`, `get_reward`, `quest`, `top` | `top` es **F/P** en `CoreController`; las otras son **C**. |
| `training` | `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `top` | **P**, contrato estructural sin progreso completo. |
| `tutorial` | `request`, `select_affiliation_deck`, `select_affiliation_deck_top`, `select_unit`, `select_unit_top` | **F/P**, flujo aceptado por `CoreController`, sin persistencia completa de todas las elecciones. |
| `ultimate_mission` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `raid`, `reset_chance`, `reset_top`, `top` | **F/P**, `UltimateMissionController`; estado y contadores persistentes, recompensas simplificadas. |
| `unit_up` | `compose`, `equip`, `evolution`, `level_up`, `skill_up`, `status_seed_up`, `top`, `version_up` | **F/P**, `GameplayController`; semillas persisten, otras mejoras son aceptación parcial. |
| `world_map` | `battle_continue`, `battle_lose`, `battle_retire`, `battle_start`, `battle_win`, `raid`, `raid_rare_boss`, `reset_challenge_num`, `reset_top`, `top` | **P**, mapa estático y resultados mínimos. |

\* `/title/get_master_table` no aparece como literal del mismo modo que los
otros 391 RPC en el ELF; está confirmada por las 285 URLs del catálogo y por
el interceptor offline.

## Rutas adicionales del servidor

No forman parte del catálogo de RPC de producción del cliente:

| Ruta | Finalidad |
| --- | --- |
| `/__health`, `/__offline_status` | Diagnóstico local del backend y Photon. |
| `/user/get_units`, `/user/set_units` | Compatibilidad y administración del inventario preparado. |
| `/build_up/get_deck` | Lectura auxiliar de mazos persistidos. |
| `/snkp/msatk/prod/pack/0/pack_info_list.txt` y archivos de pack | Distribución de contenido estático. |
| `/snkp/msatk/prod/7130000/*` | Compatibilidad con la ruta heredada de DLC. |

Estas rutas no deben contarse como prueba de comportamiento original del
cliente. También existe un fallback global para `POST` desconocidos; su código
200 solo acredita transporte, no un contrato correcto.

## Qué tablas consume cada subsistema

Los endpoints de gameplay **no abren estas tablas en Python**. Son dependencias
lógicas: la escena del cliente consulta maestros locales y usa los IDs de la
respuesta dinámica. Solo `get_master_table` realiza una lectura directa de un
blob maestro. Para evitar repetir decenas de nombres se usan estos conjuntos:

- **BASE**: `m_setting`, `m_schedule`, `m_banner`, `m_display`, `m_campaign`,
  `m_string_*`, `m_web_link*`, `m_sound_data`.
- **UNIDAD**: `m_unit*`, `m_skill*`, `m_team*`, `m_equip*`, `m_item*`,
  `m_status_seed*`, `m_unique_action_condition`, `m_set_unit`, `m_build_up`.
- **BATALLA**: `m_stage*`, `m_bg*`, `m_enemy_deck`, `m_drop_item`,
  `m_invader_drop_item`, más **UNIDAD**.

| Subsistema / rutas | Tablas o conjuntos relacionados | Fuente dinámica del backend | Tipo de relación |
| --- | --- | --- | --- |
| `title/get_file_list` | Catálogo completo de 285 nombres | `server_content/file_list.json` o bundle offline | Directa. |
| `title/get_master_table` | La tabla exacta de cada `table[]`; admite varias | `server_content/files/<md5(nombre)>` o bundle offline | Directa y confirmada. |
| Registro, login, ajustes y menú | **BASE**, `m_level`, `m_vip_rank`, `m_login_reward_contents`, `m_extra_login_bonus*`, `m_quest*`, `m_daily_quest` | defaults, perfil e inventario del jugador | Dependencia de presentación/reglas. |
| `build_up`, `unit_up` | **UNIDAD**, `m_customize_affiliation`, `m_unit_make` | `user_units.json`, `user_deck.json`, perfil | Dependencia fuerte por IDs; escritura dinámica confirmada solo en operaciones indicadas arriba. |
| `gacha` | `m_gacha*`, `m_banner`, `m_schedule`, **UNIDAD** | inventario y respuesta generada | Maestros definen línea/precio; backend decide resultado dinámico. |
| `arena` | `m_arena_class`, `m_arena_honor`, `m_arena_order_reward`, `m_arena_rank_reward`, `m_arena_reward`, `m_arena_season`, `m_arena_setting`, **BATALLA** | `arena.json`, salas en memoria | Temporada y reglas locales + estado/rival remoto. |
| `battle_tactics` | `m_battle_tactics_*`, **UNIDAD**, `m_schedule` | `battle_tactics.json`, salas en memoria | Temporada, Draft, tienda y recompensas locales + estado remoto. |
| `online_1on1`, `online_2on2` | `m_online_class`, `m_ranking_reward`, **BATALLA** | `online.json`, mazos, matchmaking en memoria, Photon UDP | Clase/reglas locales + participantes e historial remotos. |
| `friend_online_1on1`, `guild_online_2on2` | **BATALLA** y datos visuales sociales | `social.json`, `guilds.json`, salas en memoria, Photon UDP | No hay tabla maestra de sala; el backend aporta jugadores y relay. |
| `off_1on1`, `off_team` | `m_period_battle`, `m_period_battle_ranking_reward`, `m_off_reset_time`, **BATALLA** | estado y escalera JSON por modo | Reglas locales + ranking, rival y mazos remotos. |
| `friend`, `mypage`, `mail` | `m_avatar`, `m_chat_stamp`, `m_string_menu` | `social.json`, `chat_world.json`, `chat_private.json`, `chat_guild.json`, notificaciones por jugador | Casi todo el contenido es dinámico; maestros solo presentan avatar/stamps/textos. |
| `guild`, `guild_battle`, `guild_coop` | `m_guild_soldier`, `m_guild_raid_map`, `m_guild_raid_stage`, **BATALLA** | `guilds.json` y datos sociales | Guild Coop carece hoy de contrato de servidor aunque comparte maestros de gremio/combate. |
| `world_map` | `m_world_map`, `m_world_area_map`, `m_world_map_adv`, `m_weekboss*`, `m_rare_boss*`, **BATALLA** | `server_content/world_map.json` y respuesta calculada | Mapa/reglas locales; progreso del servidor es parcial. |
| `story` | `m_story_adv`, `m_story_chapter_map`, `m_story_map`, `m_string_adv_*`, **BATALLA** | `server_content/story.json` | Guion/mapa local; progreso remoto parcial. |
| `pow` | `m_pow_adv_script`, `m_pow_reward_clear`, `m_pow_reward_stage`, `m_pow_week`, **BATALLA** | respuesta temporal sin estado completo | Dependencia lógica confirmada por familia. |
| `sneak` | `m_sneak_area_stage`, `m_sneak_level_area`, **BATALLA** | solo fallback hoy | Tener tablas no hace funcional la campaña. |
| `search` | `m_search`, **BATALLA** | respuesta vacía | Dependencia lógica. |
| `boot_camp` | `m_boot_camp`, `m_boot_camp_reward`, **UNIDAD** | respuesta vacía | Dependencia lógica. |
| `training` | `m_training`, `m_training_stage`, **BATALLA** | respuesta vacía | Dependencia lógica. |
| `practice_*`, `labo` | **UNIDAD**, **BATALLA** | fixture de práctica e historiales vacíos | No existe una familia maestra `m_practice_*`. |
| Tutorial | `m_tutorial_select_affiliation_deck`, `m_tutorial_select_unit`, `m_string_tutorial`, **UNIDAD** | usuario/defaults | Selección local cruzada con respuesta del usuario. |
| `ultimate_mission` | `m_ultimate_mission_adv_script`, `m_ultimate_mission_deck`, `m_ultimate_mission_enemy`, `m_ultimate_mission_setting`, **BATALLA** | `ultimate_mission.json` por jugador | Regla/guion local + contadores remotos. |
| `event/marathon` | `m_event_master*`, `m_marathon_*`, **BASE**, **BATALLA** | payload mínimo no persistente | Familia exacta confirmada. |
| `event/marathon2nd` | `m_event_master*`, `m_marathon2nd_*`, **BASE**, **BATALLA** | payload mínimo no persistente | Familia exacta confirmada. |
| `event/extension` | `m_event_master*`, `m_extension_*`, **BASE**, **BATALLA** | objeto vacío | Familia exacta confirmada. |
| `event/mass` | `m_event_master*`, `m_mass_*`, **BASE**, **BATALLA** | `snatch_wars.json` + gremio | Snatch Wars reutiliza la familia Mass. |
| `event/pile` | `m_event_master*`, `m_pile_*`, **BASE**, **BATALLA** | objeto vacío | Familia exacta confirmada. |
| `event/recapture` | `m_event_master*`, `m_recapture_*`, **BASE**, **BATALLA** | objeto vacío | Familia exacta confirmada. |
| `event/tforce` | `m_event_master*`, `m_tforce_*`, **BASE**, **BATALLA** | objeto vacío | Familia exacta confirmada. |
| `event/tline` | `m_event_master*`, `m_tline_*`, **BASE**, **BATALLA** | objeto vacío | Familia exacta confirmada. |
| `event/united/*` | `m_event_master*`, `m_united_*`, **BASE**, **BATALLA** | objeto vacío; sin salas específicas | Familia exacta confirmada; HTTP multijugador incompleto. |
| `event/melee`, `event/score_attack` | `m_event_master*`, **BASE**, **BATALLA**; no existen `m_melee_*` ni `m_score_attack_*` actuales | fallback/objeto vacío | Asociación concreta no verificable con el catálogo vigente. |
| Tiendas de batalla/gremio/sneak | `m_shop_group`, `m_sale`, `m_fix_item`, **UNIDAD** | respuestas de tienda vacías | Catálogo local + stock/precio remoto esperado. |
| `level_up_shop` | `m_level_up_shop`, **UNIDAD** | fallback | Tabla presente, contrato ausente. |
| `limited_offer` | `m_limited_offer*`, `m_string_limited_offer`, **UNIDAD** | fallback | Tablas presentes, contrato ausente. |
| `mars_shop` | `m_mars_shop_*`, **UNIDAD** | fallback | Tablas presentes, contrato ausente. |
| `loto` | `m_loto_reward`, **UNIDAD** | compatibilidad superficial | Tabla presente, lista dinámica ausente. |
| Otras tiendas (`normal`, `medal`, `msp`, `merchant`, `daily_sale`, `dress_up`, `integrated`) | `m_shop_group`, `m_sale`, `m_dress_up_shop`, **UNIDAD**, según pantalla | mayoritariamente fallback | La asociación exacta de cada stock debe confirmarse con una traza real. |
| `present` y recompensas | `m_login_reward_contents`, `m_extra_login_bonus*`, `m_first_item`, `m_quest*`, **UNIDAD** | usuario/notificaciones | La concesión económica está incompleta. |

Una lista y el formato interno de las 285 tablas se mantienen en
[Tablas maestras](tablas-maestras.md). Las asociaciones marcadas como familia
exacta se apoyan en nombres y contratos coincidentes; cuando no hay familia
dedicada se indica expresamente en vez de inventar una relación.

## Cobertura del interceptor offline

El `OfflineServer` del cliente no representa todo el API de producción. Se
confirmaron **75 rutas literales** de las 392 inventariadas:

- todas las rutas listadas de `build_up` (5), `gacha` (9), `guild_shop` (3),
  `labo` (2), `practice_1on1` (3), `practice_own` (2), `setting` (7),
  `sneak_shop` (3), `story` (8), `tutorial` (5), `unit_up` (8) y
  `world_map` (10);
- `/friend/top` y `/top_menu/top`;
- `title/get_file_list`, `get_master_table`, `get_medal_info`,
  `get_pack_info`, `get_push_setting`, `login`, `push` y `regist_user`.

Las demás rutas pueden salir al backend externo dependiendo de la
configuración del cliente. Clases auxiliares del modo offline pueden generar
respuestas adicionales sin que su URI aparezca como un caso literal en el
despachador; por eso no se suman a las 75 sin una traza de ejecución.

En particular:

- `get_file_list` y `get_master_table` pueden quedar completamente resueltos
  dentro del APK, sin tocar `server_content`.
- que una ruta aparezca en el ELF no significa que el interceptor tenga una
  respuesta para ella;
- que el backend devuelva código 200 no significa que el cliente haya recibido
  los campos que la escena espera;
- para determinar la fuente efectiva de una sesión hay que correlacionar la
  ruta observada con la configuración del cliente y, para maestros, con su
  versión/caché instalada.

## Telemetría disponible y límites

Por defecto `ServerLog.log` registra método, ruta normalizada, IP de transporte
y tamaño del cuerpo. `UserLog.log`, `Nicknames.log` y los archivos de chat son
datos operativos privados y no forman parte de este informe ni del repositorio.

`MSA_CAPTURE_HTTP_DIAGNOSTICS=1` permite guardar solicitudes bajo
`server_data/requests` y el directorio privado de cada cliente. Debe usarse
solo en un entorno controlado: puede contener parámetros y cuerpos de juego.
La captura actual no ofrece métricas agregadas de latencia, código de estado o
frecuencia, así que este documento no debe interpretarse como estadísticas de
uso real.

Para validar una ruta al implementarla se debe registrar, como mínimo:

1. escena y pasos que la disparan;
2. método, ruta y parámetros, con secretos/identificadores anonimizados;
3. origen efectivo: offline, HTTP externo o Photon;
4. respuesta y transición siguiente del cliente;
5. maestros consultados por la escena e IDs cruzados;
6. archivos dinámicos leídos/escritos;
7. prueba automática del contrato y prueba pendiente o realizada en dispositivo.

## Prioridades que revela el mapa

1. El fallback genérico oculta rutas sin implementar. Conviene convertir cada
   familia priorizada en un contrato explícito y probar los campos esperados.
2. `guild_coop`, `guild_online_1on1`, Sneak y varias tiendas tienen rutas en el
   cliente y maestros relacionados, pero carecen de lógica real.
3. Extra Ops, POW, Training, Story y World Map devuelven estructuras parciales;
   una respuesta 200 no acredita persistencia, recompensas ni recorrido final.
4. Las tablas y los JSON dinámicos necesitan validación cruzada de IDs. No hay
   base de datos ni claves foráneas que impidan referencias rotas.
5. La validación final de online requiere dispositivos y Photon; las pruebas
   HTTP únicamente cubren coordinación y forma de respuesta.

## Special Ops y avisos nativos: rastreo del 5/9/2026

Evidencia estática del `libAppMain.so` ARM64 del workspace, mediante
`tools/analyze_online_elf.py` (herramienta privada). Las direcciones siguientes
son virtuales de ese binario. No equivalen a una captura en dispositivo.

### Special Ops: cooperativo de gremio

`SceneOps::InitSpecialOps` (0x16b8f58) consulta
`GuildMemberCoopBattleAccessor`: etapas, jugadores, estados y tiempo restante.
`SceneWorldMap::checkSpecialOps` (0x16e32ec) consulta el evento cooperativo del
usuario. **No corresponde a Extra Ops/Marathon**; publicar un evento Marathon
no implementa esta modalidad.

| Solicitud nativa | Entrada específica | Respuesta consumida |
| --- | --- | --- |
| `/guild_coop/top` | `coop_id`, `ignore_error` | `guild`, `info`, `rental_soldier` |
| `/guild_coop/member_coop_request` | `member[]` | `info` |
| `/guild_coop/stage_keep` | `coop_id`, `stage_id` | `info` |
| `/guild_coop/member_coop_battle_start` | `coop_id`, `stage_id`, `items[]`, `soldier_user_id`, `soldier_unit_id`, `soldier_unit_level`, `cover`, `deck_no`, `unit_ids[]`, `unit_level[]` | `enemy_data` (cadena), `relief_supplies_contents` |
| `/guild_coop/member_coop_battle_end` | `coop_id`, `stage_id`, `enemy_data`, `eagle_eye`, `hawk_eye` | `user`, `reward` (arreglo), `rental_soldier` |

`parseGuildMemberCoopBattle` (0x13b75e8) requiere en `info`:

- `event`: fecha fin, convertida a `DateTime`.
- `help_remain`: número de ayudas disponibles.
- `person`: arreglo de perfiles de ayudantes con contrato `parsePerson`.
- `playable`: indicador de disponibilidad.
- `stage`: arreglo de objetos con `stage_id`, `player` (arreglo de perfiles),
  `state` y `rest_time` (fecha de liberación).

El menú publica además `guild_member_coop_btl_event` y
`guild_member_coop_btl_identifier`. Actualmente el servidor entrega la hora
actual e ID cero y **carece de controlador `guild_coop`**. Por tanto no hay
operación activa, reserva de etapas ni combate cooperativo reproducible.
`GuildCoop_MemberCoopBattleStart::extractResponseJson` (0x1433314) pasa la
cadena `enemy_data` a `EnemyContext::setData`; `getParamList` (0x1338c34)
separa y convierte parámetros numéricos. No basta responder `{}` o un JSON
arbitrario para crear enemigos.

Para implementarlo queda resolver la generación de ese contexto enemigo y
su relación exacta con los maestros de etapas; después persistir operaciones,
participantes autorizados, reservas con vencimiento y resultados idempotentes.
La relación genérica con maestros de gremio indicada antes en este informe
no demuestra por sí sola qué fila genera cada etapa de Special Ops. Este
rastreo no habilita la modalidad ni certifica un combate en dispositivo.

### Avisos de Chat y otros iconos

`parseMessageLast` (0x13bdec8) exige **un objeto de cuatro cadenas**:

```json
{"last_message_date":{"world":"","guild":"","personal":"","system":""}}
```

A pesar del nombre, son marcas opacas: `MyPageUtility::isNewMessageFlag`
(0x1367490) las compara por `strcmp` con el respaldo local.
`clearNewMessageFlag` (0x1367560) copia la marca actual al respaldo. El
servidor debe mantenerlas estables entre consultas y cambiarlas cuando llega
un mensaje visible. La lectura local de cada canal controla el apagado del
aviso; no se necesita enviar un campo inventado `exclamation`.

Se corrigieron `/top_menu/top`, `/mypage/get_message_info`, `/mypage/chat` y
`/guild_battle/top` para devolver este contrato. Se usan IDs persistidos de
mensajes entrantes, filtrando remitente propio, destinatario, lista negra y
gremio. Las invitaciones y solicitudes existentes viajan por su canal privado
o de gremio y actualizan su marca. `system` queda vacío porque el servidor
no implementa un canal 4. El buzón mantiene un estado de lectura independiente.
No hay push instantáneo del servidor: la marca se actualiza cuando el cliente
consulta los endpoints correspondientes.

| Aviso | Fuente nativa comprobada | Situación |
| --- | --- | --- |
| Chat mundial, gremio y privado | `last_message_date.world/guild/personal` | Contrato corregido; pendiente ver icono y apagado en dispositivo |
| Buzón | `top_menu.unread_mail` | Ya refleja notificaciones informativas persistidas |
| Novedades de eventos | `top_menu.unread_event` | Continúa en cero; falta estado de novedades y lectura de eventos |
| Crank gratuito | `top_menu.is_free_gacha_play` | Continúa en cero; falta elegibilidad de tirada gratuita autoritativa |
| Misiones y diarias | `is_quest_reward`, `is_daily_quest_reward`, `is_daily_quest_achieved` | Continúan en cero; falta progreso/recompensa reclamable |
| Oferta diaria | `is_available_daily_sale_flag` | Continúa en cero; falta disponibilidad comercial real |
| Exploración terminada | `SearchUtility::isFinishedSearching` llamado desde `MenuButton_ExclamCheck` | Depende de la exploración; sus endpoints aún son parciales |
| Nuevo modo, historias, eventos y ofertas | `TopMenuUtility::isUpdateMark` y respaldo local | Combinan estado local, nivel y contenido; no existe un único interruptor servidor |

`parseTopMenu` confirma los nombres de los indicadores anteriores;
`SceneMainMenu::MenuButton_ExclamCheck` (0x1618e88) confirma las comprobaciones
de exploración, nivel, historia y marcas locales. Poner todos los indicadores
a uno produciría avisos de recompensas o contenido no disponible. La
reactivación completa de esos indicadores sigue pendiente de implementar
sus condiciones de negocio; únicamente las marcas sociales se corrigieron
en esta intervención.
