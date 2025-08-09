import discord
from discord.ext import commands
import asyncio
import json
import os
import sqlite3
import aiohttp
import youtube_dl
from datetime import datetime, timedelta
import re
import random
import string

intents = discord.Intents.all()

class BleedBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=self.get_prefix, intents=intents, help_command=None)
        self.db_path = "bleed_bot.db"
        self.init_database()
        self.action_limits = {
            'channel_delete': 3,
            'channel_create': 5,
            'role_delete': 3,
            'role_create': 5,
            'ban': 3,
            'kick': 5
        }
        self.action_timeframe = 60
        self.user_actions = {}
        self.cooldowns = {}
        
    def init_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS prefixes
                         (guild_id INTEGER PRIMARY KEY, prefix TEXT, user_id INTEGER, user_prefix TEXT)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS moderation
                         (guild_id INTEGER, staff_role_id INTEGER, mute_role_id INTEGER)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS welcome_messages
                         (guild_id INTEGER, channel_id INTEGER, message TEXT, self_destruct INTEGER)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS goodbye_messages
                         (guild_id INTEGER, channel_id INTEGER, message TEXT, self_destruct INTEGER)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS boost_messages
                         (guild_id INTEGER, channel_id INTEGER, message TEXT, self_destruct INTEGER)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS aliases
                         (guild_id INTEGER, shortcut TEXT, command TEXT)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS music_settings
                         (guild_id INTEGER, dj_role_id INTEGER, autoplay BOOLEAN)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS autoresponders
                         (guild_id INTEGER, trigger TEXT, response TEXT)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS levels
                         (guild_id INTEGER, user_id INTEGER, xp INTEGER, level INTEGER)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS starboard
                         (guild_id INTEGER, channel_id INTEGER, threshold INTEGER)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS counters
                         (guild_id INTEGER, counter_type TEXT, channel_id INTEGER, count INTEGER)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS sniped_messages
                         (guild_id INTEGER, channel_id INTEGER, author_id INTEGER, content TEXT, timestamp TEXT, message_type TEXT)''')
        
        cursor.execute('''CREATE TABLE IF NOT EXISTS voicemaster
                         (guild_id INTEGER, category_id INTEGER, channel_id INTEGER)''')
        
        conn.commit()
        conn.close()
    
    async def get_prefix(self, message):
        if not message.guild:
            return ";"
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT user_prefix FROM prefixes WHERE user_id = ?", (message.author.id,))
        user_prefix = cursor.fetchone()
        if user_prefix:
            conn.close()
            return user_prefix[0]
        
        cursor.execute("SELECT prefix FROM prefixes WHERE guild_id = ?", (message.guild.id,))
        guild_prefix = cursor.fetchone()
        conn.close()
        
        return guild_prefix[0] if guild_prefix else ";"
    
    def track_action(self, user_id, action_type):
        now = datetime.now()
        if user_id not in self.user_actions:
            self.user_actions[user_id] = {}
        
        if action_type not in self.user_actions[user_id]:
            self.user_actions[user_id][action_type] = []
        
        self.user_actions[user_id][action_type] = [
            timestamp for timestamp in self.user_actions[user_id][action_type]
            if now - timestamp < timedelta(seconds=self.action_timeframe)
        ]
        
        self.user_actions[user_id][action_type].append(now)
        
        return len(self.user_actions[user_id][action_type]) > self.action_limits.get(action_type, 10)
    
    def add_xp(self, guild_id, user_id, xp_amount):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT xp, level FROM levels WHERE guild_id = ? AND user_id = ?", 
                       (guild_id, user_id))
        result = cursor.fetchone()
        
        if result:
            current_xp, current_level = result
            new_xp = current_xp + xp_amount
        else:
            current_level = 1
            new_xp = xp_amount
        
        new_level = self.calculate_level(new_xp)
        level_up = new_level > current_level
        
        cursor.execute("INSERT OR REPLACE INTO levels (guild_id, user_id, xp, level) VALUES (?, ?, ?, ?)", 
                       (guild_id, user_id, new_xp, new_level))
        conn.commit()
        conn.close()
        
        return level_up, new_level
    
    def calculate_level(self, xp):
        return int((xp / 100) ** 0.5) + 1
    
    def xp_for_level(self, level):
        return ((level - 1) ** 2) * 100

bot = BleedBot()

class MusicPlayer:
    def __init__(self, bot):
        self.bot = bot
        self.queue = []
        self.current = None
        self.voice_client = None
        self.volume = 0.5
        self.repeat_mode = "off"
        
    async def play_next(self):
        if self.queue:
            self.current = self.queue.pop(0)
            source = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(self.current['url']))
            source.volume = self.volume
            self.voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(self.play_next(), self.bot.loop))

music_players = {}

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    await bot.change_presence(activity=discord.Game(name="bleed.bot"))

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    
    if message.guild:
        conn = sqlite3.connect(bot.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT shortcut, command FROM aliases WHERE guild_id = ?", (message.guild.id,))
        aliases = cursor.fetchall()
        
        for shortcut, command in aliases:
            if message.content.startswith(shortcut):
                message.content = message.content.replace(shortcut, command, 1)
                break
        
        cursor.execute("SELECT trigger, response FROM autoresponders WHERE guild_id = ?", (message.guild.id,))
        autoresponders = cursor.fetchall()
        
        for trigger, response in autoresponders:
            if trigger.lower() in message.content.lower():
                await message.channel.send(response)
                break
        
        user_id = message.author.id
        guild_id = message.guild.id
        
        if user_id not in bot.cooldowns:
            bot.cooldowns[user_id] = 0
        
        if asyncio.get_event_loop().time() - bot.cooldowns[user_id] >= 60:
            bot.cooldowns[user_id] = asyncio.get_event_loop().time()
            
            xp_gain = random.randint(15, 25)
            level_up, new_level = bot.add_xp(guild_id, user_id, xp_gain)
            
            if level_up:
                embed = discord.Embed(
                    title="🎉 Level Up!",
                    description=f"{message.author.mention} reached level **{new_level}**!",
                    color=0x00ff00
                )
                await message.channel.send(embed=embed, delete_after=10)
        
        conn.close()
    
    await bot.process_commands(message)

@bot.event
async def on_message_delete(message):
    if message.author.bot or not message.guild:
        return
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sniped_messages (guild_id, channel_id, author_id, content, timestamp, message_type) VALUES (?, ?, ?, ?, ?, ?)", 
                   (message.guild.id, message.channel.id, message.author.id, message.content, str(datetime.now()), "deleted"))
    conn.commit()
    conn.close()

@bot.event
async def on_message_edit(before, after):
    if before.author.bot or not before.guild:
        return
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO sniped_messages (guild_id, channel_id, author_id, content, timestamp, message_type) VALUES (?, ?, ?, ?, ?, ?)", 
                   (before.guild.id, before.channel.id, before.author.id, f"{before.content} -> {after.content}", str(datetime.now()), "edited"))
    conn.commit()
    conn.close()

@bot.event
async def on_member_join(member):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, message, self_destruct FROM welcome_messages WHERE guild_id = ?", 
                   (member.guild.id,))
    results = cursor.fetchall()
    conn.close()
    
    for channel_id, message, self_destruct in results:
        channel = member.guild.get_channel(channel_id)
        if channel:
            formatted_message = message.replace("{user}", member.mention).replace("{server}", member.guild.name)
            msg = await channel.send(formatted_message)
            
            if self_destruct:
                await asyncio.sleep(self_destruct)
                try:
                    await msg.delete()
                except:
                    pass

@bot.event
async def on_member_remove(member):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, message, self_destruct FROM goodbye_messages WHERE guild_id = ?", 
                   (member.guild.id,))
    results = cursor.fetchall()
    conn.close()
    
    for channel_id, message, self_destruct in results:
        channel = member.guild.get_channel(channel_id)
        if channel:
            formatted_message = message.replace("{user}", str(member)).replace("{server}", member.guild.name)
            msg = await channel.send(formatted_message)
            
            if self_destruct:
                await asyncio.sleep(self_destruct)
                try:
                    await msg.delete()
                except:
                    pass

@bot.event
async def on_guild_channel_delete(channel):
    async for entry in channel.guild.audit_logs(action=discord.AuditLogAction.channel_delete, limit=1):
        if bot.track_action(entry.user.id, 'channel_delete'):
            try:
                await channel.guild.ban(entry.user, reason="Anti-nuke: Excessive channel deletions")
                
                embed = discord.Embed(
                    title="🛡️ Anti-Nuke Triggered",
                    description=f"**{entry.user}** was banned for deleting too many channels",
                    color=0xff0000,
                    timestamp=datetime.now()
                )
                
                for text_channel in channel.guild.text_channels:
                    if text_channel.permissions_for(channel.guild.me).send_messages:
                        await text_channel.send(embed=embed)
                        break
                        
            except discord.Forbidden:
                pass

@bot.event
async def on_member_ban(guild, user):
    async for entry in guild.audit_logs(action=discord.AuditLogAction.ban, limit=1):
        if entry.user.bot:
            continue
            
        if bot.track_action(entry.user.id, 'ban'):
            try:
                await guild.ban(entry.user, reason="Anti-nuke: Excessive bans")
                
                embed = discord.Embed(
                    title="🛡️ Anti-Nuke Triggered",
                    description=f"**{entry.user}** was banned for excessive ban usage",
                    color=0xff0000,
                    timestamp=datetime.now()
                )
                
                for channel in guild.text_channels:
                    if channel.permissions_for(guild.me).send_messages:
                        await channel.send(embed=embed)
                        break
                        
            except discord.Forbidden:
                pass

@bot.event
async def on_raw_reaction_add(payload):
    if payload.user_id == bot.user.id:
        return
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id, threshold FROM starboard WHERE guild_id = ?", (payload.guild_id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return
    
    starboard_channel_id, threshold = result
    starboard_channel = bot.get_channel(starboard_channel_id)
    
    if not starboard_channel or payload.channel_id == starboard_channel_id:
        return
    
    channel = bot.get_channel(payload.channel_id)
    message = await channel.fetch_message(payload.message_id)
    
    star_reaction = None
    for reaction in message.reactions:
        if str(reaction.emoji) == "⭐":
            star_reaction = reaction
            break
    
    if star_reaction and star_reaction.count >= threshold:
        embed = discord.Embed(description=message.content, color=0xffd700, timestamp=message.created_at)
        embed.set_author(name=message.author.display_name, icon_url=message.author.display_avatar.url)
        embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})", inline=False)
        embed.set_footer(text=f"⭐ {star_reaction.count} | #{channel.name}")
        
        if message.attachments:
            embed.set_image(url=message.attachments[0].url)
        
        await starboard_channel.send(embed=embed)

@bot.group(name='prefix', invoke_without_command=True)
async def prefix_group(ctx):
    current_prefix = await bot.get_prefix(ctx.message)
    embed = discord.Embed(title="Current Prefix", description=f"The current prefix is: `{current_prefix}`", color=0x2f3136)
    await ctx.send(embed=embed)

@prefix_group.command(name='set')
@commands.has_permissions(administrator=True)
async def set_prefix(ctx, new_prefix):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    
    cursor.execute("INSERT OR REPLACE INTO prefixes (guild_id, prefix) VALUES (?, ?)", 
                   (ctx.guild.id, new_prefix))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Prefix Updated", description=f"Server prefix changed to: `{new_prefix}`", color=0x00ff00)
    await ctx.send(embed=embed)

@prefix_group.command(name='self')
async def self_prefix(ctx, new_prefix):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    
    cursor.execute("INSERT OR REPLACE INTO prefixes (user_id, user_prefix) VALUES (?, ?)", 
                   (ctx.author.id, new_prefix))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Personal Prefix Set", description=f"Your personal prefix is now: `{new_prefix}`", color=0x00ff00)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def setup(ctx):
    guild = ctx.guild
    
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(read_messages=False),
        guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
    }
    
    try:
        mod_logs = await guild.create_text_channel('mod-logs', overwrites=overwrites)
        reports = await guild.create_text_channel('reports', overwrites=overwrites)
        
        staff_role = await guild.create_role(name="Staff", color=0xff0000, permissions=discord.Permissions(kick_members=True, ban_members=True, manage_messages=True))
        
        conn = sqlite3.connect(bot.db_path)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO moderation (guild_id, staff_role_id) VALUES (?, ?)", 
                       (guild.id, staff_role.id))
        conn.commit()
        conn.close()
        
        embed = discord.Embed(title="Setup Complete", 
                             description=f"Created:\n• {mod_logs.mention}\n• {reports.mention}\n• {staff_role.mention}", 
                             color=0x00ff00)
        await ctx.send(embed=embed)
        
    except discord.Forbidden:
        await ctx.send("I don't have permission to create channels/roles!")

@bot.command()
@commands.has_permissions(administrator=True)
async def setupmute(ctx):
    guild = ctx.guild
    
    mute_role = await guild.create_role(name="Muted", color=0x808080)
    
    for channel in guild.channels:
        await channel.set_permissions(mute_role, send_messages=False, speak=False, add_reactions=False)
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("UPDATE moderation SET mute_role_id = ? WHERE guild_id = ?", 
                   (mute_role.id, guild.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Mute Role Created", description=f"Created {mute_role.mention} with proper permissions", color=0x00ff00)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def bind(ctx, action, role: discord.Role):
    if action == "staff":
        conn = sqlite3.connect(bot.db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE moderation SET staff_role_id = ? WHERE guild_id = ?", 
                       (role.id, ctx.guild.id))
        conn.commit()
        conn.close()
        
        embed = discord.Embed(title="Staff Role Bound", description=f"{role.mention} is now the staff role", color=0x00ff00)
        await ctx.send(embed=embed)

@bot.group(name='welcome', invoke_without_command=True)
async def welcome_group(ctx):
    await ctx.send("Use `welcome add`, `welcome remove`, `welcome view`, or `welcome list`")

@welcome_group.command(name='add')
@commands.has_permissions(manage_guild=True)
async def welcome_add(ctx, channel: discord.TextChannel, *, message_and_flags):
    self_destruct = None
    message = message_and_flags
    
    if '--self_destruct' in message_and_flags:
        parts = message_and_flags.split('--self_destruct')
        message = parts[0].strip()
        try:
            self_destruct = int(parts[1].strip())
        except:
            self_destruct = None
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO welcome_messages (guild_id, channel_id, message, self_destruct) VALUES (?, ?, ?, ?)", 
                   (ctx.guild.id, channel.id, message, self_destruct))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Welcome Message Added", 
                         description=f"Channel: {channel.mention}\nMessage: {message}", 
                         color=0x00ff00)
    await ctx.send(embed=embed)

@welcome_group.command(name='remove')
@commands.has_permissions(manage_guild=True)
async def welcome_remove(ctx, channel: discord.TextChannel):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM welcome_messages WHERE guild_id = ? AND channel_id = ?", 
                   (ctx.guild.id, channel.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Welcome Message Removed", description=f"Removed welcome message for {channel.mention}", color=0x00ff00)
    await ctx.send(embed=embed)

@welcome_group.command(name='view')
async def welcome_view(ctx, channel: discord.TextChannel):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT message, self_destruct FROM welcome_messages WHERE guild_id = ? AND channel_id = ?", 
                   (ctx.guild.id, channel.id))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        message, self_destruct = result
        embed = discord.Embed(title=f"Welcome Message for {channel.name}", 
                             description=f"Message: {message}\nSelf Destruct: {self_destruct}s" if self_destruct else f"Message: {message}", 
                             color=0x2f3136)
        await ctx.send(embed=embed)
    else:
        await ctx.send("No welcome message set for that channel.")

@welcome_group.command(name='list')
async def welcome_list(ctx):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT channel_id FROM welcome_messages WHERE guild_id = ?", (ctx.guild.id,))
    results = cursor.fetchall()
    conn.close()
    
    if results:
        channels = [f"<#{channel_id[0]}>" for channel_id in results]
        embed = discord.Embed(title="Welcome Channels", description="\n".join(channels), color=0x2f3136)
        await ctx.send(embed=embed)
    else:
        await ctx.send("No welcome messages configured.")

@bot.group(name='goodbye', invoke_without_command=True)
async def goodbye_group(ctx):
    await ctx.send("Use `goodbye add`, `goodbye remove`, `goodbye view`, or `goodbye list`")

@goodbye_group.command(name='add')
@commands.has_permissions(manage_guild=True)
async def goodbye_add(ctx, channel: discord.TextChannel, *, message_and_flags):
    self_destruct = None
    message = message_and_flags
    
    if '--self_destruct' in message_and_flags:
        parts = message_and_flags.split('--self_destruct')
        message = parts[0].strip()
        try:
            self_destruct = int(parts[1].strip())
        except:
            self_destruct = None
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO goodbye_messages (guild_id, channel_id, message, self_destruct) VALUES (?, ?, ?, ?)", 
                   (ctx.guild.id, channel.id, message, self_destruct))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Goodbye Message Added", 
                         description=f"Channel: {channel.mention}\nMessage: {message}", 
                         color=0x00ff00)
    await ctx.send(embed=embed)

@bot.command()
async def play(ctx, *, query):
    if not ctx.author.voice:
        return await ctx.send("You need to be in a voice channel!")
    
    if ctx.guild.id not in music_players:
        music_players[ctx.guild.id] = MusicPlayer(bot)
    
    player = music_players[ctx.guild.id]
    
    if not player.voice_client:
        player.voice_client = await ctx.author.voice.channel.connect()
    
    ytdl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
    }
    
    with youtube_dl.YoutubeDL(ytdl_opts) as ytdl:
        try:
            info = ytdl.extract_info(f"ytsearch:{query}", download=False)
            if 'entries' in info:
                info = info['entries'][0]
            
            song = {
                'title': info['title'],
                'url': info['url'],
                'duration': info.get('duration', 0),
                'requester': ctx.author
            }
            
            player.queue.append(song)
            
            embed = discord.Embed(title="Added to Queue", description=f"**{song['title']}**\nRequested by {ctx.author.mention}", color=0x00ff00)
            await ctx.send(embed=embed)
            
            if not player.voice_client.is_playing():
                await player.play_next()
                
        except Exception as e:
            await ctx.send(f"Error: {str(e)}")

@bot.command()
async def queue(ctx):
    if ctx.guild.id not in music_players:
        return await ctx.send("No music player active!")
    
    player = music_players[ctx.guild.id]
    
    if not player.queue:
        return await ctx.send("Queue is empty!")
    
    queue_list = []
    for i, song in enumerate(player.queue[:10], 1):
        queue_list.append(f"{i}. **{song['title']}** - {song['requester'].mention}")
    
    embed = discord.Embed(title="Music Queue", description="\n".join(queue_list), color=0x2f3136)
    if player.current:
        embed.add_field(name="Now Playing", value=f"**{player.current['title']}**", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
async def skip(ctx):
    if ctx.guild.id not in music_players:
        return await ctx.send("No music player active!")
    
    player = music_players[ctx.guild.id]
    
    if player.voice_client and player.voice_client.is_playing():
        player.voice_client.stop()
        await ctx.send("⏭️ Skipped!")

@bot.command()
async def pause(ctx):
    if ctx.guild.id not in music_players:
        return await ctx.send("No music player active!")
    
    player = music_players[ctx.guild.id]
    
    if player.voice_client and player.voice_client.is_playing():
        player.voice_client.pause()
        await ctx.send("⏸️ Paused!")

@bot.command()
async def resume(ctx):
    if ctx.guild.id not in music_players:
        return await ctx.send("No music player active!")
    
    player = music_players[ctx.guild.id]
    
    if player.voice_client and player.voice_client.is_paused():
        player.voice_client.resume()
        await ctx.send("▶️ Resumed!")

@bot.command()
async def volume(ctx, vol: int):
    if not 0 <= vol <= 100:
        return await ctx.send("Volume must be between 0-100!")
    
    if ctx.guild.id not in music_players:
        return await ctx.send("No music player active!")
    
    player = music_players[ctx.guild.id]
    player.volume = vol / 100
    
    if player.voice_client and player.voice_client.source:
        player.voice_client.source.volume = player.volume
    
    await ctx.send(f"🔊 Volume set to {vol}%")

@bot.group(name='alias', invoke_without_command=True)
async def alias_group(ctx):
    await ctx.send("Use `alias add`, `alias remove`, `alias view`, or `alias list`")

@alias_group.command(name='add')
@commands.has_permissions(manage_guild=True)
async def alias_add(ctx, shortcut, *, command):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO aliases (guild_id, shortcut, command) VALUES (?, ?, ?)", 
                   (ctx.guild.id, shortcut, command))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Alias Added", description=f"Shortcut: `{shortcut}`\nCommand: `{command}`", color=0x00ff00)
    await ctx.send(embed=embed)

@alias_group.command(name='remove')
@commands.has_permissions(manage_guild=True)
async def alias_remove(ctx, shortcut):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM aliases WHERE guild_id = ? AND shortcut = ?", 
                   (ctx.guild.id, shortcut))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Alias Removed", description=f"Removed alias: `{shortcut}`", color=0x00ff00)
    await ctx.send(embed=embed)

@alias_group.command(name='list')
async def alias_list(ctx):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT shortcut, command FROM aliases WHERE guild_id = ?", (ctx.guild.id,))
    results = cursor.fetchall()
    conn.close()
    
    if results:
        alias_list = [f"`{shortcut}` → `{command}`" for shortcut, command in results]
        embed = discord.Embed(title="Server Aliases", description="\n".join(alias_list), color=0x2f3136)
        await ctx.send(embed=embed)
    else:
        await ctx.send("No aliases configured.")

@bot.command()
@commands.has_permissions(kick_members=True)
async def kick(ctx, member: discord.Member, *, reason="No reason provided"):
    try:
        await member.kick(reason=reason)
        embed = discord.Embed(title="Member Kicked", description=f"**{member}** has been kicked\nReason: {reason}", color=0xff0000)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to kick this member!")

@bot.command()
@commands.has_permissions(ban_members=True)
async def ban(ctx, member: discord.Member, *, reason="No reason provided"):
    try:
        await member.ban(reason=reason)
        embed = discord.Embed(title="Member Banned", description=f"**{member}** has been banned\nReason: {reason}", color=0xff0000)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to ban this member!")

@bot.command()
@commands.has_permissions(moderate_members=True)
async def timeout(ctx, member: discord.Member, duration: int, *, reason="No reason provided"):
    try:
        await member.timeout(timedelta(minutes=duration), reason=reason)
        embed = discord.Embed(title="Member Timed Out", 
                             description=f"**{member}** has been timed out for {duration} minutes\nReason: {reason}", 
                             color=0xff0000)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to timeout this member!")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def mute(ctx, member: discord.Member, *, reason="No reason provided"):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT mute_role_id FROM moderation WHERE guild_id = ?", (ctx.guild.id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return await ctx.send("No mute role configured! Use `setupmute` first.")
    
    mute_role = ctx.guild.get_role(result[0])
    if not mute_role:
        return await ctx.send("Mute role not found!")
    
    try:
        await member.add_roles(mute_role, reason=reason)
        embed = discord.Embed(title="Member Muted", description=f"**{member}** has been muted\nReason: {reason}", color=0xff0000)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to mute this member!")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def unmute(ctx, member: discord.Member):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT mute_role_id FROM moderation WHERE guild_id = ?", (ctx.guild.id,))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return await ctx.send("No mute role configured!")
    
    mute_role = ctx.guild.get_role(result[0])
    if not mute_role:
        return await ctx.send("Mute role not found!")
    
    try:
        await member.remove_roles(mute_role)
        embed = discord.Embed(title="Member Unmuted", description=f"**{member}** has been unmuted", color=0x00ff00)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("I don't have permission to unmute this member!")

@bot.command()
async def rank(ctx, member: discord.Member = None):
    if member is None:
        member = ctx.author
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT xp, level FROM levels WHERE guild_id = ? AND user_id = ?", 
                   (ctx.guild.id, member.id))
    result = cursor.fetchone()
    
    if not result:
        embed = discord.Embed(title="No Data", description=f"{member.mention} hasn't gained any XP yet!", color=0xff0000)
        return await ctx.send(embed=embed)
    
    xp, level = result
    
    cursor.execute("SELECT COUNT(*) FROM levels WHERE guild_id = ? AND xp > ?", 
                   (ctx.guild.id, xp))
    rank = cursor.fetchone()[0] + 1
    
    conn.close()
    
    xp_for_current = bot.xp_for_level(level)
    xp_for_next = bot.xp_for_level(level + 1)
    xp_progress = xp - xp_for_current
    xp_needed = xp_for_next - xp_for_current
    
    embed = discord.Embed(title=f"{member.display_name}'s Rank", color=member.color)
    embed.add_field(name="Level", value=level, inline=True)
    embed.add_field(name="Rank", value=f"#{rank}", inline=True)
    embed.add_field(name="XP", value=f"{xp_progress}/{xp_needed}", inline=True)
    embed.set_thumbnail(url=member.display_avatar.url)
    
    await ctx.send(embed=embed)

@bot.command()
async def leaderboard(ctx):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    
    cursor.execute("SELECT user_id, xp, level FROM levels WHERE guild_id = ? ORDER BY xp DESC LIMIT 10", 
                   (ctx.guild.id,))
    results = cursor.fetchall()
    conn.close()
    
    if not results:
        return await ctx.send("No leaderboard data available!")
    
    embed = discord.Embed(title=f"{ctx.guild.name} Leaderboard", color=0x2f3136)
    
    leaderboard_text = ""
    for i, (user_id, xp, level) in enumerate(results, 1):
        user = bot.get_user(user_id)
        if user:
            leaderboard_text += f"{i}. **{user.display_name}** - Level {level} ({xp} XP)\n"
    
    embed.description = leaderboard_text
    await ctx.send(embed=embed)

@bot.command()
async def snipe(ctx, channel: discord.TextChannel = None):
    if channel is None:
        channel = ctx.channel
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT author_id, content, timestamp, message_type FROM sniped_messages WHERE guild_id = ? AND channel_id = ? ORDER BY timestamp DESC LIMIT 1", 
                   (ctx.guild.id, channel.id))
    result = cursor.fetchone()
    conn.close()
    
    if not result:
        return await ctx.send("No sniped messages found!")
    
    author_id, content, timestamp, message_type = result
    author = bot.get_user(author_id)
    
    embed = discord.Embed(
        title=f"Sniped Message ({message_type})",
        description=content,
        color=0xff0000,
        timestamp=datetime.fromisoformat(timestamp)
    )
    
    if author:
        embed.set_author(name=author.display_name, icon_url=author.display_avatar.url)
    
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_channels=True)
async def starboard(ctx, channel: discord.TextChannel, threshold: int = 3):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO starboard (guild_id, channel_id, threshold) VALUES (?, ?, ?)", 
                   (ctx.guild.id, channel.id, threshold))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Starboard Setup", 
                         description=f"Starboard channel: {channel.mention}\nThreshold: {threshold} ⭐", 
                         color=0xffd700)
    await ctx.send(embed=embed)

@bot.group(name='autorespond', invoke_without_command=True)
async def autorespond_group(ctx):
    await ctx.send("Use `autorespond add`, `autorespond remove`, or `autorespond list`")

@autorespond_group.command(name='add')
@commands.has_permissions(manage_guild=True)
async def autorespond_add(ctx, trigger, *, response):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO autoresponders (guild_id, trigger, response) VALUES (?, ?, ?)", 
                   (ctx.guild.id, trigger, response))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Auto-Responder Added", 
                         description=f"Trigger: `{trigger}`\nResponse: `{response}`", 
                         color=0x00ff00)
    await ctx.send(embed=embed)

@autorespond_group.command(name='remove')
@commands.has_permissions(manage_guild=True)
async def autorespond_remove(ctx, trigger):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM autoresponders WHERE guild_id = ? AND trigger = ?", 
                   (ctx.guild.id, trigger))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="Auto-Responder Removed", 
                         description=f"Removed trigger: `{trigger}`", 
                         color=0x00ff00)
    await ctx.send(embed=embed)

@autorespond_group.command(name='list')
async def autorespond_list(ctx):
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT trigger, response FROM autoresponders WHERE guild_id = ?", (ctx.guild.id,))
    results = cursor.fetchall()
    conn.close()
    
    if results:
        responder_list = [f"`{trigger}` → `{response}`" for trigger, response in results]
        embed = discord.Embed(title="Auto-Responders", description="\n".join(responder_list), color=0x2f3136)
        await ctx.send(embed=embed)
    else:
        await ctx.send("No auto-responders configured.")

@bot.command()
@commands.has_permissions(manage_channels=True)
async def voicemaster(ctx, category: discord.CategoryChannel):
    overwrites = {
        ctx.guild.default_role: discord.PermissionOverwrite(connect=True),
        ctx.guild.me: discord.PermissionOverwrite(connect=True, manage_channels=True)
    }
    
    join_channel = await category.create_voice_channel("➕ Join to Create", overwrites=overwrites)
    
    conn = sqlite3.connect(bot.db_path)
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO voicemaster (guild_id, category_id, channel_id) VALUES (?, ?, ?)", 
                   (ctx.guild.id, category.id, join_channel.id))
    conn.commit()
    conn.close()
    
    embed = discord.Embed(title="VoiceMaster Setup", 
                         description=f"Join channel: {join_channel.mention}\nCategory: {category.mention}", 
                         color=0x00ff00)
    await ctx.send(embed=embed)

@bot.event
async def on_voice_state_update(member, before, after):
    if after.channel:
        conn = sqlite3.connect(bot.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT category_id, channel_id FROM voicemaster WHERE guild_id = ?", (member.guild.id,))
        result = cursor.fetchone()
        conn.close()
        
        if result and after.channel.id == result[1]:
            category = member.guild.get_channel(result[0])
            if category:
                new_channel = await category.create_voice_channel(f"{member.display_name}'s Channel", 
                                                                 user_limit=10)
                await member.move_to(new_channel)
    
    if before.channel and before.channel.name.endswith("'s Channel"):
        if len(before.channel.members) == 0:
            await before.channel.delete()

@bot.command()
async def help(ctx, *, command=None):
    if command is None:
        embed = discord.Embed(title="Bleed Bot Commands", color=0x2f3136)
        embed.add_field(name="General", value="`prefix`, `help`", inline=False)
        embed.add_field(name="Moderation", value="`setup`, `setupmute`, `bind`, `kick`, `ban`, `mute`, `unmute`, `timeout`", inline=False)
        embed.add_field(name="System Messages", value="`welcome`, `goodbye`, `boost`", inline=False)
        embed.add_field(name="Music", value="`play`, `queue`, `skip`, `pause`, `resume`, `volume`", inline=False)
        embed.add_field(name="Aliases", value="`alias add/remove/view/list`", inline=False)
        embed.add_field(name="Leveling", value="`rank`, `leaderboard`", inline=False)
        embed.add_field(name="Other", value="`snipe`, `starboard`, `autorespond`, `voicemaster`", inline=False)
        embed.description = "Use `help <command>` for detailed information about a specific command."
        await ctx.send(embed=embed)

if __name__ == "__main__":
    bot.run('Bot token goes here :D')