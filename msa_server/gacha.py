"""Contratos visuales de Crank/Gacha sin mutaciones de inventario."""

from __future__ import annotations

import random

from .users import user_payload


_RNG = random.SystemRandom()
_PLAY_ROUTES = {
    "/gacha/play", "/gacha/free_play", "/gacha/ticket_play",
    "/gacha/select_play",
}


_UNIT_ITEM_RANGES = (
    (94,140),(142,147),(149,160),(162,171),(173,179),(181,194),(196,208),(210,211),(213,220),(225,233),(238,240),(245,247),(303,306),(311,312),(314,315),(317,320),(339,345),(354,354),(405,416),(433,441),(446,455),(464,475),(588,597),(606,612),(625,634),(647,650),(652,657),(674,684),(697,708),(775,784),(797,806),(815,816),(818,825),(834,845),(854,863),(869,878),(943,950),(960,966),(1075,1082),(1133,1141),(1167,1174),(1191,1203),(1228,1235),(1302,1310),(1331,1339),(1406,1417),(1442,1449),(1466,1475),(1496,1503),(1620,1631),(1660,1672),(1690,1701),(1873,1886),(1904,1911),(2032,2039),(2063,2072),(2094,2101),(2121,2128),(2151,2158),(2247,2254),(2277,2284),(2308,2315),(2337,2345),(2363,2370),(2392,2403),(2429,2436),(2459,2466),(2489,2496),(2564,2571),(2590,2597),(2618,2625),(2645,2653),(2673,2676),(2692,2703),(2717,2724),(2793,2800),(2819,2826),(2846,2854),(2874,2881),(2951,2959),(2974,2981),(2999,3010),(3034,3041),(3154,3162),(3178,3185),(3201,3209),(3229,3235),(3248,3259),(3280,3287),(3303,3311),(3327,3333),(3347,3354),(3371,3378),(3394,3403),(3419,3425),(3438,3445),(3511,3514),(3522,3532),(3553,3561),(3577,3584),(3601,3607),(3619,3628),(3672,3680),(3746,3753),(3774,3781),(3797,3804),(3821,3828),(3848,3855),(3871,3879),(3945,3953),(3977,3980),(3991,3996),(4008,4016),(4028,4035),(4050,4057),(4072,4077),(4088,4095),(4214,4220),(4231,4237),(4249,4259),(4278,4285),(4305,4312),(4327,4334),(4349,4355),(4369,4376),(4391,4397),(4412,4418),(4430,4437),(4452,4459),(4474,4480),(4591,4597),(4612,4618),(4629,4636),(4651,4656),(4667,4674),(4689,4697),(4712,4717),(4728,4734),(4745,4752),(4771,4777),(4788,4794),(4809,4818),(4833,4840),(4856,4863),(4878,4883),(4895,4904),(5082,5091),(5105,5111),(5121,5133),(5155,5162),(5172,5179),(5189,5196),(5210,5217),(5227,5234),(5245,5253),(5263,5270),(5284,5291),(5301,5308),(5318,5318),
)
_UNIT_ITEM_IDS = tuple(item_id for first, last in _UNIT_ITEM_RANGES
                       for item_id in range(first, last + 1))
_MSP_ITEM_IDS = (94,102,103,104,105,106,108,110,112,120,123,130,131,133,134,135,137,138,143,162,165,166,169,170,174,185,189,190,202,228,239,342,454,597,609,708,863,1141,1672,2345,2653,2959,3209,3311,3378,3561,3680,3879,4016,4285,4437,4697,5090,5253)
_MEDAL_ITEM_IDS = (96,97,100,109,119,126,128,139,140,142,149,156,171,173,174,179,194,203,205,206,207,211,213,215,216,217,218,219,220,225,226,227,229,231)


def _requested_num(params: dict[str, list[str]]) -> int:
    try:
        value = int(params.get("num", ["1"])[0])
    except (TypeError, ValueError, IndexError):
        value = 1
    # El cliente solamente dispone de presentaciones de una y diez tiradas.
    return 10 if value >= 10 else 1


def _gacha_data() -> list[dict]:
    return [
        {"gacha_id": 1, "sale": 0, "money": 3, "price": 10000,
         "num": 1, "remain_time": 0, "free_num": 0,
         "end_time": "2099-12-31 23:59:59", "is_playable": 1},
        {"gacha_id": 1, "sale": 0, "money": 3, "price": 90000,
         "num": 10, "remain_time": 0, "free_num": 0,
         "end_time": "2099-12-31 23:59:59", "is_playable": 1},
        {"gacha_id": 2, "sale": 0, "money": 2, "price": 300,
         "num": 1, "remain_time": 0, "free_num": 0,
         "end_time": "2099-12-31 23:59:59", "is_playable": 1},
        {"gacha_id": 2, "sale": 0, "money": 2, "price": 2600,
         "num": 10, "remain_time": 0, "free_num": 0,
         "end_time": "2099-12-31 23:59:59", "is_playable": 1},
    ]


def _premium_data() -> list[dict]:
    """Entradas Premium compatibles con el gacha 14 del maestro local."""
    return [{
        "gacha_data": {
            "gacha_id": 14, "sale": 0, "money": 2, "price": 300,
            "num": 1, "remain_time": 0, "free_num": 0,
            "end_time": "2099-12-31 23:59:59", "is_playable": 1,
        },
        "rewards": [],
        "total_num": 0,
    }, {
        "gacha_data": {
            "gacha_id": 14, "sale": 0, "money": 2, "price": 2600,
            "num": 10, "remain_time": 0, "free_num": 0,
            "end_time": "2099-12-31 23:59:59", "is_playable": 1,
        },
        "rewards": [],
        "total_num": 0,
    }]


class GachaController:
    """Genera resultados reconocibles, deliberadamente no transaccionales."""

    @staticmethod
    def handles(path: str) -> bool:
        return path.startswith("/gacha/")

    @staticmethod
    def _base(client: str, ok: dict) -> dict:
        return {
            "user": user_payload(client=client),
            "gacha_data": _gacha_data(),
            "gacha_box_data": [],
            "gacha_step_up_data": [],
            "gacha_select_data": [],
            "gacha_fever_data": [],
            "gacha_premium_data": _premium_data(),
            "gacha_supporter_data": [],
            "gacha_vip_bonus": [],
            "response": ok,
        }

    def handle(self, path: str, client: str, params: dict[str, list[str]],
               ok: dict) -> dict | None:
        if not self.handles(path):
            return None

        payload = self._base(client, ok)
        if path == "/gacha/top":
            return payload

        if path not in _PLAY_ROUTES:
            payload.update({"result": self._empty_result(), "performance": 0})
            return payload

        try:
            gacha_id = int(params.get("gacha_id", ["2"])[0])
        except (TypeError, ValueError, IndexError):
            gacha_id = 2

        # MSP y Medal respetan sus lineups maestros. Premium (id 14) usa el
        # catálogo completo de ev_item_id de unidades, nunca iconos generales.
        catalogue = {
            1: _MSP_ITEM_IDS,
            2: _MEDAL_ITEM_IDS,
            14: _UNIT_ITEM_IDS,
        }.get(gacha_id, _UNIT_ITEM_IDS)
        items = [
            {"item_id": _RNG.choice(catalogue),
             "quantity": 80, "act": 0}
            for _ in range(_requested_num(params))
        ]
        result = self._empty_result()
        result["item"] = items
        payload.update({"result": result, "performance": 0})
        return payload

    @staticmethod
    def _empty_result() -> dict:
        return {
            "item": [],
            "item_extra": [],
            "box_reward_item": [],
            "box_parts_reward_item": [],
            "supporter_reward_item": [],
        }
