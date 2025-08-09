import re
import asyncio
import datetime
import urllib.parse
import aiohttp
import discord
from discord.ext import commands

@bot.command(name="trackgame", help="Track a Roblox game's servers by name or URL")
@commands.has_permissions(administrator=True)
async def track_game(ctx, *, game_query=None):
    channel_id = ctx.channel.id  # Default to current channel
    
    # Handle file attachments
    if ctx.message.attachments:
        attachment = ctx.message.attachments[0]
        try:
            file_content = await attachment.read()
            file_text = file_content.decode('utf-8')
            
            # Look for Roblox game URL in the file
            game_id = None
            url_match = re.search(r'roblox\.com/games/(\d+)', file_text)
            if url_match:
                game_id = url_match.group(1)
                
            if game_id:
                await ctx.send(f"Found game ID: {game_id} in uploaded file. Processing...")
                await process_game_tracking(ctx, game_id, channel_id)
                return
            else:
                await ctx.send("No valid Roblox game URL found in the uploaded file.")
                return
        except Exception as e:
            await ctx.send(f"Error processing attachment: {str(e)}")
            return
    
    # No attachment, check if game_query is provided
    if not game_query:
        await ctx.send("Please provide a game URL, game name, or attach a file containing a URL.")
        return
    
    # Check if input is a URL
    url_match = re.search(r'roblox\.com/games/(\d+)', game_query)
    if url_match:
        game_id = url_match.group(1)
        await ctx.send(f"Processing game ID: {game_id} from URL...")
        await process_game_tracking(ctx, game_id, channel_id)
        return
    
    # If not a URL, search by name
    await ctx.send(f"Searching for games matching: {game_query}...")
    async with aiohttp.ClientSession() as session:
        try:
            # Search for the game by name
            search_url = f"https://games.roblox.com/v1/games/list?keyword={urllib.parse.quote(game_query)}&limit=5"
            async with session.get(search_url) as response:
                if response.status != 200:
                    await ctx.send(f"Error: Could not search for games. Status code: {response.status}")
                    return
                
                data = await response.json()
                games = data.get("games", [])
                
                if not games:
                    await ctx.send(f"No games found matching '{game_query}'.")
                    return
                
                # Create an embed with search results
                embed = discord.Embed(
                    title="Game Search Results",
                    description=f"Found {len(games)} games matching '{game_query}'",
                    color=discord.Color.blue()
                )
                
                for i, game in enumerate(games, 1):
                    game_id = game.get("placeId")
                    game_name = game.get("name")
                    player_count = game.get("playerCount", 0)
                    
                    embed.add_field(
                        name=f"{i}. {game_name}",
                        value=f"ID: {game_id}\nPlayers: {player_count:,}",
                        inline=False
                    )
                
                embed.set_footer(text="Reply with the number to track that game, or 'cancel' to abort.")
                
                await ctx.send(embed=embed)
                
                # Wait for user response
                def check(m):
                    return m.author == ctx.author and m.channel == ctx.channel
                
                try:
                    response_msg = await bot.wait_for('message', check=check, timeout=30.0)
                    
                    if response_msg.content.lower() == 'cancel':
                        await ctx.send("Game tracking cancelled.")
                        return
                    
                    try:
                        selection = int(response_msg.content)
                        if 1 <= selection <= len(games):
                            selected_game = games[selection-1]
                            game_id = str(selected_game.get("placeId"))
                            
                            # Process the selected game
                            await process_game_tracking(ctx, game_id, channel_id)
                        else:
                            await ctx.send("Invalid selection. Please try again with a valid number.")
                    except ValueError:
                        await ctx.send("Please respond with a number or 'cancel'.")
                
                except asyncio.TimeoutError:
                    await ctx.send("Selection timed out. Please try again.")
        
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")

# Create a separate command specifically for file uploads
@bot.command(name="trackgameurl", help="Track a Roblox game using a URL from an uploaded file")
async def track_game_url(ctx):
    if not ctx.message.attachments:
        await ctx.send("Please attach a file containing the Roblox game URL.")
        return
    
    attachment = ctx.message.attachments[0]
    try:
        file_content = await attachment.read()
        file_text = file_content.decode('utf-8')
        
        # Print the file content for debugging
        await ctx.send(f"File content: ```{file_text[:1000]}```")  # Show first 1000 chars
        
        # Look for Roblox game URL in the file
        url_match = re.search(r'roblox\.com/games/(\d+)', file_text)
        if url_match:
            game_id = url_match.group(1)
            await ctx.send(f"Found game ID: {game_id}. Processing...")
            await process_game_tracking(ctx, game_id, ctx.channel.id)
        else:
            await ctx.send("No valid Roblox game URL found in the uploaded file.")
    except Exception as e:
        await ctx.send(f"Error processing attachment: {str(e)}")

async def process_game_tracking(ctx, game_id, channel_id):
    """Process game tracking after a game ID has been determined"""
    await ctx.send(f"Starting to track game with ID: {game_id} in channel: {channel_id}")
    
    async with aiohttp.ClientSession() as session:
        try:
            # Get game details
            async with session.get(f"https://games.roblox.com/v1/games?universeIds={game_id}") as response:
                if response.status != 200:
                    await ctx.send(f"Error: Could not fetch game details. Status code: {response.status}")
                    return
                
                data = await response.json()
                if not data["data"]:
                    await ctx.send("Could not find the specified Roblox game.")
                    return
                
                game_name = data["data"][0]["name"]
                
                # Set up the tracking
                guild_id = str(ctx.guild.id)
                
                if guild_id not in roblox_games:
                    roblox_games[guild_id] = {}
                
                roblox_games[guild_id][game_id] = {
                    "name": game_name,
                    "channel": channel_id,
                    "last_check": None,
                    "message_id": None
                }
                
                save_roblox_games()
                
                embed = discord.Embed(
                    title="Game Tracking Added",
                    description=f"Now tracking servers for **{game_name}**",
                    color=discord.Color.green()
                )
                embed.add_field(name="Game ID", value=game_id)
                embed.add_field(name="Channel", value=f"<#{channel_id}>")
                
                await ctx.send(embed=embed)
                
                # Immediately check servers
                await check_game_servers(guild_id, game_id)
                
        except Exception as e:
            await ctx.send(f"An error occurred: {str(e)}")