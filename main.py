import discord
from discord.ext import tasks, commands
import datetime
import logging
import os
import sys
import asyncio
from dotenv import load_dotenv

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

# Load environment variables
CURRENT_PATH = os.path.dirname(os.path.abspath(__file__))
SECRET_PATH: str = os.path.join(CURRENT_PATH, "secrets.env")
load_dotenv(SECRET_PATH)
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    logging.error("Provide a bot token")
    sys.exit(0)
COGS_PATH: str = os.path.join(CURRENT_PATH, "cogs")

# Configuration
ILOVEPCS_COLOR = 9806321
ILOVEPCS_ID = 981991037226602546
TROUBLESHOOT_FORUM_ID = 1184892779671851068
STAFF_ID = [1052986282273407017]
BUMP_CHANNEL_ID = 1211083331546914866
REMINDER_TIME = 15
AUTO_CLOSE_TIME = 30
CLOSE_ON_LEAVE = True  # Enable automatic closure when OP leaves

def create_embed(title=None, description=None, title_url=None, image_url=None, footer_text=None, footer_url=None, thumbnail_url=None) -> discord.Embed:
    """
    Create a standardized embed with consistent styling.

    Args:
        description: The text content for the embed

    Returns:
        A formatted discord.Embed object
    """
    embed = discord.Embed(title=title, description=description, url=title_url, color=ILOVEPCS_COLOR)
    if image_url:
        embed.set_image(url=image_url)
    if footer_text:
        embed.set_footer(text=footer_text, icon_url=footer_url)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    return embed

class CloseButton(discord.ui.Button):
    def __init__(self, allowed_roles, bot_instance, thread=None):
        super().__init__(
            style=discord.ButtonStyle.red, label="Close Thread", custom_id="close"
        )
        self.allowed_roles = allowed_roles
        self.bot = bot_instance
        self.thread = thread

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not self.thread:
            self.thread = interaction.channel

        if interaction.user == self.thread.owner or any(
            role.id in self.allowed_roles for role in interaction.user.roles
        ):
            await self.thread.edit(
                archived=True,
                locked=True,
                applied_tags=self.bot.SOLVED_CLOSED_TAG,
                reason=f"Closed by {interaction.user}",
            )
        
            # Send embed message
            embed = create_embed(
                "Thread Closed",
                f"This thread has been closed by {interaction.user.mention}"
            )
            await interaction.followup.send(embed=embed)

            # Cleanup tracking (unchanged)
            thread_id = self.thread.id
            thread_owner_id = self.thread.owner.id
            if thread_id in self.bot.thread_activity:
                del self.bot.thread_activity[thread_id]
            if thread_owner_id in self.bot.track_posts:
                del self.bot.track_posts[thread_owner_id]
            if thread_id in self.bot.scheduled_reminders:
                self.bot.scheduled_reminders[thread_id].cancel()
                del self.bot.scheduled_reminders[thread_id]

            self.view.stop()
        else:
            embed = create_embed(
                "Permission Denied",
                "You do not have permission to close this thread!",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


class MarkPriorityButton(discord.ui.Button):
    def __init__(self, allowed_roles, bot_instance, thread=None):
        super().__init__(
            style=discord.ButtonStyle.blurple,
            label="Bump",
            custom_id="bump"
        )
        self.allowed_roles = allowed_roles
        self.bot = bot_instance
        self.thread = thread

    class MarkPriorityModal(discord.ui.Modal, title="⭐ Mark Priority"):
        reason = discord.ui.TextInput(
            label="Reason",
            default="Inactive Post",
            required=True,
            max_length=200
        )

        def __init__(self, parent_button):
            super().__init__()
            self.parent = parent_button

        async def on_submit(self, interaction: discord.Interaction):
            await self.parent.process_bump(
                interaction,
                f"Staff Reason: {self.reason.value}"
            )

    async def process_bump(self, interaction, reason_text):
        await interaction.response.defer(ephemeral=True)
        if not self.thread:
            self.thread = interaction.channel

        bump_channel = interaction.guild.get_channel(BUMP_CHANNEL_ID)
        if not bump_channel:
            embed = create_embed(
                "Error",
                "Could not find bump channel!",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        thread_id = self.thread.id
        is_staff = any(role.id in self.allowed_roles for role in interaction.user.roles)
        is_op = interaction.user == self.thread.owner

        if is_op and self.bot.BUMP_BOOL.get(thread_id, False):
            # OP bump embed
            bump_embed = create_embed(
                "Post Marked Priority",
                f"**Posted by:** {interaction.user.mention}\n"
                f"**Reason:** Inactive post\n"
                f"**Thread:** {self.thread.mention}",
            )
            await bump_channel.send(embed=bump_embed)
        
            self.bot.BUMP_BOOL[thread_id] = False
        
            # Response embed
            response_embed = create_embed(
                "Success",
                "✅ Post marked as priority in <#1211083331546914866>"
            )
            await interaction.followup.send(embed=response_embed, ephemeral=True)
    
        elif is_staff:
            # Staff bump embed
            bump_embed = create_embed(
                "Thread Bumped",
                f"**Staff:** {interaction.user.mention}\n"
                f"**Reason:** {reason_text}\n"
                f"**Thread:** {self.thread.mention}\n"
                f"**OP:** {self.thread.owner.mention}",
            )
            await bump_channel.send(embed=bump_embed)
        
            response_embed = create_embed("Success", "✅ Thread bumped!")
            await interaction.followup.send(embed=response_embed, ephemeral=True)
    
        else:
            embed = create_embed(
                "Permission Denied",
                "⛔ You don't have bump permissions!\n"
                "OPs can bump once per inactive period.\n"
                "Staff can bump with reason.",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def callback(self, interaction: discord.Interaction):
        if not self.thread:
            self.thread = interaction.channel

        thread_id = self.thread.id
        is_staff = any(role.id in self.allowed_roles for role in interaction.user.roles)
        is_op = interaction.user == self.thread.owner

        if is_staff:
            # Staff gets modal
            await interaction.response.send_modal(self.MarkPriorityModal(self))
        elif is_op and self.bot.BUMP_BOOL.get(thread_id, False):
            # OP gets automatic bump
            await self.process_bump(interaction, "Inactive post")
        else:
            await interaction.response.send_message(
                "⛔ You do not have bump permissions!",
                ephemeral=True
            )


class SupportGuidelines(discord.ui.Button):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.blurple,
            label="Support Guidelines",
            custom_id="support_guidelines",
        )

    async def callback(self, interaction: discord.Interaction):
        embed = create_embed(
            "Support Guidelines",
            f"Please review our support guidelines in <#1100092864635080925>"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class StaffTools(discord.ui.Button):
    def __init__(self, allowed_roles, staff_view):
        super().__init__(
            style=discord.ButtonStyle.blurple,
            label="Staff Only",
            custom_id="staff_only",
        )
        self.allowed_roles = allowed_roles
        self.staff_view = staff_view

    async def callback(self, interaction: discord.Interaction):
        embed = create_embed(
            "Staff Guide",
            "Check pinned messages in <#1150193787721752718> for staff guidelines."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class StaffGuide(discord.ui.Button):
    def __init__(self):
        super().__init__(
            style=discord.ButtonStyle.blurple,
            label="Staff Guide",
            custom_id="staff_guide",
        )

    async def callback(self, interaction: discord.Interaction):
        embed = create_embed(
            "Staff Guide",
            "Check pinned messages in <#1150193787721752718> for staff guidelines."
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class OpeningView(discord.ui.View):
    def __init__(self, allowed_roles, bot_instance, staff_view, thread=None):
        super().__init__(timeout=None)
        self.thread = thread
        self.allowed_roles = allowed_roles
        self.bot = bot_instance
        self.add_item(CloseButton(allowed_roles, bot_instance, thread))
        self.add_item(SupportGuidelines())
        self.add_item(StaffTools(allowed_roles, staff_view))


class ReminderView(discord.ui.View):
    def __init__(self, allowed_roles, bot_instance, thread=None):
        super().__init__(timeout=None)
        self.thread = thread
        self.allowed_roles = allowed_roles
        self.bot = bot_instance
        self.add_item(CloseButton(allowed_roles, bot_instance, thread))
        self.add_item(MarkPriorityButton(allowed_roles, bot_instance, thread))


class StaffToolsView(discord.ui.View):
    def __init__(self, allowed_roles, bot_instance, thread=None):
        super().__init__(timeout=None)
        self.thread = thread
        self.allowed_roles = allowed_roles
        self.bot = bot_instance
        self.add_item(MarkPriorityButton(allowed_roles, bot_instance, thread))
        self.add_item(StaffGuide())


class DiscordBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.thread_activity = {}
        self.scheduled_reminders = {}
        self.track_posts = {}
        self.TROUBLESHOOT_FORUM = None
        self.AWAITING_RESPONSE_TAG = None
        self.IN_PROGRESS_TAG = None
        self.SOLVED_CLOSED_TAG = None
        self.INACTIVE_TAG = None
        self.BUMP_CHANNEL = None
        self.BUMP_BOOL = {}

    async def setup_hook(self):
        # Initialize any persistent views here
        staff_view = StaffToolsView(STAFF_ID, self)
        self.add_view(staff_view)
        self.add_view(OpeningView(STAFF_ID, self, staff_view))
        self.add_view(ReminderView(STAFF_ID, self))

    async def on_ready(self):
        logging.info("Logged in as %s", self.user.name)
        guild = self.get_guild(ILOVEPCS_ID)
        self.TROUBLESHOOT_FORUM = guild.get_channel(TROUBLESHOOT_FORUM_ID)
        self.BUMP_CHANNEL = guild.get_channel(BUMP_CHANNEL_ID)
        if not self.TROUBLESHOOT_FORUM:
            logging.error("Troubleshoot forum channel not found!")
            return

        self.AWAITING_RESPONSE_TAG = [
            self.TROUBLESHOOT_FORUM.get_tag(1184982256423551006)
        ]
        self.IN_PROGRESS_TAG = [self.TROUBLESHOOT_FORUM.get_tag(1185355746146275368)]
        self.SOLVED_CLOSED_TAG = [self.TROUBLESHOOT_FORUM.get_tag(1185355888102490112)]
        self.INACTIVE_TAG = [self.TROUBLESHOOT_FORUM.get_tag(1406317680289644646)]
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="forum posts"
            )
        )
        self.check_inactivity_task.start()

    async def on_thread_create(self, thread):
        if thread.parent_id != TROUBLESHOOT_FORUM_ID:
            return

        # Checks if user has already has an active post.
        if thread.owner.id in self.track_posts:
            # Auto close the thread.
            await thread.edit(
                archived=True,
                locked=True,
                applied_tags=self.SOLVED_CLOSED_TAG,
                reason="OP already has an active post.",
            )
            await thread.send(
                embed=create_embed(
                "Duplicate Post",
                f"Closing this post because you already have an active post: {thread.mention}",
                )
            )
            return

        # Associate thread_id with OP's id
        self.track_posts[thread.owner.id] = thread.id

        # Set 2 second slow mode
        await thread.edit(slowmode_delay=2, applied_tags=self.AWAITING_RESPONSE_TAG)
        # Create close button message with bump button
        staff_view = StaffToolsView(STAFF_ID, self)
        opening_view = OpeningView(STAFF_ID, self, staff_view, thread=thread)
        message = await thread.send(
            embed=create_embed(
                "Support Request",
                "Need help? Support will be with you shortly!\n"
                f"- Only {thread.owner.mention} and authorized roles can close this thread\n"
                "- This thread will be automatically closed after 24 hours of inactivity"
            ),
            view=opening_view,
        )

        # Pin the message
        await message.pin()

        # Track initial activity with timezone-aware datetime
        self.thread_activity[thread.id] = datetime.datetime.now(datetime.timezone.utc)

        # Schedule reminder
        self.scheduled_reminders[thread.id] = asyncio.create_task(
            self.schedule_thread_reminder(thread)
        )
        # Set bump boolean, used for cooldown
        self.BUMP_BOOL[thread.id] = False

    async def on_message(self, message):
        thread = message.channel
        await self.process_commands(message)
        # Update activity for thread messages
        if isinstance(thread, discord.Thread):
            if (
                thread.parent_id == TROUBLESHOOT_FORUM_ID
                and message.author.id != thread.owner_id
                and not message.author.bot
            ):
                thread_id = thread.id
                # Update tag to In Progress:
                await thread.edit(applied_tags=self.IN_PROGRESS_TAG)
                # Update with timezone-aware datetime
                self.thread_activity[thread_id] = datetime.datetime.now(
                    datetime.timezone.utc
                )

                # Disallow the user to bump a post when active
                self.BUMP_BOOL[thread.id] = False

                # Reset reminder if thread was inactive
                if thread_id in self.scheduled_reminders:
                    self.scheduled_reminders[thread_id].cancel()
                    self.scheduled_reminders[thread_id] = asyncio.create_task(
                        self.schedule_thread_reminder(message.channel)
                    )
        
    async def on_member_remove(self, member):
        """Automatically close threads when OP leaves"""
        thread_id = self.track_posts.get(member.id)
        if not thread_id:
            return  # No thread to close

        try:
            thread = self.get_channel(thread_id)
            if thread and isinstance(thread, discord.Thread):
                await thread.edit(
                    archived=True,
                    locked=True,
                    applied_tags=self.SOLVED_CLOSED_TAG,
                    reason=f"Automatically closed - OP left the server"
                )
                await thread.send(
                    embed=create_embed(
                        "Thread Closed",
                        "⏳ This thread has been closed because the original poster left the server",
                    )
                )

        except Exception as e:
            logging.error("Error closing thread %s: %s", thread_id, e)
        finally:
            # Always clean up tracking
            if member.id in self.track_posts:
                del self.track_posts[member.id]
            if thread_id in self.thread_activity:
                del self.thread_activity[thread_id]
            if thread_id in self.scheduled_reminders:
                self.scheduled_reminders[thread_id].cancel()
                del self.scheduled_reminders[thread_id]

    async def schedule_thread_reminder(self, thread):
         # Wait for REMINDER_TIME hours
        await asyncio.sleep(REMINDER_TIME)  # Convert hours to seconds

        # Check if thread still exists and is active
        if thread.id not in self.thread_activity:
            return

        last_active = self.thread_activity[thread.id]
        now = datetime.datetime.now(datetime.timezone.utc)
        # Check if the thread has been inactive for at least REMINDER_TIME hours
        if (now - last_active).total_seconds() >= REMINDER_TIME:
            view = ReminderView(
                STAFF_ID, self, thread=thread
            )  # Close button with bump button

            # Allow the user to bump a post
            self.BUMP_BOOL[thread.id] = True

            # Send reminder
            await thread.send(
                embed=create_embed(
                    "Inactivity Notice",
                    f"⚠️ This thread has been inactive for {REMINDER_TIME} hours. "
                    f"{thread.owner.mention}, do you still need help?\n\n"
                    "The thread will be automatically closed after 24 hours of total inactivity.",
                ),
                view=view
            )
            await thread.edit(applied_tags=self.INACTIVE_TAG)

        # Cleanup: remove the task from the tracking dict
        if thread.id in self.scheduled_reminders:
            del self.scheduled_reminders[thread.id]

    @tasks.loop(seconds=1)
    async def check_inactivity_task(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        to_remove = []

        for thread_id, last_active in list(self.thread_activity.items()):
            # Check for 24 hours of inactivity
            if (now - last_active).total_seconds() >= AUTO_CLOSE_TIME:
                thread = self.get_channel(thread_id)
                if thread and isinstance(thread, discord.Thread):
                    try:
                        await thread.send(
                            embed=create_embed(
                            "Thread Closed",
                            "⏳ This thread is being closed due to 24 hours of inactivity",
                            )
                        )
                        await thread.edit(
                            archived=True,
                            locked=True,
                            applied_tags=self.SOLVED_CLOSED_TAG,
                            reason="Inactivity for 24 hours",
                        )
                    except discord.HTTPException:
                        pass
                to_remove.append(thread_id)

        # Cleanup closed threads
        for thread_id in to_remove:
            if thread_id in self.thread_activity:
                del self.thread_activity[thread_id]
            if thread.owner.id in self.track_posts:
                del self.track_posts[thread.owner.id]
            if thread_id in self.scheduled_reminders:
                self.scheduled_reminders[thread_id].cancel()
                del self.scheduled_reminders[thread_id]

    @check_inactivity_task.before_loop
    async def before_check_inactivity(self):
        await self.wait_until_ready()


bot = DiscordBot(command_prefix="!", intents=intents)


@bot.command(name="restartforum")
@commands.is_owner()
async def restart_forum_bot(ctx):
    """Command to restart the bot"""
    await ctx.send("Restarting...")
    os.execv(sys.executable, ["python"] + sys.argv)


@bot.command(name="tags")
@commands.is_owner()
async def get_forum_tags(ctx):
    """Fetches and displays the tags of a specific forum channel."""
    try:
        troubleshoot_forum = bot.get_channel(TROUBLESHOOT_FORUM_ID)
        if not isinstance(troubleshoot_forum, discord.ForumChannel):
            await ctx.send("This command only works for forum channels!")
            return

        tags = troubleshoot_forum.available_tags
        if not tags:
            await ctx.send("No tags are available in this forum channel.")
            return

        tag_info = [f"`{tag.name}` (ID: `{tag.id}`)" for tag in tags]
        await ctx.send(
            f"**Available tags in {troubleshoot_forum.name}:**\n" + "\n".join(tag_info)
        )
    except discord.HTTPException as e:
        await ctx.send(f"❌ An error occurred while fetching tags: `{e}`")


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, root_logger=True)