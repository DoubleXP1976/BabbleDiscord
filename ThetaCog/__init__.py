from .theta import theta


def setup(bot):
    cog = Theta(bot)
    bot.add_cog(cog)
