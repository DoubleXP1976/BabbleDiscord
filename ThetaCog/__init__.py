from .theta import Theta


def setup(bot):
    cog = Theta(bot)
    bot.add_cog(cog)
