import discord
from discord.ext import commands, tasks
import datetime
import asyncio
import os
import json
from dotenv import load_dotenv

# --- SETUP ---
load_dotenv()

# Define file paths for our data
DATA_FOLDER = "data"
PLAY_TIMES_FILE = os.path.join(DATA_FOLDER, "play_times.json")
LEADERBOARD_FILE = os.path.join(DATA_FOLDER, "leaderboard.json")
GAME_ROLES_FILE = os.path.join(DATA_FOLDER, "game_roles.json")
GAME_LEADERBOARD_FILE = os.path.join(DATA_FOLDER, "game_leaderboard.json")  # <-- NEW


# --- DATA HELPER FUNCTIONS ---
def setup_data_files():
    """Ensures the data directory and necessary JSON files exist."""
    os.makedirs(DATA_FOLDER, exist_ok=True)
    # Added the new file to the setup list
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
    """Saves data to a JSON file."""
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=4)


# --- BOT INITIALIZATION ---
intents = discord.Intents.default()
intents.presences = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Load data from files into memory
playing_start_times = load_data(PLAY_TIMES_FILE)
# Convert string keys back to int for user IDs
playing_start_times = {int(k): v for k, v in playing_start_times.items()}
# Convert ISO string times back to datetime objects
for user_id, data in playing_start_times.items():
    data["start_time"] = datetime.datetime.fromisoformat(data["start_time"])
    # Ensure milestones_hit is a set
    data["milestones_hit"] = set(data.get("milestones_hit", []))

leaderboard_data = load_data(LEADERBOARD_FILE)
game_roles = load_data(GAME_ROLES_FILE)
game_leaderboard_data = load_data(GAME_LEADERBOARD_FILE)  # <-- NEW

# Define hour milestones (in minutes) and corresponding messages
milestone_messages = {
    60: "⏱️ Wow, such dedication! You've been gaming for 1 hour!",
    120: "🎮 What a gamer! You've reached 2 hours!",
    180: "🔥 Batak ampota! 3 hours of solid play!",
    240: "😳 Are you okay? That’s 4 hours!",
    300: "👀 This is turning into a marathon. 5 hours and counting!",
}


# --- HELPER FUNCTIONS ---
def get_announcement_channel(member):
    """Finds the designated announcement channel, 'presence-update'."""
    return discord.utils.get(member.guild.text_channels, name="presence-update")


def format_duration(seconds):
    """Formats seconds into a human-readable string (Hh Mm Ss)."""
    if seconds < 60:
        return f"{int(seconds)}s"
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{int(hours)}h {int(minutes)}m"
    return f"{int(minutes)}m {int(seconds)}s"


async def update_leaderboard(member, duration_seconds):
    """Updates the user leaderboard with playtime."""
    guild_id_str = str(member.guild.id)
    user_id_str = str(member.id)

    if guild_id_str not in leaderboard_data:
        leaderboard_data[guild_id_str] = {}

    leaderboard_data[guild_id_str][user_id_str] = leaderboard_data[guild_id_str].get(user_id_str, 0) + duration_seconds
    save_data(LEADERBOARD_FILE, leaderboard_data)


# <-- NEW HELPER FUNCTION ---
async def update_game_leaderboard(guild, game_name, duration_seconds):
    """Updates the game leaderboard with playtime."""
    guild_id_str = str(guild.id)

    if guild_id_str not in game_leaderboard_data:
        game_leaderboard_data[guild_id_str] = {}

    game_leaderboard_data[guild_id_str][game_name] = game_leaderboard_data[guild_id_str].get(game_name,
                                                                                             0) + duration_seconds
    save_data(GAME_LEADERBOARD_FILE, game_leaderboard_data)


# --- END OF NEW HELPER FUNCTION -->

async def handle_game_role(member, game_name, action="add"):
    """Adds or removes a game-specific role from a member."""
    guild_id_str = str(member.guild.id)
    if guild_id_str not in game_roles or not game_name:
        return

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
                print(
                    f"Failed to manage role '{role.name}' for {member.name} in '{member.guild.name}'. Missing permissions.")
            except discord.HTTPException as e:
                print(f"An HTTP error occurred while managing roles: {e}")


# --- BOT EVENTS ---
@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")
    check_milestones.start()


@bot.event
async def on_presence_update(before, after):
    if after.bot:
        return

    channel = get_announcement_channel(after)

    if before.status != after.status:
        if channel:
            if after.status == discord.Status.online and before.status == discord.Status.offline:
                await channel.send(f"🟢 {after.mention} just came online.")
            elif after.status == discord.Status.offline:
                await channel.send(f"⚫ {after.mention} just went offline.")

    before_game = next((a for a in before.activities if a.type == discord.ActivityType.playing), None)
    after_game = next((a for a in after.activities if a.type == discord.ActivityType.playing), None)

    if not before_game and after_game:
        start_time = datetime.datetime.now(datetime.UTC)
        playing_start_times[after.id] = {
            "start_time": start_time.isoformat(),
            "game": after_game.name,
            "milestones_hit": [],
            "guild_id": after.guild.id,
            "channel_id": channel.id if channel else None
        }
        save_data(PLAY_TIMES_FILE, playing_start_times)
        await handle_game_role(after, after_game.name, action="add")
        if channel:
            await channel.send(f"🎮 {after.name} started playing **{after_game.name}**!")

    elif (before_game and not after_game) or (before.status != after.status and after.status == discord.Status.offline):
        if after.id in playing_start_times:
            start_info = playing_start_times.pop(after.id)
            start_time = datetime.datetime.fromisoformat(start_info["start_time"])
            duration = (datetime.datetime.now(datetime.UTC) - start_time).total_seconds()

            # UPDATED: Log time to both leaderboards
            await update_leaderboard(after, duration)
            await update_game_leaderboard(after.guild, start_info["game"], duration)

            await handle_game_role(after, start_info["game"], action="remove")
            save_data(PLAY_TIMES_FILE, playing_start_times)
            if channel:
                await channel.send(
                    f"⏹️ {after.name} stopped playing **{start_info['game']}** after {format_duration(duration)}.")

    elif before_game and after_game and before_game.name != after_game.name:
        start_info = playing_start_times.pop(after.id)
        start_time = datetime.datetime.fromisoformat(start_info["start_time"])
        duration = (datetime.datetime.now(datetime.UTC) - start_time).total_seconds()

        # UPDATED: Log time for the old game to both leaderboards
        await update_leaderboard(after, duration)
        await update_game_leaderboard(after.guild, start_info["game"], duration)

        await handle_game_role(after, before_game.name, action="remove")

        new_start_time = datetime.datetime.now(datetime.UTC)
        playing_start_times[after.id] = {
            "start_time": new_start_time.isoformat(),
            "game": after_game.name,
            "milestones_hit": [],
            "guild_id": after.guild.id,
            "channel_id": channel.id if channel else None
        }
        save_data(PLAY_TIMES_FILE, playing_start_times)
        await handle_game_role(after, after_game.name, action="add")
        if channel:
            await channel.send(f"🔄 {after.name} switched from **{before_game.name}** to **{after_game.name}**!")


# --- BACKGROUND TASKS ---
@tasks.loop(minutes=1)
async def check_milestones():
    now = datetime.datetime.now(datetime.UTC)
    for user_id, info in list(playing_start_times.items()):
        start_time = datetime.datetime.fromisoformat(info["start_time"])
        minutes_played = int((now - start_time).total_seconds() // 60)

        for milestone_minutes, message in milestone_messages.items():
            if minutes_played >= milestone_minutes and milestone_minutes not in set(info["milestones_hit"]):
                guild = bot.get_guild(info["guild_id"])
                if guild:
                    member = guild.get_member(user_id)
                    channel = guild.get_channel(info["channel_id"])
                    if member and channel:
                        await channel.send(f"**{member.mention}** {message}")

                info["milestones_hit"].append(milestone_minutes)
                save_data(PLAY_TIMES_FILE, playing_start_times)


# --- COMMANDS ---
@bot.command(name="leaderboard", help="Shows the server's gaming leaderboard for users.")
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


# <-- NEW COMMAND ---
@bot.command(name="topgames", help="Shows the most played games on the server.")
async def topgames(ctx):
    guild_id_str = str(ctx.guild.id)
    if guild_id_str not in game_leaderboard_data or not game_leaderboard_data[guild_id_str]:
        await ctx.send("No game leaderboard data has been recorded for this server yet!")
        return

    # Sort games by playtime
    sorted_games = sorted(game_leaderboard_data[guild_id_str].items(), key=lambda item: item[1], reverse=True)

    embed = discord.Embed(title=f"🎮 Most Played Games in {ctx.guild.name}", color=discord.Color.orange())

    description = ""
    for i, (game_name, total_seconds) in enumerate(sorted_games[:10], 1):
        emoji = ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else "🔹"
        description += f"{emoji} **{game_name}**: {format_duration(total_seconds)}\n"

    embed.description = description
    await ctx.send(embed=embed)


# --- END OF NEW COMMAND -->


@bot.command(name="whoplays", help="Shows who is currently playing a specific game. Usage: !whoplays \"Game Name\"")
async def whoplays(ctx, *, game_name: str):
    playing_now = []
    now = datetime.datetime.now(datetime.UTC)
    guild_id = ctx.guild.id

    for user_id, info in playing_start_times.items():
        if info["game"].lower() == game_name.lower() and info["guild_id"] == guild_id:
            member = ctx.guild.get_member(user_id)
            if member:
                start_time = datetime.datetime.fromisoformat(info["start_time"])
                duration = format_duration((now - start_time).total_seconds())
                playing_now.append(f"• **{member.display_name}** (for {duration})")

    if not playing_now:
        await ctx.send(f"No one is currently playing **{game_name}** in this server.")
        return

    embed = discord.Embed(
        title=f"Players currently in {game_name}",
        description="\n".join(playing_now),
        color=discord.Color.blue()
    )
    await ctx.send(embed=embed)


@bot.command(name="addgamerole",
             help="Links a game to a role for auto-assigning. Usage: !addgamerole \"Game Name\" @Role")
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


# --- MAIN EXECUTION ---
async def main():
    """Main function to start the bot."""
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("ERROR: DISCORD_TOKEN not found in .env file.")
        return

    setup_data_files()

    async with bot:
        try:
            await bot.start(TOKEN)
        except (discord.HTTPException, discord.GatewayNotFound, discord.ConnectionClosed, OSError) as e:
            print(f"🔄 Connection error: {e}. Bot will attempt to restart.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot shutting down.")