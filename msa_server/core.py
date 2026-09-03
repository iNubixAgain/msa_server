"""Contratos de inicio, menú principal y ajustes del perfil."""

from __future__ import annotations

from typing import Mapping

from .config import MASTER_VERSION, PUBLIC_HOST, PUBLIC_PORT, SERVER_CONTENT
from .clock import jst_now_string
from .identity import (INVALID_TERMINAL_IDS, public_player_id,
                       registration_terminal_id)
from .notifications import InGameNotificationController
from .guild import guild_name_for_client
from .profiles import get_profile, profile_detail, update_profile
from .sessions import UserTracker
from .social import SocialController
from .storage import ensure_client_defaults, read_json_file
from .users import ok_payload, user_payload


class CoreController:
    """Atiende rutas base sin depender de BaseHTTPRequestHandler."""

    def __init__(self, social: SocialController | None = None,
                 tracker: UserTracker | None = None,
                 notifications: InGameNotificationController | None = None) -> None:
        self.social = social
        self.tracker = tracker
        self.notifications = notifications

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               source_ip: str, headers: Mapping[str, str],
               ok: dict, registration_token: str = "") -> dict | None:
        if path == "/title/get_pack_info":
            host_header = headers.get("Host", "").strip() if headers else ""
            pack_host = host_header if host_header else f"{PUBLIC_HOST}:{PUBLIC_PORT}"
            return {"version": 0,
                    "url": f"http://{pack_host}/snkp/msatk/prod/pack/0/",
                    "response": ok}
        if path == "/title/get_file_list":
            catalog = read_json_file(SERVER_CONTENT / "file_list.json", [])
            return {"master_ver": MASTER_VERSION,
                    "dl_mtbl_merge_lim_size": 2097152,
                    "max_dl_stream_num_for_mtbl": 1,
                    "max_dl_stream_num_for_dlc": 4,
                    "response": ok, "file_list": catalog}
        if path == "/title/regist_user":
            ensure_client_defaults(client, f"PLAYER {source_ip}")
            terminal_id = headers.get("terminal_id", "").strip()
            if terminal_id.lower() in INVALID_TERMINAL_IDS:
                # El cliente conserva este valor y lo envía en las siguientes
                # solicitudes; debe ser único para cada instalación.
                terminal_id = registration_terminal_id(
                    headers, registration_token
                )
            return {"terminal_id": terminal_id, "ab_test": [], "response": ok}
        if path == "/title/login":
            ensure_client_defaults(client, f"PLAYER {source_ip}")
            return {"user": user_payload(
                        public_player_id(client), f"PLAYER {source_ip}", client
                    ),
                    "login_num": 1, "login_reward": [],
                    "vip_reward_result": 0, "pre_vip_level": 0,
                    "extra_login_bonus": [], "response": ok}
        if path == "/title/get_medal_info":
            return {"free_medal_num": 9999999, "medal_num": 9999999,
                    "response": ok}
        if path == "/title/get_push_setting":
            return {"push_kind": [], "response": ok}
        if path in {"/tutorial/request", "/tutorial/select_affiliation_deck",
                    "/tutorial/select_affiliation_deck_top", "/tutorial/select_unit",
                    "/tutorial/select_unit_top"}:
            return {"response": ok}
        if path in {"/setting/upload_save_data", "/setting/upload_push",
                    "/setting/kpi"}:
            return {"response": ok}
        if path == "/top_menu/top":
            now = str(ok.get("now_time") or jst_now_string())
            social_status = (
                self.social.menu_status(client) if self.social else {
                    "last_message_date": now, "current_friend_num": 0,
                }
            )
            notification_status = (
                self.notifications.status(client) if self.notifications else {
                    "unread_mail": 0,
                }
            )
            top_menu = {
                "unread_mail": notification_status["unread_mail"],
                "unread_event": 0, "is_free_gacha_play": 0,
                "is_quest_reward": 0, "is_daily_quest_reward": 0,
                "is_daily_quest_achieved": 0, "is_available_daily_sale_flag": 0,
                "event_master": [],
                "latest_event_id": 0,
                "latest_event_type": 0,
                "fix_ranking_season": 0,
            }
            return {
                "is_login_reward": 0, "user": user_payload(client=client),
                "top_menu": top_menu,
                "last_message_date": social_status["last_message_date"],
                "timesale": [],
                "guild_member_coop_btl_event": now,
                "guild_member_coop_btl_identifier": 0,
                "merchant_of_death1_event": now, "merchant_of_death2_event": now,
                "ad_movie_date": now, "is_paying": 0,
                "monthly_card_expiration_date": now, "push_kind": [],
                "current_friend_num": social_status["current_friend_num"],
                "unit_ranking_hash": "", "ab_test": [],
                "url_param": "", "response": ok,
            }
        if path == "/setting/top":
            profile = get_profile(client, f"PLAYER {client}")
            return {"user": user_payload(client=client),
                    "detail": profile_detail(profile, guild_name_for_client(client)),
                    "black_list": (self.social.blacklist(client)
                                   if self.social else []),
                    "guild": {}, "response": ok}
        if path == "/setting/change_setting":
            old_name = get_profile(client, f"PLAYER {source_ip}")["name"]
            profile = update_profile(params, client, f"PLAYER {source_ip}")
            new_name = profile.get("name", "")
            if self.tracker is not None:
                if "name" in params and old_name != new_name:
                    self.tracker.record_nickname_change(client, source_ip, old_name, new_name)
                else:
                    self.tracker.update_nickname(client, new_name)
            return {"user": user_payload(client=client),
                    "detail": profile_detail(profile, guild_name_for_client(client)),
                    "response": ok}
        if path == "/title/push":
            return ok_payload(ok)
        return None
