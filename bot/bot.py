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

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
BACKEND_URL = os.getenv("BACKEND_URL", "http://backend:8000")
API_TOKEN = os.getenv("API_TOKEN", "default_insecure_token")
PUBLIC_URL = os.getenv("PUBLIC_URL", "http://localhost")
SKIN_CHANNEL_ID = int(os.getenv("SKIN_CHANNEL_ID", "0"))

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
        print(f"Message reçu ! Auteur: {message.author}, Bot?: {message.author.bot}, Channel: {message.channel.id}, Fichiers attachés: {len(message.attachments)}")
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

    async def handle_notify_build(self, request):
        try:
            data = await request.json()
            pack_name = data.get("pack_name", "default")
            
            if SKIN_CHANNEL_ID:
                channel = self.get_channel(SKIN_CHANNEL_ID)
                if channel:
                    await channel.send(f"🚀 **New skinpack available !**\nThe `{pack_name}` pack has just been updated.\nContent Manager link: {PUBLIC_URL}/packs/{pack_name}.zip?v={int(time.time())}")
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

@bot.tree.command(name="build_pack", description="Ask the server to compile the final .zip pack")
@app_commands.describe(pack_name="The pack to compile (optional, if omitted, builds all pending packs)")
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
            await interaction.followup.send(f"⏳ The compilation of the `{pack_name}` pack has started ! The archive will soon be available at {PUBLIC_URL}/packs/{pack_name}.zip?v={int(time.time())}")
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
                            msg += f" - `{p}`\n"
                    else:
                        msg += "✅ **Updated:** *(No packs had new skins)*\n"
                        
                    msg += "\n➖ **Unchanged:**\n"
                    if unchanged:
                        for p in unchanged:
                            msg += f" - `{p}`\n"
                    else:
                        msg += " *(None)*\n"
                        
                    msg += "\nThe updated archives will be available shortly."
                    await interaction.followup.send(msg)
                    
    except Exception as e:
        await interaction.followup.send(f"❌ Error communicating with the backend: {e}")

pack_group = app_commands.Group(name="pack", description="Manage championships and their allowed cars")
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

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN is missing!")
    else:
        bot.run(DISCORD_TOKEN)
