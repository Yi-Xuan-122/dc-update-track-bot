import discord
from discord import ui
import logging
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.summary.summary_command import summarizerCog
logger = logging.getLogger(__name__)

class summary_check_view(ui.View):
    def __init__(self):
        super().__init__(timeout=60)
        self.value = None

    @ui.button(label="✅Yes", style=discord.ButtonStyle.success, custom_id="summary_yes") # yes
    async def summary_yes(self, interaction: discord.Interaction, button: ui.Button):
        self.value = True
        self.stop()

    @ui.button(label="❌No", style=discord.ButtonStyle.danger, custom_id="summary_no")
    async def summary_no(self, interaction: discord.Interaction, button: ui.Button):
        self.value = False
        self.stop()

    async def on_timeout(self):
        self.value = None
        self.stop()
