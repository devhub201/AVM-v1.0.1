"""Legacy Cloud premium layer for AVM.

* Animated (GIF) custom emojis drawn by the bot itself (lc_icons + lc_icons_extra)
* Every unicode icon in every embed / button / dropdown is swapped for its animated twin
* .emoji install / list      .setlogchannel (VPS creation log)      .leaderboard

Loaded from the bottom of avm_bot.py:  lc_premium.setup(sys.modules[__name__])
Nothing here is required for the bot to run: if this module fails to load, AVM starts without it.
"""

import asyncio
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import lc_icons
import lc_icons_extra  # noqa: F401  (registers the extra icons)

PREFIX = "ax_"          # same prefix as the AxionHost all-in-one bot, so both can share one emoji pack

# unicode icon -> animated icon name
UNI_MAP: Dict[str, str] = {
    "✅": "check", "✔️": "check", "☑️": "check", "🎉": "gift", "🎁": "gift",
    "❌": "cross", "✖️": "cross", "🚫": "cross", "⛔": "cross",
    "⚠️": "warn", "⚠": "warn", "🚨": "warn",
    "🔴": "offline", "🟢": "online", "🟡": "idle", "🟠": "idle",
    "🖥️": "server", "🖥": "server", "💻": "server",
    "📦": "container", "🗃️": "container",
    "📚": "book", "📖": "book", "📋": "clipboard", "📝": "clipboard", "✏️": "clipboard",
    "👤": "user", "👥": "members",
    "🛡️": "shield", "🛡": "shield", "🔒": "lock", "🔐": "lock", "🔑": "key",
    "🔧": "maintenance", "🛠️": "maintenance", "🛠": "maintenance", "⚙️": "gear", "⚙": "gear",
    "🔗": "link", "🔌": "link",
    "📊": "stats", "📈": "stats", "⏳": "hourglass", "⏱️": "hourglass", "⏱": "hourglass", "⌛": "hourglass",
    "🤖": "robot", "⚡": "bolt", "🌩️": "bolt", "🌩": "bolt",
    "🌐": "globe", "🔄": "refresh", "🔁": "refresh", "👑": "crown", "⛏️": "pickaxe", "⛏": "pickaxe",
    "📡": "wifi", "📶": "wifi", "🏓": "wifi",
    "💰": "money", "💵": "money", "💾": "backup", "💽": "disk", "🧠": "cpu", "🧩": "ram", "🎛️": "ram",
    "🚀": "rocket", "🛰️": "node", "⭐": "star", "🔔": "bell", "📢": "megaphone", "🔥": "fire", "💎": "diamond",
}
_UNI_RE = re.compile("|".join(re.escape(k) for k in sorted(UNI_MAP, key=len, reverse=True)))
_CODE_RE = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)
_LEAD_RE = _UNI_RE  # used to peel a leading icon off a button label

LIMITS = {"title": 256, "description": 4096, "name": 256, "value": 1024}
TOTAL_LIMIT = 5800


class EmojiEngine:
    """Turns unicode icons into the guild's animated custom emojis (when installed)."""

    def __init__(self, bot):
        self.bot = bot
        self._cache: Dict[str, Any] = {}
        self._key = -1

    def _table(self) -> Dict[str, Any]:
        n = len(self.bot.emojis)
        if n != self._key:
            self._cache = {e.name: e for e in self.bot.emojis if e.name.startswith(PREFIX)}
            self._key = n
        return self._cache

    def invalidate(self):
        self._key = -1

    def emoji(self, icon: str):
        return self._table().get(PREFIX + icon)

    def installed(self) -> int:
        t = self._table()
        return sum(1 for n in lc_icons.ICONS if PREFIX + n in t)

    def complete(self) -> bool:
        return self.installed() == len(lc_icons.ICONS)

    def text(self, s: Optional[str], limit: Optional[int] = None) -> Optional[str]:
        """Convert unicode icons in s. Falls back to the original if the result would be too long."""
        if not s or not self._table():
            return s
        parts = _CODE_RE.split(s)
        for i in range(0, len(parts), 2):          # even parts are outside code spans
            def repl(m, self=self):
                e = self.emoji(UNI_MAP[m.group(0)])
                return str(e) if e else m.group(0)
            parts[i] = _UNI_RE.sub(repl, parts[i])
        out = "".join(parts)
        if limit and len(out) > limit:
            return s
        return out

    def partial(self, icon_or_unicode: str):
        import discord
        name = UNI_MAP.get(icon_or_unicode, icon_or_unicode)
        e = self.emoji(name)
        if e is None:
            return None
        return discord.PartialEmoji(name=e.name, id=e.id, animated=e.animated)

    def embed_dict(self, d: Dict[str, Any]) -> Dict[str, Any]:
        if not self._table():
            return d
        new = dict(d)
        if new.get("title"):
            new["title"] = self.text(new["title"], LIMITS["title"])
        if new.get("description"):
            new["description"] = self.text(new["description"], LIMITS["description"])
        if new.get("fields"):
            new["fields"] = [dict(f, name=self.text(f.get("name"), LIMITS["name"]) or f.get("name"),
                                  value=self.text(f.get("value"), LIMITS["value"]) or f.get("value"))
                             for f in new["fields"]]
        total = sum(len(str(new.get(k) or "")) for k in ("title", "description"))
        total += sum(len(f.get("name", "")) + len(f.get("value", "")) for f in new.get("fields", []))
        total += len((new.get("footer") or {}).get("text", "")) + len((new.get("author") or {}).get("name", ""))
        if total > TOTAL_LIMIT:                     # would be rejected by Discord: send the plain version
            return d
        return new


def install_patches(m, engine: EmojiEngine):
    """Make every embed, button and dropdown option use the animated emojis automatically."""
    import discord

    if not getattr(discord.Embed, "_lc_patched", False):
        orig_to_dict = discord.Embed.to_dict

        def to_dict(self):
            data = orig_to_dict(self)
            try:
                return engine.embed_dict(data)
            except Exception:
                return data
        discord.Embed.to_dict = to_dict
        discord.Embed._lc_patched = True

    def peel(label, emoji):
        """('🖥️ Manage', None) -> ('Manage', PartialEmoji) when the animated twin is installed."""
        try:
            if emoji is None and isinstance(label, str):
                mm = _LEAD_RE.match(label.lstrip())
                if mm:
                    p = engine.partial(mm.group(0))
                    if p is not None:
                        return label.lstrip()[mm.end():].lstrip() or None, p
            elif isinstance(emoji, str) and emoji in UNI_MAP:
                p = engine.partial(emoji)
                if p is not None:
                    return label, p
        except Exception:
            pass
        return label, emoji

    if not getattr(discord.ui.Button, "_lc_patched", False):
        orig_btn = discord.ui.Button.__init__

        def btn_init(self, *a, **kw):
            if "label" in kw or "emoji" in kw:
                label, emoji = peel(kw.get("label"), kw.get("emoji"))
                if label is None and emoji is None and not kw.get("url") and "sku_id" not in kw:
                    label = kw.get("label")
                kw["label"], kw["emoji"] = label, emoji
            orig_btn(self, *a, **kw)
        discord.ui.Button.__init__ = btn_init
        discord.ui.Button._lc_patched = True

    if not getattr(discord.SelectOption, "_lc_patched", False):
        orig_opt = discord.SelectOption.__init__

        def opt_init(self, *a, **kw):
            if a:
                kw.setdefault("label", a[0])
                a = a[1:]
            label, emoji = peel(kw.get("label"), kw.get("emoji"))
            kw["label"] = label if label else kw.get("label")
            kw["emoji"] = emoji
            orig_opt(self, *a, **kw)
        discord.SelectOption.__init__ = opt_init
        discord.SelectOption._lc_patched = True


# ------------------------------------------------------------------ installing the pack
async def install_pack(bot, guild, overwrite: bool = False, progress=None):
    import discord
    created, skipped, failed = [], [], []
    existing = {e.name: e for e in guild.emojis}
    todo = []
    for name in lc_icons.ICONS:
        full = PREFIX + name
        if full in existing and not overwrite:
            skipped.append(name)
        else:
            todo.append(name)
    animated_used = sum(1 for e in guild.emojis if e.animated)
    free = guild.emoji_limit - animated_used
    if len(todo) > free:
        for name in todo[max(free, 0):]:
            failed.append((name, f"no free animated emoji slot (limit {guild.emoji_limit}); delete some or boost the server"))
        todo = todo[:max(free, 0)]
    rendered = await asyncio.to_thread(lambda: {n: lc_icons.render(n) for n in todo})
    for i, name in enumerate(todo, 1):
        full = PREFIX + name
        try:
            if full in existing:
                await existing[full].delete(reason="Legacy Cloud emoji refresh")
            await guild.create_custom_emoji(name=full, image=rendered[name], reason="Legacy Cloud icon pack")
            created.append(name)
        except discord.Forbidden:
            failed.append((name, "missing 'Manage Expressions' permission"))
            break
        except discord.HTTPException as exc:
            failed.append((name, f"{exc.status}: {str(exc.text)[:120]}"))
            if exc.status == 429:
                await asyncio.sleep(10)
        if progress and i % 8 == 0:
            await progress(i, len(todo))
        await asyncio.sleep(1.3)           # stay under Discord's emoji-create rate limit
    return created, skipped, failed


# ------------------------------------------------------------------ setup
def setup(m):
    """m = the avm_bot module (needs: bot, discord, commands, tasks helpers, settings)."""
    import discord
    from discord.ext import commands

    bot = m.bot
    engine = EmojiEngine(bot)
    bot.lc_emoji = engine
    install_patches(m, engine)
    installing = set()
    ready_done = {"v": False}

    def E(name: str) -> str:
        e = engine.emoji(name)
        return str(e) if e else lc_icons.FALLBACK.get(name, "▫️")

    @bot.listen("on_guild_emojis_update")
    async def lc_emojis_changed(guild, before, after):
        engine.invalidate()

    @bot.listen("on_ready")
    async def lc_on_ready():
        engine.invalidate()
        if ready_done["v"]:
            return
        ready_done["v"] = True
        if str(m._cfg("auto_emojis", "1")).lower() in ("0", "false", "no", "off"):
            return
        guild = bot.get_guild(m.GUILD_ID) if m.GUILD_ID else (bot.guilds[0] if bot.guilds else None)
        if guild is None or engine.complete() or guild.id in installing:
            return
        if not guild.me.guild_permissions.manage_expressions:
            m.logger.warning("Animated emojis: bot lacks 'Manage Expressions' — run .emoji install after granting it")
            return
        installing.add(guild.id)
        try:
            m.logger.info(f"Animated emojis: drawing {len(lc_icons.ICONS)} icons for {guild.name} …")
            created, _, failed = await install_pack(bot, guild)
            m.logger.info(f"Animated emojis: created {len(created)}, failed {len(failed)}")
            for name, why in failed:
                m.logger.warning(f"Animated emoji {name}: {why}")
        except Exception:
            m.logger.exception("Animated emoji auto-install failed")
        finally:
            installing.discard(guild.id)
            engine.invalidate()

    # ---------------------------------------------------------- .emoji
    @bot.group(name="emoji", invoke_without_command=True)
    @m.is_admin()
    async def emoji_group(ctx):
        await ctx.send(embed=m.create_info_embed("Animated Emojis",
            f"`{m.PREFIX}emoji install` — draw + upload the animated icon pack\n"
            f"`{m.PREFIX}emoji install overwrite` — redraw all icons\n"
            f"`{m.PREFIX}emoji list` — show every icon\n\n"
            f"Installed: **{engine.installed()}/{len(lc_icons.ICONS)}**"))

    @emoji_group.command(name="install")
    @m.is_admin()
    async def emoji_install(ctx, mode: str = ""):
        guild = ctx.guild
        if guild is None:
            return await ctx.send(embed=m.create_error_embed("Server Only", "Run this inside your server."))
        if guild.id in installing:
            return await ctx.send(embed=m.create_warning_embed("Already Running", "An install is already in progress."))
        if not guild.me.guild_permissions.manage_expressions:
            return await ctx.send(embed=m.create_error_embed("Missing Permission",
                "Give the bot **Manage Expressions** (or Manage Emojis and Stickers), then run this again."))
        installing.add(guild.id)
        note = await ctx.send(embed=m.create_info_embed("Drawing Icons",
            f"Creating **{len(lc_icons.ICONS)}** animated emojis — about a minute."))
        try:
            created, skipped, failed = await install_pack(bot, guild, overwrite=mode.lower() == "overwrite")
        except Exception as exc:
            installing.discard(guild.id)
            return await ctx.send(embed=m.create_error_embed("Install Failed", f"`{type(exc).__name__}: {exc}`"))
        installing.discard(guild.id)
        engine.invalidate()
        desc = f"Created **{len(created)}** • already there **{len(skipped)}** • failed **{len(failed)}**"
        if failed:
            desc += "\n\n**Problems:**\n" + "\n".join(f"• `{n}` — {w}" for n, w in failed[:8])
        done = (m.create_success_embed if not failed else m.create_warning_embed)("Emoji Pack", desc)
        try:
            await note.edit(embed=done)
        except Exception:
            await ctx.send(embed=done)

    @emoji_group.command(name="list")
    @m.is_admin()
    async def emoji_list(ctx):
        lines = [f"{E(n)} `{n}` — {lc_icons.DESCRIPTIONS.get(n, '')}" for n in lc_icons.ICONS]
        embed = m.create_info_embed("Icon Pack", f"Installed **{engine.installed()}/{len(lc_icons.ICONS)}**\n\n" + "\n".join(lines[:24]))
        embed.add_field(name="More", value="\n".join(lines[24:]) or "—", inline=False)
        await ctx.send(embed=embed)

    # ---------------------------------------------------------- creation log channel
    def log_channel_id() -> int:
        return m._to_int(m.get_setting("creation_log_channel", "0"))

    @bot.command(name="setlogchannel")
    @m.is_admin()
    async def set_log_channel(ctx, channel: discord.TextChannel = None):
        if channel is None:
            cur = log_channel_id()
            return await ctx.send(embed=m.create_info_embed("VPS Creation Log",
                f"Current: {f'<#{cur}>' if cur else '**not set**'}\nUse `{m.PREFIX}setlogchannel #channel` to set it, "
                f"or `{m.PREFIX}setlogchannel off`."))
        gaps = await m._channel_permission_gaps(channel)
        if gaps:
            return await ctx.send(embed=m.create_error_embed("Cannot Use That Channel",
                "The bot is missing: " + ", ".join(gaps)))
        m.set_setting("creation_log_channel", str(channel.id))
        await ctx.send(embed=m.create_success_embed("Creation Log Set",
            f"Every new VPS will be logged in {channel.mention}. Only creations are posted."))
        await post_creation(None, test_channel=channel)

    @bot.command(name="logoff", aliases=["unsetlogchannel"])
    @m.is_admin()
    async def log_off(ctx):
        m.set_setting("creation_log_channel", "0")
        await ctx.send(embed=m.create_success_embed("Creation Log Off", "VPS creation logging is disabled."))

    async def post_creation(info: Optional[Dict[str, Any]], test_channel=None):
        """Called after every successful VPS build. info comes from lc_nodes' deploy wrapper."""
        cid = test_channel.id if test_channel else log_channel_id()
        if not cid:
            return
        try:
            ch = test_channel or bot.get_channel(cid) or await bot.fetch_channel(cid)
            if info is None:
                embed = discord.Embed(title="VPS Creation Log — connected",
                                      description="This is how each new VPS will be reported.", color=0x00ff88)
            else:
                v = info["vps"]
                embed = discord.Embed(title="🚀 New VPS Created", color=0x00ff88,
                                      description=f"<@{info['owner']}> • `{v['container_name']}`")
                embed.add_field(name="📦 Plan", value=f"{v.get('plan_name', 'Custom')}\n{v.get('config', '')}", inline=True)
                embed.add_field(name="🖥️ OS", value=f"`{v.get('os_version', '?')}`", inline=True)
                embed.add_field(name="🛰️ Node", value=f"`{info.get('node', 'local')}` ({info.get('node_type', 'lxc')})", inline=True)
                embed.add_field(name="🎁 Source", value=info.get("source", "—"), inline=True)
                embed.add_field(name="⏱️ Build time", value=f"{info.get('seconds', 0):.1f}s", inline=True)
                embed.add_field(name="👤 Owner ID", value=f"`{info['owner']}`", inline=True)
                steps = info.get("steps") or []
                if steps:
                    lines = [f"{'✅' if ok else '❌'} {label} — {sec:.1f}s" for label, sec, ok in steps[:12]]
                    embed.add_field(name="📋 Build log", value="\n".join(lines)[:1000], inline=False)
            if m.valid_url(m.THUMBNAIL):
                embed.set_thumbnail(url=m.THUMBNAIL)
            embed.set_footer(text=m.FOOTER_TEXT)
            embed.timestamp = discord.utils.utcnow()
            await ch.send(embed=embed)
        except Exception as exc:
            m.logger.error(f"Creation log failed: {type(exc).__name__}: {exc}")

    bot.lc_post_creation = post_creation

    # ---------------------------------------------------------- leaderboard
    @bot.command(name="leaderboard", aliases=["lb", "topinvites"])
    @commands.cooldown(1, 10, commands.BucketType.user)
    async def leaderboard(ctx):
        if not await m.maintenance_check(ctx):
            return
        conn = m.get_db()
        try:
            inv = conn.execute("SELECT user_id, invites FROM user_stats WHERE invites > 0 ORDER BY invites DESC LIMIT 10").fetchall()
            bst = conn.execute("SELECT user_id, boosts FROM user_stats WHERE boosts > 0 ORDER BY boosts DESC LIMIT 5").fetchall()
        finally:
            conn.close()
        medals = ["🥇", "🥈", "🥉"]
        goal = max((p["invites"] for p in m.FREE_VPS_PLANS["invites"]), default=1)
        lines = []
        for i, row in enumerate(inv):
            tag = medals[i] if i < 3 else f"`#{i + 1}`"
            lines.append(f"{tag} <@{row[0]}> — **{row[1]}** invites `{m.make_bar(min(row[1], goal) / goal * 100, 8)}`")
        embed = m.create_info_embed("Invite Leaderboard", "Top inviters earn free VPS plans. See `" + m.PREFIX + "plans`.")
        embed.add_field(name="👑 Top Inviters", value="\n".join(lines) or "No invites recorded yet.", inline=False)
        if bst:
            embed.add_field(name="💎 Top Boosters", value="\n".join(f"<@{r[0]}> — **{r[1]}**" for r in bst), inline=False)
        await ctx.send(embed=embed)

    m.logger.info(f"Legacy Cloud premium layer loaded • icons={len(lc_icons.ICONS)} • emoji mappings={len(UNI_MAP)}")
