import os
import discord
from discord.ext import commands
from discord import app_commands
import requests
import aiohttp
import time
from aiohttp import web
import asyncio
import typing
import urllib.parse

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
API_TOKEN = os.getenv("API_TOKEN", "default_insecure_token")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost")
SKIN_CHANNEL_ID = int(os.getenv("SKIN_CHANNEL_ID", "0"))

class PackSelectionView(discord.ui.View):
    def __init__(self, upload_id: str, discord_user_id: str, packs: list):
        super().__init__(timeout=None)
        self.upload_id = upload_id
        self.discord_user_id = discord_user_id
        for pack in packs:
            button = discord.ui.Button(label=pack, style=discord.ButtonStyle.primary, custom_id=f"sel_{upload_id[:8]}_{pack}")
            button.callback = self.make_callback(pack)
            self.add_item(button)
            
    def make_callback(self, pack_name: str):
        async def callback(interaction: discord.Interaction):
            if str(interaction.user.id) != str(self.discord_user_id):
                await interaction.response.send_message("❌ You can only select the pack for your own upload!", ephemeral=True)
                return
            
            await interaction.response.defer()
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{BACKEND_URL}/upload/{self.upload_id}/select_pack", params={"selected_pack": pack_name, "token": API_TOKEN}) as resp:
                    if resp.status == 200:
                        await interaction.message.delete()
                    else:
                        err = await resp.json()
                        await interaction.followup.send(f"❌ Error: {err.get('detail')}", ephemeral=True)
        return callback

class SkinBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)
        
    async def setup_hook(self):
        # Sync slash commands
        await self.tree.sync()
        
        # Start aiohttp web server for internal backend communication
        app = web.Application()
        app.router.add_post('/notify_preview', self.handle_notify)
        app.router.add_post('/notify_build', self.handle_notify_build)
        app.router.add_post('/notify_selection', self.handle_notify_selection)
        app.router.add_post('/notify_hourly_summary', self.handle_notify_hourly_summary)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, '0.0.0.0', 8080)
        await site.start()
        print("Internal Web Server started on port 8080")

    async def on_ready(self):
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print(f'Restricted to channel ID: {SKIN_CHANNEL_ID}')
        print('------')

    async def on_message(self, message):
        print(f"Message received! Author: {message.author}, Bot?: {message.author.bot}, Channel: {message.channel.id}, Attachments: {len(message.attachments)}")
        if message.author.bot:
            return

        if SKIN_CHANNEL_ID and message.channel.id != SKIN_CHANNEL_ID:
            return

        if not message.attachments:
            return

        for attachment in message.attachments:
            if attachment.filename.lower().endswith(('.zip', '.7z', '.rar')):
                await message.add_reaction("⏳")
                try:
                    file_bytes = await attachment.read()
                    data = aiohttp.FormData()
                    data.add_field('file', file_bytes, filename=attachment.filename)
                    data.add_field('discord_user_id', str(message.author.id))
                    data.add_field('discord_username', message.author.name)
                    
                    async with aiohttp.ClientSession() as session:
                        async with session.post(f"{BACKEND_URL}/upload", data=data) as resp:
                            if resp.status == 200:
                                await message.add_reaction("✅")
                            else:
                                err = await resp.json()
                                err_msg = err.get("detail", "Unknown error")
                                await message.reply(f"❌ Rejected: {err_msg}")
                                await message.add_reaction("❌")
                except Exception as e:
                    await message.reply(f"❌ Transfer error: {e}")

    async def handle_notify(self, request):
        try:
            data = await request.json()
            upload_id = data.get("upload_id")
            preview_url = data.get("preview_url")
            username = data.get("username", "A player")
            pack_name = data.get("pack_name", "default")
            
            if not SKIN_CHANNEL_ID:
                return web.json_response({"error": "SKIN_CHANNEL_ID not set in environment"}, status=500)
                
            channel = self.get_channel(SKIN_CHANNEL_ID)
            if not channel:
                return web.json_response({"error": "Channel not found"}, status=500)

            msg = f"✅ **{username}** uploaded a valid skin for the `{pack_name}` pack!"
            
            if preview_url:
                backend_preview = f"{BACKEND_URL}{preview_url}"
                async with aiohttp.ClientSession() as session:
                    async with session.get(backend_preview) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            with open(f"/tmp/{upload_id}.jpg", "wb") as f:
                                f.write(image_data)
                            file = discord.File(f"/tmp/{upload_id}.jpg", filename="preview.jpg")
                            await channel.send(content=msg, file=file)
                            os.remove(f"/tmp/{upload_id}.jpg")
                        else:
                            await channel.send(content=msg + "\n*(Preview image not found on the backend)*")
            else:
                await channel.send(content=msg + "\n*(No preview found in the archive)*")
                
            return web.json_response({"status": "ok"})
        except Exception as e:
            print(f"Error handling notification: {e}")
            print(f"Error handling notification: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_notify_selection(self, request):
        try:
            data = await request.json()
            upload_id = data.get("upload_id")
            discord_user_id = data.get("discord_user_id")
            username = data.get("username", "A player")
            matched_packs = data.get("matched_packs", [])
            
            if SKIN_CHANNEL_ID:
                channel = self.get_channel(SKIN_CHANNEL_ID)
                if channel:
                    view = PackSelectionView(upload_id, discord_user_id, matched_packs)
                    msg = f"Hey <@{discord_user_id}>! The car you uploaded is registered in multiple championships.\n**Please choose the championship for this skin:**"
                    await channel.send(content=msg, view=view)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_notify_build(self, request):
        try:
            data = await request.json()
            pack_name = data.get("pack_name", "default")
            
            if SKIN_CHANNEL_ID:
                channel = self.get_channel(SKIN_CHANNEL_ID)
                if channel:
                    encoded_pack_name = urllib.parse.quote(pack_name)
                    await channel.send(f"🚀 **New skinpack available !**\nThe `{pack_name}` pack has just been updated.\n[Download {pack_name} pack]({PUBLIC_URL}/packs/{encoded_pack_name}.zip?v={int(time.time())})")
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

    async def handle_notify_hourly_summary(self, request):
        try:
            data = await request.json()
            updated = data.get("updated", [])
            unchanged = data.get("unchanged", [])
            
            if SKIN_CHANNEL_ID:
                channel = self.get_channel(SKIN_CHANNEL_ID)
                if channel:
                    msg = "⏱️ **Hourly Skinpack Update**\n\n"
                    if updated:
                        msg += "✅ **Updated (New skins added):**\n"
                        for p in updated:
                            encoded_p = urllib.parse.quote(p)
                            msg += f" - [{p}]({PUBLIC_URL}/packs/{encoded_p}.zip?v={int(time.time())})\n"
                    else:
                        msg += "✅ **Updated:** *(No packs had new skins)*\n"
                        
                    msg += "\n➖ **Unchanged:**\n"
                    if unchanged:
                        for p in unchanged:
                            encoded_p = urllib.parse.quote(p)
                            msg += f" - [{p}]({PUBLIC_URL}/packs/{encoded_p}.zip?v={int(time.time())})\n"
                    else:
                        msg += " *(None)*\n"
                    await channel.send(msg)
            return web.json_response({"status": "ok"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

bot = SkinBot()

def is_correct_channel(interaction: discord.Interaction):
    if not SKIN_CHANNEL_ID:
        return True # If not configured, allow anywhere
    return interaction.channel_id == SKIN_CHANNEL_ID

@bot.tree.command(name="upload_skin", description="Generate a link to upload your Assetto Corsa skin")
async def upload_skin(interaction: discord.Interaction):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    upload_url = f"{PUBLIC_URL}?user_id={interaction.user.id}&username={interaction.user.name}"
    
    await interaction.response.send_message(
        f"Hi {interaction.user.name} ! Discord limits file sizes.\n"
        f"You can upload your skin directly here:\n"
        f"🔗 **{upload_url}**\n\n"
        f"*(The system will automatically detect which championship the car belongs to!)*",
        ephemeral=True
    )

async def pack_autocomplete(interaction: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BACKEND_URL}/packs", params={"token": API_TOKEN}) as response:
                if response.status == 200:
                    packs = await response.json()
                    return [
                        app_commands.Choice(name=pack, value=pack)
                        for pack in packs.keys() if current.lower() in pack.lower()
                    ][:25]
    except Exception:
        pass
    return []

@bot.tree.command(name="build_pack", description="Ask the server to compile the final .zip pack")
@app_commands.default_permissions(administrator=True)
@app_commands.describe(pack_name="The pack to compile (optional, if omitted, builds all pending packs)")
@app_commands.autocomplete(pack_name=pack_autocomplete)
async def build_pack(interaction: discord.Interaction, pack_name: typing.Optional[str] = None):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return

    await interaction.response.defer()
    
    try:
        if pack_name:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{BACKEND_URL}/build", params={"pack_name": pack_name, "token": API_TOKEN}) as response:
                    if response.status != 200:
                        try:
                            err = await response.json()
                            err_msg = err.get("detail", "Unknown error")
                        except Exception:
                            err_msg = f"HTTP {response.status}"
                        await interaction.followup.send(f"❌ Error: {err_msg}")
                        return
            encoded_pack_name = urllib.parse.quote(pack_name)
            await interaction.followup.send(f"⏳ The compilation of the `{pack_name}` pack has started! The archive will soon be available here: [Download {pack_name}]({PUBLIC_URL}/packs/{encoded_pack_name}.zip?v={int(time.time())})")
        else:
            async with aiohttp.ClientSession() as session:
                async with session.post(f"{BACKEND_URL}/build_all", params={"token": API_TOKEN}) as response:
                    if response.status != 200:
                        await interaction.followup.send(f"❌ Error communicating with the backend: HTTP {response.status}")
                        return
                    data = await response.json()
                    
                    updated = data.get("updated", [])
                    unchanged = data.get("unchanged", [])
                    
                    msg = "⏳ **Compilation of all packs has started!**\n\n"
                    if updated:
                        msg += "✅ **Updated (New skins):**\n"
                        for p in updated:
                            encoded_p = urllib.parse.quote(p)
                            msg += f" - [{p}]({PUBLIC_URL}/packs/{encoded_p}.zip?v={int(time.time())})\n"
                    else:
                        msg += "✅ **Updated:** *(No packs had new skins)*\n"
                        
                    msg += "\n➖ **Unchanged:**\n"
                    if unchanged:
                        for p in unchanged:
                            encoded_p = urllib.parse.quote(p)
                            msg += f" - [{p}]({PUBLIC_URL}/packs/{encoded_p}.zip?v={int(time.time())})\n"
                    else:
                        msg += " *(None)*\n"
                        
                    msg += "\nThe updated archives will be available shortly."
                    await interaction.followup.send(msg)
                    
    except Exception as e:
        await interaction.followup.send(f"❌ Error communicating with the backend: {e}")

pack_group = app_commands.Group(name="pack", description="Manage championships and their allowed cars", default_permissions=discord.Permissions(administrator=True))
bot.tree.add_command(pack_group)

@pack_group.command(name="list", description="List all championships and their cars")
async def pack_list(interaction: discord.Interaction):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BACKEND_URL}/packs", params={"token": API_TOKEN}) as response:
                if response.status != 200:
                    await interaction.followup.send(f"❌ Error HTTP {response.status}")
                    return
                packs = await response.json()
                
        if not packs:
            await interaction.followup.send("No championships found.")
            return
            
        embed = discord.Embed(title="🏆 Championships Configuration", color=discord.Color.blue())
        for pack_name, cars in packs.items():
            cars_list = "\n".join([f"- `{car}`" for car in cars]) if cars else "*(No cars)*"
            embed.add_field(name=f"📦 {pack_name}", value=cars_list, inline=False)
            
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@pack_group.command(name="create", description="Create a new empty championship")
@app_commands.describe(pack_name="The name of the new championship (e.g. wec_2024)")
async def pack_create(interaction: discord.Interaction, pack_name: str):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BACKEND_URL}/packs/{pack_name}", params={"token": API_TOKEN}) as response:
                if response.status != 200:
                    err = await response.json()
                    await interaction.followup.send(f"❌ Error: {err.get('detail', 'Unknown error')}")
                    return
        await interaction.followup.send(f"✅ Championship `{pack_name}` created successfully!")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@pack_group.command(name="delete", description="Delete a championship")
@app_commands.describe(pack_name="The name of the championship to delete")
@app_commands.autocomplete(pack_name=pack_autocomplete)
async def pack_delete(interaction: discord.Interaction, pack_name: str):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(f"{BACKEND_URL}/packs/{pack_name}", params={"token": API_TOKEN}) as response:
                if response.status != 200:
                    err = await response.json()
                    await interaction.followup.send(f"❌ Error: {err.get('detail', 'Unknown error')}")
                    return
        await interaction.followup.send(f"✅ Championship `{pack_name}` deleted successfully!")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@pack_group.command(name="add_car", description="Add a car to a championship")
@app_commands.describe(pack_name="The championship name", car_name="The EXACT folder name of the car in Assetto Corsa")
@app_commands.autocomplete(pack_name=pack_autocomplete)
async def pack_add_car(interaction: discord.Interaction, pack_name: str, car_name: str):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BACKEND_URL}/packs/{pack_name}/cars/{car_name}", params={"token": API_TOKEN}) as response:
                if response.status != 200:
                    err = await response.json()
                    await interaction.followup.send(f"❌ Error: {err.get('detail', 'Unknown error')}")
                    return
        await interaction.followup.send(f"✅ Car `{car_name}` added to championship `{pack_name}`!")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@pack_group.command(name="remove_car", description="Remove a car from a championship")
@app_commands.describe(pack_name="The championship name", car_name="The car folder name to remove")
@app_commands.autocomplete(pack_name=pack_autocomplete)
async def pack_remove_car(interaction: discord.Interaction, pack_name: str, car_name: str):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.delete(f"{BACKEND_URL}/packs/{pack_name}/cars/{car_name}", params={"token": API_TOKEN}) as response:
                if response.status != 200:
                    err = await response.json()
                    await interaction.followup.send(f"❌ Error: {err.get('detail', 'Unknown error')}")
                    return
        await interaction.followup.send(f"✅ Car `{car_name}` removed from championship `{pack_name}`.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

config_group = app_commands.Group(name="config", description="Manage bot configuration and upload modes", default_permissions=discord.Permissions(administrator=True))
bot.tree.add_command(config_group)

@config_group.command(name="view", description="View current configuration")
async def config_view(interaction: discord.Interaction):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{BACKEND_URL}/settings", params={"token": API_TOKEN}) as response:
                if response.status != 200:
                    await interaction.followup.send(f"❌ Error HTTP {response.status}")
                    return
                settings = await response.json()
                
        embed = discord.Embed(title="⚙️ Current Configuration", color=discord.Color.green())
        upload_mode = settings.get("upload_mode", "direct")
        summary_mode = settings.get("summary_mode", "never_empty")
        
        descriptions = {
            "direct": "➡️ **Direct Upload to ACSM.** The skin will be instantly available on the game server, but the .zip archive for other players will not be built immediately.",
            "pack_only": "📦 **Pack Build Only.** Creates the complete .zip file for players to download, but the skin is not sent to the game server.",
            "both": "🚀 **Both (ACSM + Pack).** The skin is sent directly to the game server AND the .zip pack is built instantly. Perfect if everyone needs to be up to date.",
            "manual": "⏸️ **Manual (No auto actions).** The skin is just saved in the database. You'll need to use `/build_pack` to send everything later."
        }
        
        summary_desc = {
            "always": "🔄 **Always.** Send the summary every hour, even if no skins were updated.",
            "once_on_empty": "1️⃣ **Only once if empty.** Send exactly 1 empty summary, then stop until new skins arrive.",
            "never_empty": "🔇 **Never if empty.** Only send the summary if new skins were updated."
        }
        
        embed.add_field(name="Currently Active Upload Mode", value=f"**Mode : `{upload_mode}`**\n\n{descriptions.get(upload_mode, 'Unknown')}", inline=False)
        embed.add_field(name="Hourly Summary Rule", value=f"**Mode : `{summary_mode}`**\n\n{summary_desc.get(summary_mode, 'Unknown')}", inline=False)
            
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@config_group.command(name="summary", description="Set the hourly summary rule when no skins were updated")
@app_commands.describe(mode="Choose the summary mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="Always - Always send the summary", value="always"),
    app_commands.Choice(name="Only once if empty - Send 1 empty summary, then stop", value="once_on_empty"),
    app_commands.Choice(name="Never if empty - Only send when skins are updated", value="never_empty")
])
async def config_summary(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BACKEND_URL}/settings", json={"summary_mode": mode.value}, params={"token": API_TOKEN}) as response:
                if response.status != 200:
                    await interaction.followup.send(f"❌ Error HTTP {response.status}")
                    return
        await interaction.followup.send(f"✅ Hourly summary mode updated to: `{mode.name}`")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

@config_group.command(name="set_mode", description="Set the automatic rule when a new skin is uploaded")
@app_commands.describe(mode="Choose how new skins will be processed")
@app_commands.choices(mode=[
    app_commands.Choice(name="direct - Sends to game server, no .zip", value="direct"),
    app_commands.Choice(name="pack_only - Creates players .zip, no server upload", value="pack_only"),
    app_commands.Choice(name="both - Sends to server AND creates players .zip", value="both"),
    app_commands.Choice(name="manual - Just stores the skin, no auto action", value="manual")
])
async def config_set_mode(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    if not is_correct_channel(interaction):
        await interaction.response.send_message("You cannot use this command in this channel.", ephemeral=True)
        return
        
    await interaction.response.defer()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{BACKEND_URL}/settings", json={"upload_mode": mode.value}, params={"token": API_TOKEN}) as response:
                if response.status != 200:
                    err = await response.json()
                    await interaction.followup.send(f"❌ Error: {err.get('detail', 'Unknown error')}")
                    return
        await interaction.followup.send(f"✅ Upload mode successfully changed to `{mode.value}`!")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN is missing!")
    else:
        bot.run(DISCORD_TOKEN)
