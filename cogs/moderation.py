import discord
from discord.ext import commands

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.hybrid_command(name="addrole", description="Assign a specific role to a server member.")
    @commands.has_permissions(manage_roles=True)
    @discord.app_commands.describe(
        member="The user you want to give the role to.",
        role="The role to assign to the user."
    )
    async def addrole(self, ctx, member: discord.Member, role: discord.Role):
        await ctx.defer()
        
        if ctx.author.top_role <= role:
            await ctx.send("🚫 You cannot give a role higher than or equal to your own.")
            return

        if ctx.guild.me.top_role <= role:
            await ctx.send("🚫 I cannot give this role (it is higher than my highest role).")
            return

        if role in member.roles:
            await ctx.send(f"⚠️ {member.mention} already has the role {role.mention}.")
            return

        await member.add_roles(role)
        await ctx.send(f"✅ Added {role.mention} to {member.mention}.")

    @commands.hybrid_command(name="removerole", description="Remove a specific role from a server member.")
    @commands.has_permissions(manage_roles=True)
    @discord.app_commands.describe(
        member="The user you want to remove the role from.",
        role="The role you wish to remove."
    )
    async def removerole(self, ctx, member: discord.Member, role: discord.Role):
        await ctx.defer()
        
        if ctx.author.top_role <= role:
            await ctx.send("🚫 You cannot remove a role higher than or equal to your own.")
            return

        if role not in member.roles:
            await ctx.send(f"⚠️ {member.mention} does not have the role {role.mention}.")
            return

        await member.remove_roles(role)
        await ctx.send(f"🗑️ Removed {role.mention} from {member.mention}.")

    @commands.hybrid_command(name="roleedit", description="Modify role settings (Color, Hoist, Mentionable).")
    @commands.has_permissions(manage_roles=True)
    @discord.app_commands.describe(
        role="The role you want to edit.",
        action="The setting to change (Mentionable, Hoist, or Color).",
        value="The new value: 'True/False' for toggles, or a Hex Code (e.g., FF0000) for color."
    )
    @discord.app_commands.choices(action=[
        discord.app_commands.Choice(name="Toggle Mentionable", value="mention"),
        discord.app_commands.Choice(name="Toggle Hoist (Display Separate)", value="hoist"),
        discord.app_commands.Choice(name="Change Color", value="color")
    ])
    async def roleedit(self, ctx, role: discord.Role, action: str, value: str):
        """
        Edit a role's properties.
        Usage: /roleedit @Member mention true
        """
        await ctx.defer()

        if ctx.guild.me.top_role <= role:
            await ctx.send("🚫 I cannot edit this role (it is above me).")
            return

        try:
            if action == "mention":
                is_mentionable = value.lower() in ['true', 'yes', 'on', '1']
                await role.edit(mentionable=is_mentionable)
                await ctx.send(f"✅ Set **{role.name}** mentionable to: `{is_mentionable}`")
            
            elif action == "hoist":
                is_hoist = value.lower() in ['true', 'yes', 'on', '1']
                await role.edit(hoist=is_hoist)
                await ctx.send(f"✅ Set **{role.name}** to display separately: `{is_hoist}`")

            elif action == "color":
                hex_code = value.replace("#", "")
                try:
                    new_color = discord.Color(int(hex_code, 16))
                    await role.edit(color=new_color)
                    await ctx.send(f"✅ Changed **{role.name}** color to `#{hex_code}`")
                except ValueError:
                    await ctx.send("❌ Invalid Hex Color code (e.g., use FF0000 for red).")

        except Exception as e:
            await ctx.send(f"❌ Error editing role: {e}")

    @commands.hybrid_command(name="roleinfo", description="View all roles assigned to a specific user.")
    @discord.app_commands.describe(
        member="The user whose roles you want to see (leave empty to view your own)."
    )
    async def roleinfo(self, ctx, member: discord.Member = None):
        """
        Displays a list of roles for a user.
        Usage: /roleinfo @Username
        """
        member = member or ctx.author
        
        # Get roles (exclude @everyone which is always index 0)
        roles = [role.mention for role in member.roles if role != ctx.guild.default_role]
        roles.reverse() # Show highest roles first
        
        if not roles:
            role_list = "No roles."
        else:
            role_list = ", ".join(roles)

        embed = discord.Embed(title=f"🎭 Roles for {member.display_name}", description=role_list, color=member.color)
        embed.set_footer(text=f"Total: {len(roles)} Roles")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(Moderation(bot))
