import discord
from redbot.core.bot import Red
from redbot.core import checks, commands, Config
from redbot.core.i18n import cog_i18n, Translator
from redbot.core.utils._internal_utils import send_to_owners_with_prefix_replaced
from redbot.core.utils.chat_formatting import escape, pagify

from .thetatypes import (
    ThetaStream
    )
from .thetaerrors import (
    APIError,
    InvalidThetaCredentials,
    OfflineStream,
    StreamNotFound,
    StreamsError,
)
from . import thetatypes as _thetatypes

import re
import logging
import asyncio
import aiohttp
import contextlib
from datetime import datetime
from collections import defaultdict
from typing import Optional, List, Tuple, Union

_ = Translator("Streams", __file__)
log = logging.getLogger("red.core.cogs.Theta")


@cog_i18n(_)
class Theta(commands.Cog):

    global_defaults = {"refresh_timer": 200, "tokens": {}, "streams": []}

    guild_defaults = {
        "autodelete": False,
        "mention_everyone": True,
        "mention_here": False,
        "live_message_mention": True,
        "live_message_nomention": False,
        "ignore_reruns": False,
    }

    role_defaults = {"mention": False}

    def __init__(self, bot: Red):
        super().__init__()
        self.db: Config = Config.get_conf(self, 26262626)
        self.ttv_bearer_cache: dict = {}
        self.db.register_global(**self.global_defaults)
        self.db.register_guild(**self.guild_defaults)
        self.db.register_role(**self.role_defaults)

        self.bot: Red = bot

        self.theta: List[Theta] = []
        self.task: Optional[asyncio.Task] = None

        self._ready_event: asyncio.Event = asyncio.Event()
        self._init_task: asyncio.Task = self.bot.loop.create_task(self.initialize())

    def check_name_or_id(self, data: str) -> bool:
        matched = self.yt_cid_pattern.fullmatch(data)
        if matched is None:
            return True
        return False

    async def initialize(self) -> None:
        """Should be called straight after cog instantiation."""
        await self.bot.wait_until_ready()

        try:
            await self.move_api_keys()
            await self.get_theta_bearer_token()
            self.theta = await self.load_theta()
            self.task = self.bot.loop.create_task(self._theta_alerts())
        except Exception as error:
            log.exception("Failed to initialize Theta cog:", exc_info=error)

        self._ready_event.set()

    async def cog_before_invoke(self, ctx: commands.Context):
        await self._ready_event.wait()

    async def move_api_keys(self) -> None:
        """Move the API keys from cog stored config to core bot config if they exist."""
        tokens = await self.db.tokens()
        theta = await self.bot.get_shared_api_tokens("theta")
        for token_type, token in tokens.items():
            if token_type == "ThetaStream" and "api_key" and "client_id" and "access_token" not in theta:
                # Don't need to check Community since they're set the same
                await self.bot.set_shared_api_tokens("theta", client_id=token)
        await self.db.tokens.clear()

    async def get_theta_bearer_token(self) -> None:
        tokens = await self.bot.get_shared_api_tokens("theta")
        if tokens.get("client_id"):
            try:
             if tokens.get("client_secret"):
              try:
               tokens["access_token"]
            except KeyError:
                message = _(
                    "You need a client secret key to use correctly Theta API on this cog.\n"
                    "Follow these steps:\n"
                    "1. Go to this page: https://discord.gg/as8hUeA.\n"
                    '2. Contact Ghostie in Theta Discord.\n'
                    "3. Copy your client ID and your client secret into:\n"
                    "`[p]set api theta client_id <your_client_id_here> "
                    "client_secret <your_client_secret_here>`\n\n"
                    "Note: These tokens are sensitive and should only be used in a private channel "
                    "or in DM with the bot."
                )
                await send_to_owners_with_prefix_replaced(self.bot, message)
        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.theta.tv/v1/oauth/token",
                params={
                    "client_id": tokens.get("client_id", ""),
                    "client_secret": tokens.get("client_secret", ""),
                    "access_token": tokens.get("access_token", ""),
                    "grant_type": "access_token",
                },
            ) as req:
                try:
                    data = await req.json()
                except aiohttp.ContentTypeError:
                    data = {}

                if req.status == 200:
                    pass
                elif req.status == 400 and data.get("message") == "invalid client":
                    log.error(
                        "Theta API request failed authentication: set Client ID is invalid."
                    )
                elif req.status == 403 and data.get("message") == "invalid client secret":
                    log.error(
                        "Theta API request failed authentication: set Client Secret is invalid."
                    )
                elif "message" in data:
                    log.error(
                        "Theta OAuth2 API request failed with status code %s"
                        " and error message: %s",
                        req.status,
                        data["message"],
                    )
                else:
                    log.error("Theta OAuth2 API request failed with status code %s", req.status)

                if req.status != 200:
                    return

        self.ttv_bearer_cache = data
        self.ttv_bearer_cache["expires_at"] = datetime.now().timestamp() + data.get("expires_in")

    async def maybe_renew_theta_bearer_token(self) -> None:
        if self.ttv_bearer_cache:
            if self.ttv_bearer_cache["expires_at"] - datetime.now().timestamp() <= 60:
                await self.get_theta_bearer_token()

    @commands.command()
    async def thetastream(self, ctx: commands.Context, channel_name: str):
        """Check if a Theta channel is live."""
        await self.maybe_renew_theta_bearer_token()
        token = (await self.bot.get_shared_api_tokens("theta")).get("client_id").get("access_token"),
        theta = ThetaStream(
            name=channel_name, token=token, bearer=self.ttv_bearer_cache.get("access_token", None),
        )
        await self.check_online(ctx, theta)

    async def check_online(
        self,
        ctx: commands.Context,
        stream: Union[ThetaStream],
    ):
        try:
            info = await stream.is_online()
        except OfflineStream:
            await ctx.send(_("That user is offline."))
        except StreamNotFound:
            await ctx.send(_("That channel doesn't seem to exist."))
        except InvalidThetaCredentials:
            await ctx.send(
                _(
                    "The Theta token is either invalid or has not been set. See "
                    "`{prefix}streamset thetatoken`."
                ).format(prefix=ctx.clean_prefix)
            )
        except APIError:
            await ctx.send(
                _("Something went wrong while trying to contact the stream service's API.")
            )
        else:
            if isinstance(info, tuple):
                embed, is_rerun = info
                ignore_reruns = await self.db.guild(ctx.channel.guild).ignore_reruns()
                if ignore_reruns and is_rerun:
                    await ctx.send(_("That user is offline."))
                    return
            else:
                embed = info
            await ctx.send(embed=embed)

    @commands.group()
    @commands.guild_only()
    @checks.mod()
    async def thetaalert(self, ctx: commands.Context):
        """Manage automated theta alerts."""
        pass


    @thetaalert.group(name="theta", invoke_without_command=True)
    async def _theta(self, ctx: commands.Context, channel_name: str = None):
        """Manage Theta stream notifications."""
        if channel_name is not None:
            await ctx.invoke(self.theta_alert_channel, channel_name)
        else:
            await ctx.send_help()

    @_theta.command(name="channel")
    async def theta_alert_channel(self, ctx: commands.Context, channel_name: str):
        """Toggle alerts in this channel for a Theta stream."""
        if re.fullmatch(r"<#\d+>", channel_name):
            await ctx.send(
                _("Please supply the name of a *Theta* channel, not a Discord channel.")
            )
            return
        await self.theta_alert(ctx, ThetaStream, channel_name.lower())

    @thetaalert.command(name="thetaalert")
    async def theta_alert(self, ctx: commands.Context, channel_name_or_id: str):
        """Toggle alerts in this channel for a Theta stream."""
        await self.theta_alert(ctx, ThetaStream, channel_name_or_id)

    @thetaalert.command(name="quit", usage="[disable_all=No]")
    async def thetaalert_quit(self, ctx: commands.Context, _all: bool = False):
        """Disable all Theta stream alerts in this channel or server.
        `[p]thetaalert quit` will disable this channel's stream
        alerts.
        Do `[p]thetaalert quit yes` to disable all stream alerts in
        this server.
        """
        streams = self.theta.copy()
        local_channel_ids = [c.id for c in ctx.guild.channels]
        to_remove = []

        for theta in theta:
            for channel_id in theta.channels:
                if channel_id == ctx.channel.id:
                    theta.channels.remove(channel_id)
                elif _all and ctx.channel.id in local_channel_ids:
                    if channel_id in theta.channels:
                        stream.channels.remove(channel_id)

            if not theta.channels:
                to_remove.append(stream)

        for theta in to_remove:
            theta.remove(stream)

        self.theta = theta
        await self.save_theta()

        if _all:
            msg = _("All the stream alerts in this server have been disabled.")
        else:
            msg = _("All the stream alerts in this channel have been disabled.")

        await ctx.send(msg)

    @thetaalert.command(name="stop", usage="[disable_all=No]")
    async def thetaalert_stop(self, ctx: commands.Context, _all: bool = False):
        """Disable all stream alerts in this channel or server.
        `[p]thetaalert stop` will disable this channel's stream
        alerts.
        Do `[p]thetaalert stop yes` to disable all stream alerts in
        this server.
        """
        theta = self.theta.copy()
        local_channel_ids = [c.id for c in ctx.guild.channels]
        to_remove = []

        for theta in theta:
            for channel_id in theta.channels:
                if channel_id == ctx.channel.id:
                    theta.channels.remove(channel_id)
                elif _all and ctx.channel.id in local_channel_ids:
                    if channel_id in theta.channels:
                        theta.channels.remove(channel_id)

            if not theta.channels:
                to_remove.append(theta)

        for theta in to_remove:
            theta.remove(stream)

        self.theta = theta
        await self.save_theta()

        if _all:
            msg = _("All the stream alerts in this server have been disabled.")
        else:
            msg = _("All the stream alerts in this channel have been disabled.")

        await ctx.send(msg)

    @thetaalert.command(name="list")
    async def thetaalert_list(self, ctx: commands.Context):
        """List all active stream alerts in this server."""
        theta_list = defaultdict(list)
        guild_channels_ids = [c.id for c in ctx.guild.channels]
        msg = _("Active alerts:\n\n")

        for theta in self.theta:
            for channel_id in theta.channels:
                if channel_id in guild_channels_ids:
                    theta_list[channel_id].append(theta.name.lower())

        if not theta_list:
            await ctx.send(_("There are no active alerts in this server."))
            return

        for channel_id, theta in theta_list.items():
            channel = ctx.guild.get_channel(channel_id)
            msg += "** - #{}**\n{}\n".format(channel, ", ".join(theta))

        for page in pagify(msg):
            await ctx.send(page)

    async def theta_alert(self, ctx: commands.Context, _class, channel_name):
        theta = self.get_theta(_class, channel_name)
        if not theta:
            token = await self.bot.get_shared_api_tokens(_class.token_name)
            is_theta = _class.__name__ == "ThetaStream"
            if is_theta and not self.check_name_or_id(channel_name):
                theta = _class(id=channel_name, token=token)
            elif is_theta:
                await self.maybe_renew_theta_bearer_token()
                theta = _class(
                    name=channel_name,
                    token=token.get("client_id"),
                    bearer=self.ttv_bearer_cache.get("access_token", None),
                )
            else:
                theta = _class(name=channel_name, token=token)
            try:
                exists = await self.check_exists(stream)
            except InvalidThetaCredentials:
                await ctx.send(
                    _(
                        "The Thetatoken is either invalid or has not been set. See "
                        "`{prefix}thetaset thetatoken`."
                    ).format(prefix=ctx.clean_prefix)
                )
                return
            except APIError:
                await ctx.send(
                    _("Something went wrong while trying to contact the stream service's API.")
                )
                return
            else:
                if not exists:
                    await ctx.send(_("That channel doesn't seem to exist."))
                    return

        await self.add_or_remove(ctx, stream)

    @commands.group()
    @checks.mod()
    async def thetaset(self, ctx: commands.Context):
        """Set tokens for accessing streams."""
        pass

    @thetaset.command(name="timer")
    @checks.is_owner()
    async def _thetaset_refresh_timer(self, ctx: commands.Context, refresh_time: int):
        """Set theta check refresh time."""
        if refresh_time < 60:
            return await ctx.send(_("You cannot set the refresh timer to less than 60 seconds"))

        await self.db.refresh_timer.set(refresh_time)
        await ctx.send(
            _("Refresh timer set to {refresh_time} seconds".format(refresh_time=refresh_time))
        )

    @thetaset.command()
    @checks.is_owner()
    async def thetatoken(self, ctx: commands.Context):
        """Explain how to set the theta token."""

        message = _(
            "You need a client secret key to use correctly Theta API on this cog.\n"
            "Follow these steps:\n"
            "1. Go to this page: https://discord.gg/as8hUeA.\n"
            '2. Contact Ghostie in Theta Discord.\n'
            "3. Copy your client ID and your client secret into:\n"
            "`[p]set api theta client_id <your_client_id_here> "
            "client_secret <your_client_secret_here>`\n\n"
            "Note: These tokens are sensitive and should only be used in a private channel "
            "or in DM with the bot."
        ).format(prefix=ctx.clean_prefix)

        await ctx.maybe_send_embed(message)

    @thetaset.group()
    @commands.guild_only()
    async def message(self, ctx: commands.Context):
        """Manage custom message for theta alerts."""
        pass

    @message.command(name="mention")
    @commands.guild_only()
    async def with_mention(self, ctx: commands.Context, message: str = None):
        """Set Theta alert message when mentions are enabled.
        Use `{mention}` in the message to insert the selected mentions.
        Use `{theta.name}` in the message to insert the channel or user name.
        For example: `[p]thetaset message mention "{mention}, {theta.name} is live!"`
        """
        if message is not None:
            guild = ctx.guild
            await self.db.guild(guild).live_message_mention.set(message)
            await ctx.send(_("Theta alert message set!"))
        else:
            await ctx.send_help()

    @message.command(name="nomention")
    @commands.guild_only()
    async def without_mention(self, ctx: commands.Context, message: str = None):
        """Set Theta alert message when mentions are disabled.
        Use `{theta.name}` in the message to insert the channel or user name.
        For example: `[p]thetaset message nomention "{theta.name} is live!"`
        """
        if message is not None:
            guild = ctx.guild
            await self.db.guild(guild).live_message_nomention.set(message)
            await ctx.send(_("Theta alert message set!"))
        else:
            await ctx.send_help()

    @message.command(name="clear")
    @commands.guild_only()
    async def clear_message(self, ctx: commands.Context):
        """Reset the theta alert messages in this server."""
        guild = ctx.guild
        await self.db.guild(guild).live_message_mention.set(False)
        await self.db.guild(guild).live_message_nomention.set(False)
        await ctx.send(_("Theta alerts in this server will now use the default alert message."))

    @thetaset.group()
    @commands.guild_only()
    async def mention(self, ctx: commands.Context):
        """Manage mention settings for Theta alerts."""
        pass

    @mention.command(aliases=["everyone"])
    @commands.guild_only()
    async def all(self, ctx: commands.Context):
        """Toggle the `@\u200beveryone` mention."""
        guild = ctx.guild
        current_setting = await self.db.guild(guild).mention_everyone()
        if current_setting:
            await self.db.guild(guild).mention_everyone.set(False)
            await ctx.send(_("`@\u200beveryone` will no longer be mentioned for stream alerts."))
        else:
            await self.db.guild(guild).mention_everyone.set(True)
            await ctx.send(_("When a stream is live, `@\u200beveryone` will be mentioned."))

    @mention.command(aliases=["here"])
    @commands.guild_only()
    async def online(self, ctx: commands.Context):
        """Toggle the `@\u200bhere` mention."""
        guild = ctx.guild
        current_setting = await self.db.guild(guild).mention_here()
        if current_setting:
            await self.db.guild(guild).mention_here.set(False)
            await ctx.send(_("`@\u200bhere` will no longer be mentioned for stream alerts."))
        else:
            await self.db.guild(guild).mention_here.set(True)
            await ctx.send(_("When a stream is live, `@\u200bhere` will be mentioned."))

    @mention.command()
    @commands.guild_only()
    async def role(self, ctx: commands.Context, *, role: discord.Role):
        """Toggle a role mention."""
        current_setting = await self.db.role(role).mention()
        if current_setting:
            await self.db.role(role).mention.set(False)
            await ctx.send(
                _("`@\u200b{role.name}` will no longer be mentioned for stream alerts.").format(
                    role=role
                )
            )
        else:
            await self.db.role(role).mention.set(True)
            msg = _(
                "When a Theta stream is live, `@\u200b{role.name}` will be mentioned."
            ).format(role=role)
            if not role.mentionable:
                msg += " " + _(
                    "Since the role is not mentionable, it will be momentarily made mentionable "
                    "when announcing a streamalert. Please make sure I have the correct "
                    "permissions to manage this role, or else members of this role won't receive "
                    "a notification."
                )
            await ctx.send(msg)

    @thetaset.command()
    @commands.guild_only()
    async def autodelete(self, ctx: commands.Context, on_off: bool):
        """Toggle alert deletion for when streams go offline."""
        await self.db.guild(ctx.guild).autodelete.set(on_off)
        if on_off:
            await ctx.send(_("The notifications will be deleted once Theta streams go offline."))
        else:
            await ctx.send(_("Notifications will no longer be deleted."))

    @thetaset.command(name="ignorereruns")
    @commands.guild_only()
    async def ignore_reruns(self, ctx: commands.Context):
        """Toggle excluding rerun Theta streams from alerts."""
        guild = ctx.guild
        current_setting = await self.db.guild(guild).ignore_reruns()
        if current_setting:
            await self.db.guild(guild).ignore_reruns.set(False)
            await ctx.send(_("Theta Streams of type 'rerun' will be included in alerts."))
        else:
            await self.db.guild(guild).ignore_reruns.set(True)
            await ctx.send(_("Theta Streams of type 'rerun' will no longer send an alert."))

    async def add_or_remove(self, ctx: commands.Context, stream):
        if ctx.channel.id not in theta.channels:
            theta.channels.append(ctx.channel.id)
            if theta not in self.theta:
                self.theta.append(stream)
            await ctx.send(
                _(
                    "I'll now send a notification in this channel when {theta.name} is live."
                ).format(theta=theta)
            )
        else:
            theta.channels.remove(ctx.channel.id)
            if not theta.channels:
                self.theta.remove(stream)
            await ctx.send(
                _(
                    "I won't send notifications about {theta.name} in this channel anymore."
                ).format(theta=theta)
            )

        await self.save_theta()

    def get_theta(self, _class, name):
        for theta in self.theta:
            # if isinstance(theta, _class) and theta.name == name:
            #    return theta stream
            # Reloading this cog causes an issue with this check ^
            # isinstance will always return False
            # As a workaround, we'll compare the class' name instead.
            # Good enough.
            if _class.__name__ == "ThetaStream" and theta.type == _class.__name__:
                # Because name could be a username or a channel id
                if self.check_name_or_id(name) and theta.name.lower() == name.lower():
                    return theta
                elif not self.check_name_or_id(name) and theta.id == name:
                    return theta
            elif theta.type == _class.__name__ and theta.name.lower() == name.lower():
                return theta

    @staticmethod
    async def check_exists(theta):
        try:
            await theta.is_online()
        except OfflineStream:
            pass
        except StreamNotFound:
            return False
        except StreamsError:
            raise
        return True

    async def _theta_alerts(self):
        await self.bot.wait_until_ready()
        while True:
            try:
                await self.check_theta()
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(await self.db.refresh_timer())

    async def check_theta(self):
        for theta in self.theta:
            with contextlib.suppress(Exception):
                try:
                    if theta.__class__.__name__ == "ThetaStream":
                        await self.maybe_renew_theta_bearer_token()
                        embed, is_rerun = await theta.is_online()
                    else:
                        embed = await theta.is_online()
                        is_rerun = False
                except OfflineStream:
                    if not theta._messages_cache:
                        continue
                    for message in theta._messages_cache:
                        with contextlib.suppress(Exception):
                            autodelete = await self.db.guild(message.guild).autodelete()
                            if autodelete:
                                await message.delete()
                    theta._messages_cache.clear()
                    await self.save_theta()
                else:
                    if theta._messages_cache:
                        continue
                    for channel_id in theta.channels:
                        channel = self.bot.get_channel(channel_id)
                        if not channel:
                            continue
                        ignore_reruns = await self.db.guild(channel.guild).ignore_reruns()
                        if ignore_reruns and is_rerun:
                            continue
                        mention_str, edited_roles = await self._get_mention_str(channel.guild)

                        if mention_str:
                            alert_msg = await self.db.guild(channel.guild).live_message_mention()
                            if alert_msg:
                                content = alert_msg.format(mention=mention_str, theta=theta)
                            else:
                                content = _("{mention}, {theta} is now live!").format(
                                    mention=mention_str,
                                    theta=escape(
                                        str(theta.name), mass_mentions=True, formatting=True
                                    ),
                                )
                        else:
                            alert_msg = await self.db.guild(channel.guild).live_message_nomention()
                            if alert_msg:
                                content = alert_msg.format(theta=theta)
                            else:
                                content = _("{theta} is now live!").format(
                                    theta=escape(
                                        str(theta.name), mass_mentions=True, formatting=True
                                    )
                                )

                        m = await channel.send(content, embed=embed)
                        theta._messages_cache.append(m)
                        if edited_roles:
                            for role in edited_roles:
                                await role.edit(mentionable=False)
                        await self.save_theta()

    async def _get_mention_str(self, guild: discord.Guild) -> Tuple[str, List[discord.Role]]:
        """Returns a 2-tuple with the string containing the mentions, and a list of
        all roles which need to have their `mentionable` property set back to False.
        """
        settings = self.db.guild(guild)
        mentions = []
        edited_roles = []
        if await settings.mention_everyone():
            mentions.append("@everyone")
        if await settings.mention_here():
            mentions.append("@here")
        can_manage_roles = guild.me.guild_permissions.manage_roles
        for role in guild.roles:
            if await self.db.role(role).mention():
                if can_manage_roles and not role.mentionable:
                    try:
                        await role.edit(mentionable=True)
                    except discord.Forbidden:
                        # Might still be unable to edit role based on hierarchy
                        pass
                    else:
                        edited_roles.append(role)
                mentions.append(role.mention)
        return " ".join(mentions), edited_roles

    async def filter_theta(self, streams: list, channel: discord.TextChannel) -> list:
        filtered = []
        for theta in theta:
            th_id = str(theta["channel"]["_id"])
            for alert in self.theta:
                if isinstance(alert, ThetaStream) and alert.id == th_id:
                    if channel.id in alert.channels:
                        break
            else:
                filtered.append(theta)
        return filtered

    async def load_theta(self):
        theta = []
        for raw_theta in await self.db.theta():
            _class = getattr(_thetatypes, raw_theta["type"], None)
            if not _class:
                continue
            raw_msg_cache = raw_theta["messages"]
            raw_theta["_messages_cache"] = []
            for raw_msg in raw_msg_cache:
                chn = self.bot.get_channel(raw_msg["channel"])
                if chn is not None:
                    try:
                        msg = await chn.fetch_message(raw_msg["message"])
                    except discord.HTTPException:
                        pass
                    else:
                        raw_theta["_messages_cache"].append(msg)
            token = await self.bot.get_shared_api_tokens(_class.token_name)
            if token:
                if _class.__name__ == "ThetaStream":
                    raw_theta["token"] = token.get("client_id")
                    raw_theta["bearer"] = self.ttv_bearer_cache.get("access_token", None)
                else:
                    raw_theta["token"] = token
            theta.append(_class(**raw_theta))

        return theta

    async def save_theta(self):
        raw_theta = []
        for theta in self.theta:
            raw_theta.append(theta.export())

        await self.db.theta.set(raw_theta)

    def cog_unload(self):
        if self.task:
            self.task.cancel()

    __del__ = cog_unload
