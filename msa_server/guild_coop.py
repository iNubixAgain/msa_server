"""Native Special Ops entry contract; combat generation is not implemented."""
from .content import rental_soldier


class GuildCoopController:
    def __init__(self, guild):
        self.guild = guild

    def handle(self, path, client, params, ok):
        if path != "/guild_coop/top":
            return None
        # Reuse the authorized membership view; never resolve another
        # player's operation from an arbitrary coop_id supplied by a client.
        guild = self.guild.handle("/guild_battle/top", client, {}, ok)["guild"]
        return {
            "guild": guild,
            "info": {
                "event": "2023-01-12 15:30:00",
                "help_remain": 0, "person": [], "playable": False, "stage": [],
            },
            "rental_soldier": rental_soldier(), "response": ok,
        }
