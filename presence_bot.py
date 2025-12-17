import discord
from discord.ext import commands, tasks
import datetime
import asyncio
import os
import json
from dotenv import load_dotenv
from aiohttp import web

load_dotenv()

# --- CONSTANTS ---
DATA_FOLDER = "data"
PLAY_TIMES_FILE = os.path.join(DATA_FOLDER, "play_times.json")
LEADERBOARD_FILE = os.path.join(DATA_FOLDER, "leaderboard.json")
GAME_ROLES_FILE = os.path.join(DATA_FOLDER, "game_roles.json")
GAME_LEADERBOARD_FILE = os.path.join(DATA_FOLDER, "game_leaderboard.json")

# --- MODIFIED: Separated channel names for different purposes ---
WEEKLY_ANNOUNCEMENT_CHANNEL_NAME = "general"
PRESENCE_CHANNEL_NAME = "presence-update"


# --- DATA HELPER FUNCTIONS ---
def setup_data_files():
    """Ensures the data directory and necessary JSON files exist."""
    os.makedirs(DATA_FOLDER, exist_ok=True)
    for file_path in [PLAY_TIMES_FILE, LEADERBOARD_FILE, GAME_ROLES_FILE, GAME_LEADERBOARD_FILE]:
        if not os.path.exists(file_path):
            with open(file_path, 'w') as f:
                json.dump({}, f)


def load_data(file_path):
    """Loads data from a JSON file."""
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_data(file_path, data):
    """Saves data to a JSON file, handling datetime and set objects."""
    serializable_data = {}
    if file_path == PLAY_TIMES_FILE:
        for user_id, user_data in data.items():
            serializable_data[user_id] = {
                "start_time": user_data["start_time"].isoformat(),
                "last_updated": user_data["last_updated"].isoformat(),
                "game": user_data["game"],
                "milestones_hit": list(user_data["milestones_hit"]),
                "guild_id": user_data["guild_id"],
                "channel_id": user_data["channel_id"]
            }
    else:
        serializable_data = data

    with open(file_path, 'w') as f:
        json.dump(serializable_data, f, indent=4)


# --- BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- DATA LOADING ---
setup_data_files()
playing_start_times_raw = load_data(PLAY_TIMES_FILE)
playing_start_times = {}
for user_id_str, data in playing_start_times_raw.items():
    user_id = int(user_id_str)
    start_time = datetime.datetime.fromisoformat(data["start_time"])
    playing_start_times[user_id] = {
        "start_time": start_time,
        "last_updated": datetime.datetime.fromisoformat(data.get("last_updated", start_time.isoformat())),
        "game": data["game"],
        "milestones_hit": set(data.get("milestones_hit", [])),
        "guild_id": data["guild_id"],
        "channel_id": data["channel_id"]
    }

leaderboard_data = load_data(LEADERBOARD_FILE)
game_roles = load_data(GAME_ROLES_FILE)
game_leaderboard_data = load_data(GAME_LEADERBOARD_FILE)

milestone_messages = {
    60: "⏱️ Wow, such dedication! You've been gaming for 1 hour!",
    120: "🎮 What a gamer! You've reached 2 hours!",
    180: "🔥 Batak ampota! 3 hours of solid play!",
    240: "😳 Are you okay? That’s 4 hours!",
    300: "👀 This is turning into a marathon. 5 hours and counting!",
}


# --- CORE HELPER FUNCTIONS ---
# MODIFIED: Renamed and generalized the function to find any channel by name
def get_text_channel_by_name(guild, channel_name):
    """Finds a text channel in a guild by its name."""
    return discord.utils.get(guild.text_channels, name=channel_name)


def format_duration(seconds):
    """Formats seconds into a human-readable string (Hh Mm Ss)."""
    if seconds < 0: seconds = 0
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{int(hours)}h {int(minutes)}m"
    return f"{int(minutes)}m {int(seconds)}s"


async def update_leaderboard(member, duration_seconds):
    """Updates the user leaderboard with playtime."""
    if duration_seconds <= 0: return
    guild_id_str = str(member.guild.id)
    user_id_str = str(member.id)
    if guild_id_str not in leaderboard_data:
        leaderboard_data[guild_id_str] = {}
    current_time = leaderboard_data[guild_id_str].get(user_id_str, 0)
    leaderboard_data[guild_id_str][user_id_str] = current_time + duration_seconds
    save_data(LEADERBOARD_FILE, leaderboard_data)


async def update_game_leaderboard(guild, game_name, duration_seconds):
    """Updates the game leaderboard with playtime."""
    if duration_seconds <= 0: return
    guild_id_str = str(guild.id)
    if guild_id_str not in game_leaderboard_data:
        game_leaderboard_data[guild_id_str] = {}
    current_time = game_leaderboard_data[guild_id_str].get(game_name, 0)
    game_leaderboard_data[guild_id_str][game_name] = current_time + duration_seconds
    save_data(GAME_LEADERBOARD_FILE, game_leaderboard_data)


async def handle_game_role(member, game_name, action="add"):
    """Adds or removes a game-specific role from a member."""
    guild_id_str = str(member.guild.id)
    if guild_id_str not in game_roles or not game_name: return
    game_name_lower = game_name.lower()
    if game_name_lower in game_roles[guild_id_str]:
        role_id = game_roles[guild_id_str][game_name_lower]
        role = member.guild.get_role(role_id)
        if role:
            try:
                if action == "add":
                    await member.add_roles(role, reason=f"Started playing {game_name}")
                elif action == "remove":
                    await member.remove_roles(role, reason=f"Stopped playing {game_name}")
            except discord.Forbidden:
                print(f"Error: Bot lacks permissions to manage role '{role.name}' for {member.name}.")
            except discord.HTTPException as e:
                print(f"Error: An HTTP error occurred while managing roles: {e}")


# --- ACTIVITY TRACKING LOGIC ---
async def start_tracking_activity(member, game):
    """Handles all logic for when a member starts a game."""
    if member.id in playing_start_times: return
    # MODIFIED: Get the specific presence channel
    channel = get_text_channel_by_name(member.guild, PRESENCE_CHANNEL_NAME)
    now = datetime.datetime.now(datetime.UTC)

    playing_start_times[member.id] = {
        "start_time": now,
        "last_updated": now,
        "game": game.name,
        "milestones_hit": set(),
        "guild_id": member.guild.id,
        "channel_id": channel.id if channel else None # Store the presence channel ID
    }
    save_data(PLAY_TIMES_FILE, playing_start_times)
    await handle_game_role(member, game.name, action="add")
    print(f"INFO: Started tracking {member.name} playing {game.name}")
    if channel:
        await channel.send(f"🎮 {member.name} started playing **{game.name}**!")


async def stop_tracking_activity(member):
    """Handles all logic for when a member stops a game, returning the session info."""
    if member.id not in playing_start_times:
        return None, None

    start_info = playing_start_times.pop(member.id)
    now = datetime.datetime.now(datetime.UTC)

    duration_since_last_update = (now - start_info["last_updated"]).total_seconds()
    await update_leaderboard(member, duration_since_last_update)
    await update_game_leaderboard(member.guild, start_info["game"], duration_since_last_update)

    await handle_game_role(member, start_info["game"], action="remove")
    save_data(PLAY_TIMES_FILE, playing_start_times)

    total_duration = (now - start_info["start_time"]).total_seconds()
    print(f"INFO: Stopped tracking {member.name}. Total session time: {format_duration(total_duration)}")
    return start_info, total_duration


# --- BOT EVENTS ---
@bot.event
async def on_ready():
    """Called when the bot is ready and connected."""
    print(f"✅ Bot is online as {bot.user}")
    print("-" * 20)

    print("⏳ Waiting 10 seconds for presence cache to populate...")
    await asyncio.sleep(10)
    print("✅ Cache ready.")

    print("🚀 Performing initial presence scan for ongoing activities...")
    active_users_found = 0
    for guild in bot.guilds:
        async for member in guild.fetch_members(limit=None):
            if member.bot:
                continue

            game_activity = next((a for a in member.activities if a.type == discord.ActivityType.playing), None)
            if game_activity and member.id not in playing_start_times:
                print(f"  -> Found {member.name} playing {game_activity.name}. Starting tracking.")
                await start_tracking_activity(member, game_activity)
                active_users_found += 1

    print(f"✅ Initial scan complete. Found and started tracking {active_users_found} active users.")
    print("-" * 20)

    # Start the keep-alive server
    bot.loop.create_task(keep_alive())

    check_milestones.start()
    update_leaderboards_periodically.start()
    weekly_reset_and_announce.start()


@bot.event
async def on_presence_update(before, after):
    if after.bot: return

    # MODIFIED: Get the specific presence channel for all real-time updates
    channel = get_text_channel_by_name(after.guild, PRESENCE_CHANNEL_NAME)

    if before.status != after.status and channel:
        if after.status == discord.Status.online and before.status == discord.Status.offline:
            await channel.send(f"🟢 {after.mention} just came online.")
        elif after.status == discord.Status.offline:
            await channel.send(f"⚫ {after.mention} just went offline.")

    before_game = next((a for a in before.activities if a.type == discord.ActivityType.playing), None)
    after_game = next((a for a in after.activities if a.type == discord.ActivityType.playing), None)

    if not before_game and after_game:
        await start_tracking_activity(after, after_game)
    elif (before_game and not after_game) or (before_game and after.status == discord.Status.offline):
        start_info, duration = await stop_tracking_activity(after)
        if channel and start_info:
            await channel.send(
                f"⏹️ {after.name} stopped playing **{start_info['game']}** after {format_duration(duration)}.")
    elif before_game and after_game and before_game.name != after_game.name:
        await stop_tracking_activity(after)
        await start_tracking_activity(after, after_game)
        if channel:
            await channel.send(f"🔄 {after.name} switched from **{before_game.name}** to **{after_game.name}**!")


# --- BACKGROUND TASKS ---
@tasks.loop(minutes=5)
async def update_leaderboards_periodically():
    """Periodically saves playtime for active users to prevent data loss."""
    now = datetime.datetime.now(datetime.UTC)
    if not playing_start_times: return

    print(f"LOG: [{datetime.datetime.now()}] Running periodic leaderboard update...")
    for user_id, info in list(playing_start_times.items()):
        guild = bot.get_guild(info["guild_id"])
        if not guild: continue
        member = guild.get_member(user_id)
        if not member: continue

        duration_to_log = (now - info["last_updated"]).total_seconds()
        await update_leaderboard(member, duration_to_log)
        await update_game_leaderboard(guild, info["game"], duration_to_log)

        playing_start_times[user_id]["last_updated"] = now

    save_data(PLAY_TIMES_FILE, playing_start_times)
    print("LOG: Periodic leaderboard update complete.")


@tasks.loop(minutes=1)
async def check_milestones():
    """Checks for and announces playtime milestones."""
    now = datetime.datetime.now(datetime.UTC)
    for user_id, info in list(playing_start_times.items()):
        total_minutes_played = int((now - info["start_time"]).total_seconds() // 60)

        for milestone_minutes, message in milestone_messages.items():
            if total_minutes_played >= milestone_minutes and milestone_minutes not in info["milestones_hit"]:
                guild = bot.get_guild(info["guild_id"])
                if guild:
                    member = guild.get_member(user_id)
                    # The channel_id stored is the presence channel, which is correct for milestones
                    channel = guild.get_channel(info["channel_id"])
                    if member and channel:
                        try:
                            await channel.send(f"**{member.mention}** {message}")
                            playing_start_times[user_id]["milestones_hit"].add(milestone_minutes)
                        except discord.HTTPException as e:
                            print(f"Error: Could not send milestone message: {e}")

    if any(info["milestones_hit"] for info in playing_start_times.values()):
        save_data(PLAY_TIMES_FILE, playing_start_times)


@tasks.loop(hours=24)
async def weekly_reset_and_announce():
    """
    Checks daily if it's the reset day (Monday). If so, announces winners
    and resets leaderboards for the new week.
    """
    if datetime.datetime.now(datetime.UTC).weekday() != 0:
        return

    print("--- RUNNING WEEKLY LEADERBOARD RESET ---")

    for guild in bot.guilds:
        print(f"Processing reset for guild: {guild.name} ({guild.id})")
        guild_id_str = str(guild.id)
        # MODIFIED: Get the specific weekly announcement channel
        announcement_channel = get_text_channel_by_name(guild, WEEKLY_ANNOUNCEMENT_CHANNEL_NAME)

        if not announcement_channel:
            print(f"  -> Skipping guild {guild.name}, no '{WEEKLY_ANNOUNCEMENT_CHANNEL_NAME}' channel found.")
            continue

        user_lb = leaderboard_data.get(guild_id_str, {})
        sorted_users = sorted(user_lb.items(), key=lambda i: i[1], reverse=True)

        game_lb = game_leaderboard_data.get(guild_id_str, {})
        sorted_games = sorted(game_lb.items(), key=lambda i: i[1], reverse=True)

        embed = discord.Embed(
            title="🏆 The Weekly Grind is Over! 🏆",
            description="The dust has settled on another epic week of gaming! A huge congratulations to this week's champions. **The leaderboards have now been wiped clean for a fresh start!**",
            color=discord.Color.gold(),
            timestamp=datetime.datetime.now(datetime.UTC)
        )
        # NEW: Fixed thumbnail with a direct image link
        embed.set_thumbnail(url="https://i.imgur.com/rXf2z2i.png")

        if sorted_users:
            top_user_id_str, top_user_seconds = sorted_users[0]
            top_user = guild.get_member(int(top_user_id_str))
            top_user_mention = top_user.mention if top_user else f"User ({top_user_id_str})"
            embed.add_field(
                name="👑 Weekly Gaming Champion",
                value=f"{top_user_mention}\n**Time Played:** `{format_duration(top_user_seconds)}`",
                inline=True
            )
        else:
            embed.add_field(name="👑 Weekly Gaming Champion", value="*No one played this week!*", inline=True)

        if sorted_games:
            top_game_name, top_game_seconds = sorted_games[0]
            embed.add_field(
                name="🎮 Most Dominant Game",
                value=f"**{top_game_name}**\n**Total Playtime:** `{format_duration(top_game_seconds)}`",
                inline=True
            )
        else:
            embed.add_field(name="🎮 Most Dominant Game", value="*No games were tracked!*", inline=True)

        if len(sorted_users) > 1:
            embed.add_field(name='\u200b', value='\u200b', inline=False)
            honorable_mentions = []
            for i, (user_id_str, total_seconds) in enumerate(sorted_users[1:3], start=2):
                member = guild.get_member(int(user_id_str))
                name = member.display_name if member else f"User ({user_id_str})"
                emoji = "🥈" if i == 2 else "🥉"
                honorable_mentions.append(f"{emoji} **{name}**: `{format_duration(total_seconds)}`")
            embed.add_field(name="🏅 Hall of Fame", value="\n".join(honorable_mentions), inline=False)

        embed.set_footer(text="A new week begins now. Good luck, everyone!")

        try:
            await announcement_channel.send(embed=embed)
            print(f"  -> Announcement sent for {guild.name}.")
        except discord.Forbidden:
            print(f"  -> FAILED to send announcement for {guild.name} (Missing Permissions).")
        except discord.HTTPException as e:
            print(f"  -> FAILED to send announcement for {guild.name}: {e}")

        if guild_id_str in leaderboard_data:
            leaderboard_data[guild_id_str] = {}
            print(f"  -> User leaderboard reset for {guild.name}.")
        if guild_id_str in game_leaderboard_data:
            game_leaderboard_data[guild_id_str] = {}
            print(f"  -> Game leaderboard reset for {guild.name}.")

    save_data(LEADERBOARD_FILE, leaderboard_data)
    save_data(GAME_LEADERBOARD_FILE, game_leaderboard_data)
    print("--- WEEKLY RESET COMPLETE. DATA SAVED. ---")


@check_milestones.before_loop
@update_leaderboards_periodically.before_loop
@weekly_reset_and_announce.before_loop
async def before_tasks():
    """Ensures the bot is ready before starting any background tasks."""
    await bot.wait_until_ready()


# --- COMMANDS ---
@bot.command(name="leaderboard", aliases=["lb"], help="Shows the server's gaming leaderboard for users.")
async def leaderboard(ctx):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in leaderboard_data or not leaderboard_data[guild_id_str]:
        await ctx.send("No user leaderboard data has been recorded for this server yet!")
        return

    sorted_users = sorted(leaderboard_data[guild_id_str].items(), key=lambda item: item[1], reverse=True)
    embed = discord.Embed(title=f"🏆 Top Gamers in {ctx.guild.name}", color=discord.Color.gold())
    description = ""
    for i, (user_id_str, total_seconds) in enumerate(sorted_users[:10], 1):
        member = ctx.guild.get_member(int(user_id_str))
        name = member.display_name if member else f"User ({user_id_str})"
        emoji = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else "🔹"
        description += f"{emoji} **{name}**: {format_duration(total_seconds)}\n"
    embed.description = description
    await ctx.send(embed=embed)


@bot.command(name="topgames", aliases=["tg"], help="Shows the most played games on the server.")
async def topgames(ctx):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in game_leaderboard_data or not game_leaderboard_data[guild_id_str]:
        await ctx.send("No game leaderboard data has been recorded for this server yet!")
        return

    sorted_games = sorted(game_leaderboard_data[guild_id_str].items(), key=lambda item: item[1], reverse=True)
    embed = discord.Embed(title=f"🎮 Most Played Games in {ctx.guild.name}", color=discord.Color.orange())
    description = ""
    for i, (game_name, total_seconds) in enumerate(sorted_games[:10], 1):
        emoji = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else "🔹"
        description += f"{emoji} **{game_name}**: {format_duration(total_seconds)}\n"
    embed.description = description
    await ctx.send(embed=embed)


# NEW: Overhauled command to check both required channels.
@bot.command(name="checkchannels", help="Checks if the bot can find and use the required channels.")
async def check_channels(ctx):
    """Checks the configuration of both required announcement channels."""
    embed = discord.Embed(
        title="⚙️ Bot Channel Configuration Check",
        description="This checks if I can find and send messages in the required channels.",
        color=discord.Color.blurple()
    )

    # Helper to check a single channel
    async def check_single_channel(channel_name):
        channel = get_text_channel_by_name(ctx.guild, channel_name)
        if not channel:
            return f"❌ **Not Found:** A channel named `{channel_name}` does not exist."

        permissions = channel.permissions_for(ctx.guild.me)
        if not permissions.send_messages or not permissions.embed_links:
             return (f"✅ **Found:** {channel.mention}\n"
                     f"❌ **Permissions Error:** I need `Send Messages` and `Embed Links` permissions in this channel.")

        return f"✅ **OK:** Found {channel.mention} and have the correct permissions."

    weekly_status = await check_single_channel(WEEKLY_ANNOUNCEMENT_CHANNEL_NAME)
    presence_status = await check_single_channel(PRESENCE_CHANNEL_NAME)

    embed.add_field(name=f"Weekly Announcements (`#{WEEKLY_ANNOUNCEMENT_CHANNEL_NAME}`)", value=weekly_status, inline=False)
    embed.add_field(name=f"Real-time Activity (`#{PRESENCE_CHANNEL_NAME}`)", value=presence_status, inline=False)
    embed.set_footer(text="If there are any errors, please create the channels or fix my permissions.")

    await ctx.send(embed=embed)


@bot.command(name="whoplays", help="Shows who is currently playing a specific game.")
async def whoplays(ctx, *, game_name: str):
    playing_now = []
    now = datetime.datetime.now(datetime.UTC)
    guild_id = ctx.guild.id
    for user_id, info in playing_start_times.items():
        if info["game"].lower() == game_name.lower() and info["guild_id"] == guild_id:
            member = ctx.guild.get_member(user_id)
            if member:
                duration = format_duration((now - info["start_time"]).total_seconds())
                playing_now.append(f"• **{member.display_name}** (for {duration})")

    if not playing_now:
        await ctx.send(f"No one is currently playing **{game_name}** in this server.")
        return
    embed = discord.Embed(title=f"Players currently in {game_name}", description="\n".join(playing_now),
                          color=discord.Color.blue())
    await ctx.send(embed=embed)


@bot.command(name="addgamerole", help="Links a game to a role. Usage: !addgamerole \"Game Name\" @Role")
@commands.has_permissions(manage_roles=True)
async def add_game_role(ctx, game_name: str, role: discord.Role):
    guild_id_str = str(ctx.guild.id)
    game_name_lower = game_name.lower()
    if guild_id_str not in game_roles:
        game_roles[guild_id_str] = {}
    game_roles[guild_id_str][game_name_lower] = role.id
    save_data(GAME_ROLES_FILE, game_roles)
    await ctx.send(f"✅ Successfully linked the game **{game_name}** to the `{role.name}` role.")


@add_game_role.error
async def add_game_role_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You need the 'Manage Roles' permission to use this command.")


@bot.command(name="reset", help="Resets all leaderboard stats. Requires 'boss' role.")
async def reset_stats(ctx):
    """Resets all leaderboard data for the server. Requires 'boss' role."""
    # Check for 'boss' role (case-insensitive)
    has_boss_role = any(role.name.lower() == "boss" for role in ctx.author.roles)

    if not has_boss_role:
        await ctx.send("❌ You do not have the required role ('boss') to use this command.")
        return

    guild_id_str = str(ctx.guild.id)
    
    # Reset User Leaderboard
    if guild_id_str in leaderboard_data:
        leaderboard_data[guild_id_str] = {}
        save_data(LEADERBOARD_FILE, leaderboard_data)

    # Reset Game Leaderboard
    if guild_id_str in game_leaderboard_data:
        game_leaderboard_data[guild_id_str] = {}
        save_data(GAME_LEADERBOARD_FILE, game_leaderboard_data)

    await ctx.send("⚠️ **SERVER WIPE** ⚠️\nAll leaderboard statistics for this server have been reset by the boss.")


# --- KEEP_ALIVE SERVER ---
async def handle(request):
    return web.Response(text="Bot is running!")

async def keep_alive():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    print(f"🌍 Keep-alive web server started on port {port}")


# --- MAIN EXECUTION ---
async def main():
    """Main function to start the bot."""
    TOKEN = os.getenv("DISCORD_TOKEN")

    if not TOKEN:
        print("❌ ERROR: DISCORD_TOKEN not found in environment variables.")
        print("👉 How to fix:")
        print("1. Create a file named .env in the same directory as the bot.")
        print("2. Add this line to the .env file: DISCORD_TOKEN='your_bot_token_here'")
        return

    async with bot:
        await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot shutting down.")
