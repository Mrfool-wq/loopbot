<div align="center">

# 🔁 LoopBot

**An all-in-one Discord bot for community servers:** moderation, self-roles, giveaways, staff applications, invite tracking, YouTube alerts, server backups and more, all set up from one `/setup` panel.

![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![discord.py 2.x](https://img.shields.io/badge/discord.py-2.x-5865F2)

</div>

---

## ✨ What can LoopBot do?

| | Feature | In short |
|---|---|---|
| 🛡️ | **Moderation** | Ban, unban, mute, purge, nicknames, role tools and a mod log channel. Every action checks role hierarchy. |
| 🎭 | **Self Roles** | Members pick their own roles from a dropdown menu or buttons on a panel you design. |
| 🎁 | **Giveaways** | Timed giveaways with multiple winners, early end and reroll. They survive bot restarts. |
| 📝 | **Staff Applications** | `/apply` opens a form and a private ticket. Staff accept or reject with buttons, and a transcript is saved. |
| 📨 | **Invite Tracking** | See who invited whom, per-member invite counts, and a log of joins and leaves. |
| 📺 | **YouTube Alerts** | Post a message in a channel the moment a YouTube channel uploads. |
| 💾 | **Server Backups** | Save roles, channels, permissions and emojis. Restoring needs a second admin to confirm. |
| 📢 | **Announcements** | Post as the bot, or schedule a message to repeat every few minutes, hours, days or weeks. |
| 😀 | **Auto Reactions** | React with an emoji whenever a chosen word is said. |
| 💤 | **AFK + Fun** | AFK status, polls, coin flips, chat topics and avatars. |
| ⚙️ | **Easy setup** | One `/setup` panel for the staff role, log channels and prefix. |

Almost every command works **both as a slash command** (`/ban`) **and with a prefix** (`!ban`). The default prefix is `!` and each server can change it.

---

## 🚀 Getting started (server admins)

1. **Invite the bot** to your server (see [Invite link](#-invite-link)).
2. **Move the bot's role up** in *Server Settings → Roles*. It can only manage roles that are **below** its own role.
3. Type **`/setup`** and choose your **staff role** and **log channels**.
4. Type **`/help`** to see every command, with its arguments and the permission it needs.

That's it. Each feature below only needs a command or two to get going.

---

## 📖 How it works

**🎭 Self roles.** Create a panel, then add roles to it:
```
/selfrole create channel:#self-roles title:Get your role style:buttons
/selfrole add panel:1 role:@Event Ping emoji:💎
```
Clicking a role toggles it on or off. Roles with powerful permissions (Administrator, Manage Roles, Ban Members and similar) can never be handed out this way, even by mistake.

**🎁 Giveaways.** `/gstart 1h 2 Nitro` starts a one-hour giveaway with 2 winners. Members join by reacting. Use `/gend` to finish early and `/greroll` to pick someone new.

**📝 Applications.** Members run `/apply` and fill in a short form. A private channel is created for the applicant and your staff role. Staff use the **Accept / Reject / Close** buttons. When a ticket closes, a text transcript goes to your log channel and to the applicant by DM. Pick the staff role and log channel in `/setup`.

**📨 Invite tracking.** The bot remembers which invite each member used. `/invites` shows your count, and a join/leave log (set in `/setup`) shows who invited every new member. Someone who leaves within 10 minutes counts as a "fake" invite.

**💾 Backups.** `/backup-create` saves your server layout. `/backup-load` **wipes the server and rebuilds it** from a backup, so it needs a **second administrator** to confirm. Members' roles and message history are not restored.

**📺 YouTube alerts.** `/youtube add` connects a YouTube channel to a Discord channel. You can customise the message with `{author}`, `{title}` and `{link}`.

---

## 📋 Commands

49 commands. In Discord, `/help` shows the same list with a category menu. `<like this>` is required and `[like this]` is optional.

### 🛡️ Moderation

| Command | What it does |
|---|---|
| `/ban <user> [reason]` | Ban a member. Use their ID to ban someone who already left. *(needs Ban Members)* |
| `/unban <user_id> [reason]` | Unban a user using their ID. *(needs Ban Members)* |
| `/mute <member> <duration> [reason]` | Timeout a member. Duration like 10m, 2h or 1d (max 28 days). *(needs Moderate Members)* |
| `/unmute <member> [reason]` | Remove a member's timeout. *(needs Moderate Members)* |
| `/purge <amount>` | Delete the last 1-200 messages in this channel. *(needs Manage Messages)* |
| `/nick <member> [new_nickname]` | Change a member's nickname. Leave the nickname empty to reset it. *(needs Manage Nicknames)* |
| `/addrole <member> <role>` | Give a role to a member. *(needs Manage Roles)* |
| `/removerole <member> <role>` | Take a role away from a member. *(needs Manage Roles)* |
| `/roleedit <role> <action> <value>` | Edit a role. Action: mention or hoist (true/false), or color (hex like FF0000). *(needs Manage Roles)* |
| `/setmodlog <channel>` | Choose the channel where moderation actions are logged. *(needs Administrator)* |

### 🔧 Utility

| Command | What it does |
|---|---|
| `/help` | Show this menu. |
| `/setup` | Open the server settings panel: staff role, log channels and prefix. *(needs Administrator)* |
| `/setprefix <new_prefix>` | Change the bot's prefix (up to 5 characters). *(needs Administrator)* |
| `/ping` | Check the bot's response time. |
| `/serverinfo` | Show information about this server. |
| `/roleinfo [member]` | See every role a member has. |
| `/afk [reason]` | Set yourself as AFK. The bot tells people who mention you, and clears it when you next talk. |

### 🎉 Fun

| Command | What it does |
|---|---|
| `/poll <question>` | Create a 👍 / 👎 poll. |
| `/coinflip` | Flip a coin. |
| `/topic` | Get a random chat topic. |
| `/avatar [member]` | Show a member's avatar in full size. |
| `/mimic <member> <text>` | Send a message that looks like it came from another member. *(needs Manage Webhooks)* |

### 🎁 Giveaways

| Command | What it does |
|---|---|
| `/gstart <duration> <winners> <prize>` | Start a giveaway. Duration like 30s, 10m, 2h or 1d (10 seconds to 30 days), up to 20 winners. *(needs Manage Messages)* |
| `/gend <message_id>` | End a giveaway right now and pick the winners. *(needs Manage Messages)* |
| `/greroll <channel> <message_id>` | Pick a new winner for a finished giveaway. *(needs Manage Messages)* |

### 📢 Announcements

| Command | What it does |
|---|---|
| `/announce <channel> <message> [image]` | Post a message in a channel as the bot, with an optional image. *(needs Manage Messages)* |
| `/autoannounce <channel> <interval> <message>` | Repeat a message automatically. Interval like 30m, 1h, 1d or 1w (minimum 5m). *(needs Manage Messages)* |
| `/stopannouncements <channel>` | Stop every repeating message in a channel. *(needs Manage Messages)* |

### 😀 Auto Reactions

> The bot reacts to whole words. Add * to match word endings: `kill*` also matches killed and killing.

| Command | What it does |
|---|---|
| `/setreact <trigger> <emoji> [channel]` | React with an emoji when a word is said. Leave the channel empty to work everywhere. *(needs Manage Channels)* |
| `/delreact <trigger> [channel]` | Remove an auto-reaction. *(needs Manage Channels)* |
| `/reactlist` | List all auto-reactions in this server. |

### 📨 Invites

| Command | What it does |
|---|---|
| `/invites [member]` | See how many people you (or another member) invited. |
| `/addinvites <member> <amount>` | Add bonus invites to a member. Use a negative number to remove. *(needs Administrator)* |
| `/setinvitelog <channel>` | Choose the channel where joins and leaves are logged. *(needs Administrator)* |

### 📺 Social Alerts

> In the message, use {author}, {title} and {link}.

| Command | What it does |
|---|---|
| `/youtube add <channel_id> <discord_channel> [message]` | Post in a channel when a YouTube channel uploads. The ID starts with UC. *(needs Manage Webhooks)* |
| `/youtube remove <channel_id>` | Stop alerts for a YouTube channel. *(needs Manage Webhooks)* |
| `/youtube list` | Show this server's YouTube alerts. |

### 🎭 Self Roles

> Members pick their own roles from a panel. Use style `menu` (dropdown) or `buttons`.

| Command | What it does |
|---|---|
| `/selfrole create <channel> <title> [description] [image_url] [footer] [color] [style] [exclusive]` | Create a role panel in a channel. *(needs Manage Roles)* |
| `/selfrole add <panel> <role> [emoji] [label] [description]` | Add a role to a panel. *(needs Manage Roles)* |
| `/selfrole remove <panel> <role>` | Remove a role from a panel. *(needs Manage Roles)* |
| `/selfrole edit <panel> [title] [description] [image_url] [footer] [color] [style] [exclusive]` | Change a panel. Type none to clear a text field. *(needs Manage Roles)* |
| `/selfrole list` | Show this server's panels. *(needs Manage Roles)* |
| `/selfrole refresh <panel>` | Redraw a panel, or re-post it if its message was deleted. *(needs Manage Roles)* |
| `/selfrole delete <panel>` | Delete a panel and its message. *(needs Manage Roles)* |

### 💾 Backups

> Slash commands only. Loading or deleting a backup needs a second Administrator to confirm.

| Command | What it does |
|---|---|
| `/backup-create` | Save roles, channels, permissions and emojis (max 5 per server). *(needs Administrator)* |
| `/backup-list` | Show this server's saved backups. *(needs Administrator)* |
| `/backup-load <backup_id>` | Restore a backup. This WIPES the current channels and roles. *(needs Administrator)* |
| `/backup-delete <backup_id>` | Delete a saved backup. *(needs Administrator)* |

### 📝 Applications

> Staff review applications with the buttons in the ticket. Pick the staff role and log channel with /setup.

| Command | What it does |
|---|---|
| `/apply` | Start a staff application. A form opens, then a private channel is created for you. |

---

## 🔗 Invite link

Replace `YOUR_CLIENT_ID` with your application's ID (Discord Developer Portal → your app → *General Information*):

```
https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&scope=bot%20applications.commands&permissions=1101525282036
```

This asks for only what the bot uses: Add Reactions, Manage Channels, Manage Server, View Audit Log, Ban Members, View Channels, Send Messages, Manage Messages, Embed Links, Attach Files, Read Message History, Use External Emojis, Manage Nicknames, Manage Roles, Manage Webhooks, Manage Expressions, Moderate Members.

---

## 🖥️ Self-hosting

Want to run your own copy? You need **Python 3.10 or newer**.

### 1. Create the bot
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) and create an application, then add a **Bot**.
2. Under **Bot → Privileged Gateway Intents**, turn on **Server Members Intent** and **Message Content Intent**.
3. Copy the bot **token** (keep it secret).

### 2. Install
```bash
git clone https://github.com/Mrfool-wq/loopbot.git
cd loopbot
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure
Create a file named `.env` next to `main.py`:
```env
DISCORD_TOKEN=your-bot-token-here
```

### 4. Run
```bash
python main.py
```
The database (`bot_database.db`) is created automatically on first start. Slash commands sync on startup and can take a few minutes to appear. The bot owner can force a sync with `!sync`.

### Optional settings (`.env`)

| Variable | Needed for | Notes |
|---|---|---|
| `DISCORD_TOKEN` | Everything | **Required.** |
| `WEBSUB_CALLBACK_URL` | YouTube alerts | A public `https://` address ending in `/callback` that points to the bot (for example a domain or tunnel). Without it, YouTube alerts stay off. |
| `WEBSUB_PORT` | YouTube alerts | Local port for the webhook server. Default `8080`. |
| `WEBSUB_SECRET` | YouTube alerts | Any long random text used to verify notifications. If empty, one is derived from your bot token. |
| `ERROR_LOG_CHANNEL_ID` | Debugging | A channel ID where unexpected errors are posted. |

### ⚠️ Keep these out of GitHub
Never commit your `.env` file or your database. Make sure `.gitignore` contains:
```
.env
*.db*
__pycache__/
venv/
```

---

## 🗂️ Project structure

```
loopbot/
├── main.py              # Starts the bot, loads every cog, prefix handling
├── database.py          # SQLite setup (tables are created automatically)
├── guild_settings.py    # Per-server settings shared by several cogs
├── requirements.txt
└── cogs/
    ├── moderation.py    # ban, mute, roles, AFK, mod log
    ├── utility.py       # prefix, ping, server info, purge
    ├── setup.py         # the /setup settings panel
    ├── help.py          # the /help menu (command list is written by hand)
    ├── selfroles.py     # self-role panels
    ├── giveaway.py
    ├── applications.py
    ├── invites.py
    ├── socials.py       # YouTube alerts
    ├── backup.py
    ├── announcement.py
    ├── autoreact.py
    ├── fun.py
    └── errorhandler.py  # friendly error messages
```

### Adding your own command
1. Write it in a cog inside `cogs/` (or create a new cog file; every `.py` file in `cogs/` loads automatically).
2. Add one line for it in `cogs/help.py` so it shows up in `/help`.

---

## 🔒 Data and privacy

LoopBot stores its data in a local SQLite file on the machine that runs it. It keeps:

- server settings (prefix, staff role, log channels) and self-role panel setups
- giveaway details, scheduled announcements and YouTube alert subscriptions
- invite counts and which invite each member joined with
- AFK statuses and application records
- server backups (role and channel names, permissions and emoji images)

It reads messages only to run AFK, auto-reactions and commands, and it does **not** store message content. Application transcripts are created in memory and sent to your log channel and the applicant, not saved in the database.

---

## 🤝 Contributing

Bug reports and pull requests are welcome. Open an [issue](https://github.com/Mrfool-wq/loopbot/issues) to report a problem or suggest a feature.

## 📄 License

LoopBot is released under the [MIT License](LICENSE). You are free to use, copy, modify and distribute it, including for commercial use, as long as the license notice is kept.
