import json
import logging
from random import choice
from string import ascii_letters
import xml.etree.ElementTree as ET
from typing import ClassVar, Optional, List

import aiohttp
import discord

from .thetaerrors import (
    APIError,
    OfflineStream,
    InvalidThetaCredentials,
    StreamNotFound,
)
from redbot.core.i18n import Translator
from redbot.core.utils.chat_formatting import humanize_number

THETA_BASE_URL = "https://api.theta.tv/v1"
THETA_ID_ENDPOINT = THETA_BASE_URL + "/user/{{user_id}}"
THETA_STREAMS_ENDPOINT = THETA_BASE_URL + "/theta/live/{{video_id}}"

_ = Translator("Streams", __file__)

log = logging.getLogger("redbot.cogs.Theta")


def rnd(url):
    """Appends a random parameter to the url to avoid Discord's caching"""
    return url + "?rnd=" + "".join([choice(ascii_letters) for _loop_counter in range(6)])


class Theta:

    token_name: ClassVar[Optional[str]] = None

    def __init__(self, **kwargs):
        self.name = kwargs.pop("name", None)
        self.channels = kwargs.pop("channels", [])
        # self.already_online = kwargs.pop("already_online", False)
        self._messages_cache = kwargs.pop("_messages_cache", [])
        self.type = self.__class__.__name__

    async def is_online(self):
        raise NotImplementedError()

    def make_embed(self):
        raise NotImplementedError()

    def export(self):
        data = {}
        for k, v in self.__dict__.items():
            if not k.startswith("_"):
                data[k] = v
        data["messages"] = []
        for m in self._messages_cache:
            data["messages"].append({"channel": m.channel.id, "message": m.id})
        return data

    def __repr__(self):
        return "<{0.__class__.__name__}: {0.name}>".format(self)

class ThetaStream(Theta):

    token_name = "theta"

    def __init__(self, **kwargs):
        self.id = kwargs.pop("id", None)
        self._client_id = kwargs.pop("token", None)
        self._bearer = kwargs.pop("bearer", None)
        super().__init__(**kwargs)

    async def is_online(self):
        if not self.id:
            self.id = await self.fetch_id()

        url = THETA_STREAMS_ENDPOINT
        header = {"client-_id": str(self._client_id)}
        if self._bearer is not None:
            header = {**header, "Authorization": f"Bearer {self._bearer}"}
        params = {"user_id": self.id}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=header, params=params) as r:
                data = await r.json(encoding="utf-8")
        if r.status == 200:
            if not data["data"]:
                raise OfflineStream()
            self.name = data["data"][0]["user_name"]
            data = data["data"][0]
            data["game_name"] = None
            data["followers"] = None
            data["view_count"] = None
            data["profile_image_url"] = None

            game_id = data["game_id"]
            if game_id:
                params = {"id": game_id}
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        "https://api.theta.tv/v1/user", headers=header, params=params
                    ) as r:
                        game_data = await r.json(encoding="utf-8")
                if game_data:
                    game_data = game_data["data"][0]
                    data["game_name"] = game_data["name"]
            params = {"to_id": self.id}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.theta.tv/v1/channel/{{channel_id}}/channel_action", headers=header, params=params
                ) as r:
                    user_data = await r.json(encoding="utf-8")
            if user_data:
                followers = user_data["total"]
                data["followers"] = followers

            params = {"id": self.id}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.theta.tv/v1/user", headers=header, params=params
                ) as r:
                    user_profile_data = await r.json(encoding="utf-8")
            if user_profile_data:
                profile_image_url = user_profile_data["data"][0]["profile_image_url"]
                data["profile_image_url"] = profile_image_url
                data["view_count"] = user_profile_data["data"][0]["view_count"]

            is_rerun = False
            return self.make_embed(data), is_rerun
        elif r.status == 400:
            raise InvalidThetaCredentials()
        elif r.status == 404:
            raise StreamNotFound()
        else:
            raise APIError()

    async def fetch_id(self):
        header = {"client-_id": str(self._client_id)}
        if self._bearer is not None:
            header = {**header, "Authorization": f"Bearer {self._bearer}"}
        url = THETA_ID_ENDPOINT
        params = {"login": self.name}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=header, params=params) as r:
                data = await r.json()

        if r.status == 200:
            if not data["data"]:
                raise StreamNotFound()
            return data["data"][0]["id"]
        elif r.status == 400:
            raise StreamNotFound()
        elif r.status == 401:
            raise InvalidThetaCredentials()
        else:
            raise APIError()

    def make_embed(self, data):
        is_rerun = data["type"] == "rerun"
        url = f"https://www.theta.tv/{data['user_name']}"
        logo = data["profile_image_url"]
        if logo is None:
            logo = "https://user-slivertv.imgix.net/default_profile.jpg?w=56"
        status = data["title"]
        if not status:
            status = _("Untitled broadcast")
        if is_rerun:
            status += _(" - Rerun")
        embed = discord.Embed(title=status, url=url, color=0x6441A4)
        embed.set_author(name=data["user_name"])
        embed.add_field(name=_("Followers"), value=humanize_number(data["followers"]))
        embed.add_field(name=_("Total views"), value=humanize_number(data["view_count"]))
        embed.set_thumbnail(url=logo)
        if data["thumbnail_url"]:
            embed.set_image(url=rnd(data["thumbnail_url"].format(width=320, height=180)))
        if data["game_name"]:
            embed.set_footer(text=_("Playing: ") + data["game_name"])
        return embed

    def __repr__(self):
        return "<{0.__class__.__name__}: {0.name} (ID: {0.id})>".format(self)
