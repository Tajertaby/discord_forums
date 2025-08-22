import discord
from discord.ext import tasks, commands
import datetime
import logging
import os
import sys
import asyncio
from dotenv import load_dotenv
from typing import List, Optional, Dict, Any

# Setup intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

# Load environment variables
CURRENT_PATH = os.path.dirname(os.path.abspath(__file__))
SECRET_PATH = os.path.join(CURRENT_PATH, "secrets.env")
load_dotenv(SECRET_PATH)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
if not DISCORD_TOKEN:
    logging.error("Provide a bot token")
    sys.exit(0)
COGS_PATH = os.path.join(CURRENT_PATH, "cogs")


# Configuration constants
class Config:
    ILOVEPCS_COLOR = 9806321
    ILOVEPCS_ID = 981991037226602546
    TROUBLESHOOT_FORUM_ID = 1184892779671851068
    STAFF_ID = [1052986282273407017]
    BUMP_CHANNEL_ID = 1211083331546914866
    REMINDER_TIME = 24 * 3600
    AUTO_CLOSE_TIME = 48 * 3600
    CLOSE_ON_LEAVE = True


def create_embed(
    title: Optional[str] = None,
    description: Optional[str] = None,
    title_url: Optional[str] = None,
    image_url: Optional[str] = None,
    footer_text: Optional[str] = None,
    footer_url: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
) -> discord.Embed:
    """Create a standardized embed with consistent styling."""
    embed = discord.Embed(
        title=title, description=description, url=title_url, color=Config.ILOVEPCS_COLOR
    )
    if image_url:
        embed.set_image(url=image_url)
    if footer_text:
        embed.set_footer(text=footer_text, icon_url=footer_url)
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    return embed


class BaseButton(discord.ui.Button):
    """Base button class with common functionality."""

    def __init__(
        self,
        bot_instance: "DiscordBot",
        thread: Optional[discord.Thread] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.bot = bot_instance
        self.thread = thread

    def has_permission(self, user: discord.Member) -> bool:
        """Check if user has staff permissions."""
        return any(role.id in Config.STAFF_ID for role in user.roles)

    def is_thread_owner(self, user: discord.Member) -> bool:
        """Check if user is the thread owner."""
        return self.thread and user == self.thread.owner

    async def send_permission_denied(
        self, interaction: discord.Interaction, message: str, response=False
    ):
        """Send a permission denied message."""
        embed = create_embed(title="⛔ Permission Denied!", description=message)
        if not response:
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)


class CloseButton(BaseButton):
    def __init__(
        self, bot_instance: "DiscordBot", thread: Optional[discord.Thread] = None
    ):
        super().__init__(
            bot_instance,
            thread,
            style=discord.ButtonStyle.red,
            label="🔒 Close Post",
            custom_id="close",
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if not self.thread:
            self.thread = interaction.channel

        if self.is_thread_owner(interaction.user) or self.has_permission(
            interaction.user
        ):
            await self._close_thread(interaction)
        else:
            await self.send_permission_denied(
                interaction, "You do not have permission to close this post!"
            )

    async def _close_thread(self, interaction: discord.Interaction):
        """Close the thread and cleanup tracking."""
        embed = create_embed(
            title="🔒 Post Closed!",
            description=f"This post has been closed by {interaction.user.mention} ({interaction.user.name}).",
        )
        await interaction.followup.send(embed=embed)
        post_tags = self.track_posts[self.thread.owner.id][2] + self.tag.solved_closed
        await self.thread.edit(
            archived=True,
            locked=True,
            applied_tags=post_tags,
            reason=f"Closed by {interaction.user}",
        )

        # Cleanup tracking
        self.bot.cleanup_thread_tracking(self.thread.id, self.thread.owner.id)
        self.view.stop()


class MarkPriorityButton(BaseButton):
    def __init__(
        self, bot_instance: "DiscordBot", thread: Optional[discord.Thread] = None
    ):
        super().__init__(
            bot_instance,
            thread,
            style=discord.ButtonStyle.blurple,
            label="⭐ Mark Priority",
            custom_id="mark_priority",
        )

    class MarkPriorityModal(discord.ui.Modal, title="Mark Priority Reason"):
        def __init__(self, parent_button: "MarkPriorityButton"):
            super().__init__()
            self.parent = parent_button
            self.reason = discord.ui.TextInput(
                label="Reason", default="Inactive Post", required=True, max_length=200
            )
            self.add_item(self.reason)

        async def on_submit(self, interaction: discord.Interaction):
            await self.parent.process_bump(interaction, f"{self.reason.value}")

    async def callback(self, interaction: discord.Interaction):
        if not self.thread:
            self.thread = interaction.channel

        thread_id = self.thread.id
        is_staff = self.has_permission(interaction.user)
        is_op = self.is_thread_owner(interaction.user)

        if is_staff:
            await interaction.response.send_modal(self.MarkPriorityModal(self))
        elif is_op and self.bot.bump_bool.get(thread_id, False):
            await self.process_bump(interaction, "Inactive post")
        else:
            await self._send_priority_error(interaction)

    async def process_bump(self, interaction: discord.Interaction, reason_text: str):
        """Process the priority bump request."""
        await interaction.response.defer(ephemeral=True)

        bump_channel = interaction.guild.get_channel(Config.BUMP_CHANNEL_ID)
        if not bump_channel:
            logging.error("Bump channel not found!")
            return

        # Create and send bump embed
        bump_embed = create_embed(
            title="⚠️ Attention Required!",
            description=f"**Post:** [{self.thread.name}]({self.thread.jump_url})\n"
            f"**Marked By:** {interaction.user.mention} ({interaction.user.name})\n"
            f"**Reason:** {reason_text}\n"
            f"**Original Poster:** {self.thread.owner.mention} ({self.thread.owner.name})",
        )
        await bump_channel.send(embed=bump_embed)

        # Update bump status for OP
        if self.is_thread_owner(interaction.user):
            self.bot.bump_bool[self.thread.id] = False

        # Send confirmation
        response_embed = create_embed(
            description="✅ Post marked as priority in <#1211083331546914866>"
        )
        await interaction.followup.send(embed=response_embed, ephemeral=True)

    async def _send_priority_error(self, interaction: discord.Interaction):
        """Send priority permission error message."""
        embed = create_embed(
            title="⛔ Permission Denied!",
            description="You cannot mark a post as a priority for these following reasons:\n"
            "- You are not the original poster.\n"
            "- You cannot mark a post as a priority when the post is active.\n"
            "- You can only mark a post as a priority once per inactivity period.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class InfoButton(BaseButton):
    """Base class for information buttons."""

    def __init__(self, label: str, custom_id: str, description: str):
        super().__init__(
            None, style=discord.ButtonStyle.blurple, label=label, custom_id=custom_id
        )
        self.description = description

    async def callback(self, interaction: discord.Interaction):
        embed = create_embed(description=self.description)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class SupportGuidelines(InfoButton):
    def __init__(self):
        super().__init__(
            "📖 Support Guidelines",
            "support_guidelines",
            "Review our support guidelines in <#1100092864635080925>",
        )


class StaffGuide(InfoButton):
    def __init__(self):
        super().__init__(
            "👔 Staff Guide",
            "staff_guide",
            "Check pinned messages in <#1150193787721752718> for staff guidelines.",
        )


class StaffTools(BaseButton):
    def __init__(self, bot_instance: "DiscordBot", staff_view: discord.ui.View):
        super().__init__(
            bot_instance,
            style=discord.ButtonStyle.blurple,
            label="👔 Staff Only",
            custom_id="staff_only",
        )
        self.staff_view = staff_view

    async def callback(self, interaction: discord.Interaction):
        if self.has_permission(interaction.user):
            await interaction.response.send_message(
                embed=create_embed(title="Staff Options"),
                view=self.staff_view,
                ephemeral=True,
            )
        else:
            await self.send_permission_denied(
                interaction,
                "You do not have permission to access staff tools!",
                response=True,
            )


# View classes
class BaseView(discord.ui.View):
    """Base view class."""

    def __init__(
        self, bot_instance: "DiscordBot", thread: Optional[discord.Thread] = None
    ):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.thread = thread


class OpeningView(BaseView):
    def __init__(
        self,
        bot_instance: "DiscordBot",
        staff_view: discord.ui.View,
        thread: Optional[discord.Thread] = None,
    ):
        super().__init__(bot_instance, thread)
        self.add_item(CloseButton(bot_instance, thread))
        self.add_item(SupportGuidelines())
        self.add_item(StaffTools(bot_instance, staff_view))


class ReminderView(BaseView):
    def __init__(
        self, bot_instance: "DiscordBot", thread: Optional[discord.Thread] = None
    ):
        super().__init__(bot_instance, thread)
        self.add_item(CloseButton(bot_instance, thread))
        self.add_item(MarkPriorityButton(bot_instance, thread))


class StaffToolsView(BaseView):
    def __init__(
        self, bot_instance: "DiscordBot", thread: Optional[discord.Thread] = None
    ):
        super().__init__(bot_instance, thread)
        self.add_item(MarkPriorityButton(bot_instance, thread))
        self.add_item(StaffGuide())


class ForumTags:
    """Container for forum tags."""

    def __init__(self, forum_channel: discord.ForumChannel):
        self.awaiting_response = forum_channel.get_tag(1184982256423551006)
        self.in_progress = forum_channel.get_tag(1185355746146275368)
        self.solved_closed = forum_channel.get_tag(1185355888102490112)
        self.inactive = forum_channel.get_tag(1406317680289644646)


class ThreadManager:
    """Manages thread tracking and cleanup."""

    def __init__(self):
        self.activity: Dict[int, datetime.datetime] = {}
        self.scheduled_reminders: Dict[int, asyncio.Task] = {}
        self.track_posts: Dict[int, List[int]] = {}
        self.bump_bool: Dict[int, bool] = {}

    def cleanup_thread(self, thread_id: int, owner_id: int):
        """Clean up all tracking for a thread."""
        self.activity.pop(thread_id, None)
        self.track_posts.pop(owner_id, None)

        if thread_id in self.scheduled_reminders:
            self.scheduled_reminders[thread_id].cancel()
            del self.scheduled_reminders[thread_id]

        self.bump_bool.pop(thread_id, None)


class DiscordBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.thread_manager = ThreadManager()
        self.guild: Optional[discord.Guild] = None
        self.troubleshoot_forum: Optional[discord.ForumChannel] = None
        self.bump_channel: Optional[discord.TextChannel] = None
        self.tags: Optional[ForumTags] = None

    # Properties for backward compatibility and cleaner access
    @property
    def thread_activity(self) -> Dict[int, datetime.datetime]:
        return self.thread_manager.activity

    @property
    def scheduled_reminders(self) -> Dict[int, asyncio.Task]:
        return self.thread_manager.scheduled_reminders

    @property
    def track_posts(self) -> Dict[int, List[int]]:
        return self.thread_manager.track_posts

    @property
    def bump_bool(self) -> Dict[int, bool]:
        return self.thread_manager.bump_bool

    def cleanup_thread_tracking(self, thread_id: int, owner_id: int):
        """Public method to cleanup thread tracking."""
        self.thread_manager.cleanup_thread(thread_id, owner_id)

    async def setup_hook(self):
        """Initialize persistent views."""
        staff_view = StaffToolsView(self)
        self.add_view(staff_view)
        self.add_view(OpeningView(self, staff_view))
        self.add_view(ReminderView(self))

    async def on_ready(self):
        """Bot ready event handler."""
        logging.info("Logged in as %s", self.user.name)

        # Initialize guild and channels
        self.guild = self.get_guild(Config.ILOVEPCS_ID)
        self.troubleshoot_forum = self.guild.get_channel(Config.TROUBLESHOOT_FORUM_ID)
        self.bump_channel = self.guild.get_channel(Config.BUMP_CHANNEL_ID)

        if not self.troubleshoot_forum:
            logging.error("Troubleshoot forum channel not found!")
            return

        # Initialize tags
        self.tags = ForumTags(self.troubleshoot_forum)

        # Set bot presence
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching, name="forum posts"
            )
        )

        # Start background tasks
        self.check_inactivity_task.start()

    async def on_thread_create(self, thread: discord.Thread):
        """Handle new thread creation."""
        if thread.parent_id != Config.TROUBLESHOOT_FORUM_ID:
            return

        # Check for existing posts
        if await self._handle_duplicate_post(thread):
            return

        # Setup new thread
        await self._setup_new_thread(thread)

    async def on_thread_delete(self, thread: discord.Thread):
        """Handle thread deletion"""
        if thread.parent_id != Config.TROUBLESHOOT_FORUM_ID:
            return

        # Check if post is closed or not
        if self.tags.solved_closed in thread.applied_tags:
            return

        self.cleanup_thread_tracking(thread.id, thread.owner.id)

    async def _handle_duplicate_post(self, thread: discord.Thread) -> bool:
        """Handle duplicate posts by the same user."""
        if thread.owner.id in self.track_posts:
            existing_thread_id = self.track_posts[thread.owner.id][0]
            existing_thread = self.get_channel(existing_thread_id)

            embed = create_embed(
                title="Already Posted",
                description=f"Closing this post because you already have an [active post]({existing_thread.jump_url if existing_thread else 'unknown'}).",
            )
            await thread.send(thread.owner.mention, embed=embed)
            post_tags = self.track_posts[thread.owner.id][2] + self.tags.solved_closed
            await thread.edit(
                archived=True,
                locked=True,
                applied_tags=post_tags
                reason="OP already has an active post.",
            )

            return True
        return False

    async def _setup_new_thread(self, thread: discord.Thread):
        """Setup a new thread with initial configuration."""
        # Track the thread
        self.track_posts[thread.owner.id] = [thread.id, thread.owner.id, thread.applied_tags]
        post_tags = self.track_posts[thread.owner.id][2] + self.tags.awaiting_response
        # Configure thread
        await thread.edit(slowmode_delay=2, applied_tags=post_tags)

        # Create and send initial message
        staff_view = StaffToolsView(self)
        opening_view = OpeningView(self, staff_view, thread=thread)

        embed = create_embed(
            title="Troubleshooting Questions",
            description="Please answer the questions below. Do not create a new post if you have an active one; it will be auto-closed.\n\n"
            "1. What is the issue?\n"
            "2. What fixes you tried?\n"
            "3. What are your specs? If your PC turns on, download [HWInfo](https://www.hwinfo.com/download/) to find out. "
            "Alternatively, post a photo inside of your PC.\n"
            "4. Any recent logs in event viewer that may provide clues?\n"
            "5. Any recent BSODs? If so please paste the error message.\n"
            "6. Any recent changes to your PC?\n\n"
            "Extra information?",
        )

        message = await thread.send(
            thread.owner.mention, embed=embed, view=opening_view
        )
        await message.pin()

        # Initialize tracking
        self.thread_activity[thread.id] = datetime.datetime.now(datetime.timezone.utc)
        self.bump_bool[thread.id] = False

        # Schedule reminder
        self.scheduled_reminders[thread.id] = asyncio.create_task(
            self.schedule_thread_reminder(thread)
        )

    async def on_message(self, message: discord.Message):
        """Handle message events."""
        await self.process_commands(message)
        thread = message.channel
        if (isinstance(message.channel, discord.Thread)):
            if self.tags.solved_closed not in thread.applied_tags:
                await self._handle_thread_message(message)
            else:
                # Keep closed posts closed when new message is sent
                await thread.edit(archived=True)

    async def _handle_thread_message(self, message: discord.Message):
        """Handle messages in threads."""
        thread = message.channel

        if (
            thread.parent_id != Config.TROUBLESHOOT_FORUM_ID
            or message.author.bot
            or thread.owner.id not in self.track_posts
        ):
            return


        previous_user_id = self.track_posts[thread.owner.id][1]

        # Only update activity if different user posted
        if previous_user_id == message.author.id:
            return

        # Update tracking
        self.track_posts[thread.owner.id][1] = message.author.id
        self.thread_activity[thread.id] = datetime.datetime.now(datetime.timezone.utc)
        self.bump_bool[thread.id] = False
        # Update thread status
        if self.tags.in_progress not in thread.applied_tags:
            post_tags = self.track_posts[thread.owner.id][2] + self.tags.in_progress
            await thread.edit(applied_tags=post_tags)

        # Reset reminder
        await self._reset_thread_reminder(thread)

    async def _reset_thread_reminder(self, thread: discord.Thread):
        """Reset the reminder for a thread."""
        thread_id = thread.id
        if thread_id in self.scheduled_reminders:
            self.scheduled_reminders[thread_id].cancel()

        self.scheduled_reminders[thread_id] = asyncio.create_task(
            self.schedule_thread_reminder(thread)
        )

    async def on_member_remove(self, member: discord.Member):
        """Handle member leaving server."""
        if not Config.CLOSE_ON_LEAVE or member.id not in self.track_posts:
            return

        thread_id = self.track_posts[member.id][0]

        try:
            thread = self.get_channel(thread_id)
            if thread and isinstance(thread, discord.Thread):
                await self._close_thread_on_leave(thread)
        except Exception as e:
            logging.error("Error closing thread %s: %s", thread_id, e)
        finally:
            self.cleanup_thread_tracking(thread_id, member.id)

    async def _close_thread_on_leave(self, thread: discord.Thread):
        """Close thread when member leaves."""
        embed = create_embed(
            title="🔒 Post Closed!",
            description="This post has been closed due to the original poster leaving the server.",
        )
        await thread.send(embed=embed)
        post_tags = self.track_posts[thread.owner.id][2] + self.tag.solved_closed
        await thread.edit(
            archived=True,
            locked=True,
            applied_tags=post_tags,
            reason="Automatically closed - OP left the server",
        )

    async def schedule_thread_reminder(self, thread: discord.Thread):
        """Schedule a reminder for inactive thread."""
        await asyncio.sleep(Config.REMINDER_TIME)

        if thread.id not in self.thread_activity:
            return

        last_active = self.thread_activity[thread.id]
        now = datetime.datetime.now(datetime.timezone.utc)

        if (now - last_active).total_seconds() >= Config.REMINDER_TIME:
            await self._send_inactivity_reminder(thread, last_active)

        # Cleanup
        self.scheduled_reminders.pop(thread.id, None)

    async def _send_inactivity_reminder(
        self, thread: discord.Thread, last_active: datetime.datetime
    ):
        """Send inactivity reminder."""
        view = ReminderView(self, thread=thread)
        self.bump_bool[thread.id] = True

        embed = create_embed(
            title="⚠️ Inactivity Notice",
            description=f"This post has been inactive since <t:{int(last_active.timestamp())}:R>.\n"
            "The post will close without warning if there is inactivity for 48 hours.",
        )

        await thread.send(thread.owner.mention, embed=embed, view=view)
        post_tags = self.track_posts[thread.owner.id][2] + self.tags.inactive
        await thread.edit(applied_tags=post_tags)

    @tasks.loop(minutes=10)
    async def check_inactivity_task(self):
        """Check for inactive threads and auto-close them."""
        now = datetime.datetime.now(datetime.timezone.utc)
        to_remove = []

        for thread_id, last_active in list(self.thread_activity.items()):
            if (now - last_active).total_seconds() >= Config.AUTO_CLOSE_TIME:
                thread = self.get_channel(thread_id)
                if thread and isinstance(thread, discord.Thread):
                    await self._auto_close_inactive_thread(thread)
                to_remove.append(thread_id)

        # Cleanup closed threads
        for thread_id in to_remove:
            owner_id = None
            for uid, (tid, _) in self.track_posts.items():
                if tid == thread_id:
                    owner_id = uid
                    break
            if owner_id:
                self.cleanup_thread_tracking(thread_id, owner_id)

    async def _auto_close_inactive_thread(self, thread: discord.Thread):
        """Auto-close an inactive thread."""
        embed = create_embed(
            title="🔒 Post Closed!",
            description="This post has been closed due to inactivity.",
        )
        await thread.send(embed=embed)
        post_tags = self.track_posts[thread.owner.id][2] + self.tags.solved_closed
        await thread.edit(
            archived=True,
            locked=True,
            applied_tags=post_tags,
            reason="Inactivity for 48 hours",
        )

    @check_inactivity_task.before_loop
    async def before_check_inactivity(self):
        """Wait for bot to be ready before starting inactivity checks."""
        await self.wait_until_ready()


# Initialize bot
bot = DiscordBot(command_prefix="!", intents=intents)


@bot.command(name="restartforum")
@commands.is_owner()
async def restart_forum_bot(ctx):
    """Command to restart the bot."""
    await ctx.send("Restarting...")
    os.execv(sys.executable, ["python"] + sys.argv)


@bot.command(name="tags")
@commands.is_owner()
async def get_forum_tags(ctx):
    """Fetch and display forum tags."""
    try:
        if not isinstance(bot.troubleshoot_forum, discord.ForumChannel):
            await ctx.send(
                "Troubleshoot forum channel not found or not a forum channel!"
            )
            return

        tags = bot.troubleshoot_forum.available_tags
        if not tags:
            await ctx.send("No tags are available in this forum channel.")
            return

        tag_info = [f"`{tag.name}` (ID: `{tag.id}`)" for tag in tags]
        await ctx.send(
            f"**Available tags in {bot.troubleshoot_forum.name}:**\n"
            + "\n".join(tag_info)
        )
    except discord.HTTPException as e:
        await ctx.send(f"❌ An error occurred while fetching tags: `{e}`")
        logging.error("Error fetching tags: %s", e)


if __name__ == "__main__":
    bot.run(DISCORD_TOKEN, root_logger=True)
