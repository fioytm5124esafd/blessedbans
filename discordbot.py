# -*- coding: utf-8 -*-

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import os
from motor.motor_asyncio import AsyncIOMotorClient


TOKEN = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")

LOG_CHANNEL_ID = 1542216926879809566

ALLOWED_ROLE_IDS = {
    1542202831644131358,
    1542218495280812062,
    1542208806526652526,
}

PROTECTED_ROLE_IDS = set()


intents = discord.Intents.default()
intents.guilds = True
intents.members = True


bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

tree = bot.tree


# ---------------------------------------------------------------------------
# CONEXIÓN A MONGODB
# ---------------------------------------------------------------------------
mongo_client = None
db = None
sanciones_collection = None


async def inicializar_mongodb():
    global mongo_client, db, sanciones_collection

    if not MONGODB_URI:
        raise RuntimeError(
            "No se encontró la variable MONGODB_URI. "
            "Configúrala en Railway."
        )

    mongo_client = AsyncIOMotorClient(MONGODB_URI)
    db = mongo_client["blessed_db"]
    sanciones_collection = db["sanciones"]

    await sanciones_collection.create_index("user_id")

    print("Conectado a MongoDB Atlas correctamente.")


# ---------------------------------------------------------------------------
# OPERACIONES DE BASE DE DATOS
# ---------------------------------------------------------------------------
async def obtener_historial(user_id: int):
    cursor = sanciones_collection.find(
        {"user_id": user_id}
    ).sort("fecha", 1)

    return await cursor.to_list(length=None)


async def guardar_sancion(
    user_id: int,
    username: str,
    razon: str,
    moderador: str,
    moderador_id: int,
    evidencia: str
):
    sancion = {
        "username": username,
        "user_id": user_id,
        "razon": razon,
        "moderador": moderador,
        "moderador_id": moderador_id,
        "evidencia": evidencia,
        "fecha": datetime.now(timezone.utc),
        "estado": "En Blacklist"
    }

    await sanciones_collection.insert_one(sancion)


async def actualizar_ultima_sancion(
    user_id: int,
    estado: str
):
    ultima = await sanciones_collection.find_one(
        {"user_id": user_id, "estado": "En Blacklist"},
        sort=[("fecha", -1)]
    )

    if ultima:
        await sanciones_collection.update_one(
            {"_id": ultima["_id"]},
            {"$set": {"estado": estado}}
        )


# ---------------------------------------------------------------------------
# AUTORIZACIÓN
# ---------------------------------------------------------------------------
def tiene_rol_autorizado(member: discord.Member) -> bool:
    return any(
        role.id in ALLOWED_ROLE_IDS
        for role in member.roles
    )


# ---------------------------------------------------------------------------
# MODAL DE BLACKLIST
# ---------------------------------------------------------------------------
class BanBlessedModal(discord.ui.Modal):

    def __init__(self):
        super().__init__(title="Blacklist Blessed")

        self.user_id_input = discord.ui.TextInput(
            label="ID del usuario",
            placeholder="Ejemplo: 123456789012345678",
            required=True,
            max_length=25
        )

        self.razon_input = discord.ui.TextInput(
            label="Razón",
            placeholder="Indica el motivo de la blacklist...",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=1000
        )

        self.evidencia_input = discord.ui.TextInput(
            label="Evidencia",
            placeholder="URL de imagen, vídeo, mensaje, etc. (opcional)",
            required=False,
            max_length=1000
        )

        self.add_item(self.user_id_input)
        self.add_item(self.razon_input)
        self.add_item(self.evidencia_input)

    async def on_submit(self, interaction: discord.Interaction):

        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "\U0000274C No se pudo comprobar tu usuario.",
                ephemeral=True
            )
            return

        if not tiene_rol_autorizado(interaction.user):
            await interaction.response.send_message(
                "\U0000274C No tienes permiso para utilizar este sistema.",
                ephemeral=True
            )
            return

        try:
            user_id = int(self.user_id_input.value.strip())
        except ValueError:
            await interaction.response.send_message(
                "\U0000274C El ID del usuario no es válido.",
                ephemeral=True
            )
            return

        razon = self.razon_input.value.strip()
        evidencia = self.evidencia_input.value.strip()

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "\U0000274C Este comando solo puede utilizarse dentro del servidor.",
                ephemeral=True
            )
            return

        try:
            miembro = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            miembro = None

        if miembro:
            roles_protegidos = [
                role for role in miembro.roles
                if role.id in PROTECTED_ROLE_IDS
            ]

            if roles_protegidos:
                await interaction.response.send_message(
                    "\U0000274C No puedes blacklistear a este usuario porque tiene un rol protegido.",
                    ephemeral=True
                )
                return

            if guild.me and miembro.top_role >= guild.me.top_role:
                await interaction.response.send_message(
                    "\U0000274C No puedo blacklistear a este usuario porque su rol está por encima o al mismo nivel que el mío.",
                    ephemeral=True
                )
                return

        try:
            await guild.fetch_ban(discord.Object(id=user_id))
            ya_en_blacklist = True
        except discord.NotFound:
            ya_en_blacklist = False
        except discord.HTTPException:
            ya_en_blacklist = False

        try:
            await guild.ban(
                discord.Object(id=user_id),
                reason=(
                    f"Blacklist | {razon} | "
                    f"Moderador: {interaction.user}"
                )
            )
        except discord.NotFound:
            await interaction.response.send_message(
                "\U0000274C No se encontró al usuario.",
                ephemeral=True
            )
            return
        except discord.Forbidden:
            await interaction.response.send_message(
                "\U0000274C No tengo permisos suficientes para blacklistear a este usuario.",
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            if e.code == 40007:
                ya_en_blacklist = True
            else:
                await interaction.response.send_message(
                    f"\U0000274C Discord rechazó la blacklist.\n`{e}`",
                    ephemeral=True
                )
                return

        username = f"Usuario {user_id}"
        avatar_url = None

        if miembro:
            username = str(miembro)
            avatar_url = miembro.display_avatar.url
        else:
            try:
                usuario = await bot.fetch_user(user_id)
                username = str(usuario)
                avatar_url = usuario.display_avatar.url
            except Exception:
                pass

        await guardar_sancion(
            user_id=user_id,
            username=username,
            razon=razon,
            moderador=str(interaction.user),
            moderador_id=interaction.user.id,
            evidencia=evidencia
        )

        titulo = (
            "\U0001F528 Usuario en Blacklist"
            if not ya_en_blacklist
            else "\U0001F528 Blacklist actualizada"
        )

        embed = discord.Embed(
            title=titulo,
            description=(
                f"**{username}** ha sido metido en la Blacklist "
                f"del servidor."
            ),
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(
            name="\U0001F464 Usuario",
            value=f"<@{user_id}>\n`{username}`",
            inline=True
        )
        embed.add_field(
            name="\U0001F194 ID",
            value=f"`{user_id}`",
            inline=True
        )
        embed.add_field(
            name="\U0001F4DD Razón",
            value=razon,
            inline=False
        )
        embed.add_field(
            name="\U0001F6E1\uFE0F Moderador",
            value=interaction.user.mention,
            inline=True
        )
        embed.add_field(
            name="\U0001F4C5 Fecha",
            value=f"<t:{int(datetime.now(timezone.utc).timestamp())}:F>",
            inline=True
        )
        embed.add_field(
            name="\U0001F512 Estado",
            value="Blacklist permanente",
            inline=True
        )
        embed.add_field(
            name="\U0001F4F8 Evidencia",
            value=evidencia if evidencia else "No proporcionada.",
            inline=False
        )

        if avatar_url:
            embed.set_thumbnail(url=avatar_url)

        embed.set_footer(text="Blessed Moderation")

        log_channel = guild.get_channel(LOG_CHANNEL_ID)

        if log_channel:
            await log_channel.send(
                embed=embed,
                view=BanLogView(user_id)
            )

        if miembro:
            try:
                dm_embed = discord.Embed(
                    title="\U0001F6AB Has sido metido en Blacklist",
                    description=f"Has sido añadido a la Blacklist de **{guild.name}**.",
                    color=discord.Color.red()
                )
                dm_embed.add_field(
                    name="\U0001F4DD Razón",
                    value=razon,
                    inline=False
                )
                dm_embed.add_field(
                    name="\U0001F512 Duración",
                    value="Permanente",
                    inline=True
                )
                dm_embed.add_field(
                    name="\U0001F6E1\uFE0F Moderador",
                    value=str(interaction.user),
                    inline=True
                )
                await miembro.send(embed=dm_embed)
            except discord.HTTPException:
                pass

        await interaction.response.send_message(
            f"\U00002705 Usuario `{user_id}` añadido a la Blacklist correctamente.",
            ephemeral=True
        )


# ---------------------------------------------------------------------------
# VISTA DE LOG (con botones)
# ---------------------------------------------------------------------------
class BanLogView(discord.ui.View):

    def __init__(self, user_id: int):
        super().__init__(timeout=None)
        self.user_id = user_id

        historial_button = discord.ui.Button(
            label="Ver historial",
            emoji="\U0001F4CB",
            style=discord.ButtonStyle.secondary,
            custom_id=f"blessed_historial_{user_id}"
        )
        historial_button.callback = self.ver_historial
        self.add_item(historial_button)

        unban_button = discord.ui.Button(
            label="Quitar de Blacklist",
            emoji="\U0001F513",
            style=discord.ButtonStyle.danger,
            custom_id=f"blessed_unban_{user_id}"
        )
        unban_button.callback = self.desbanear
        self.add_item(unban_button)

        perfil_button = discord.ui.Button(
            label="Ver perfil",
            emoji="\U0001F464",
            style=discord.ButtonStyle.link,
            url=f"https://discord.com/users/{user_id}"
        )
        self.add_item(perfil_button)

    async def ver_historial(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "\U0000274C No tienes permiso.",
                ephemeral=True
            )
            return

        if not tiene_rol_autorizado(interaction.user):
            await interaction.response.send_message(
                "\U0000274C No tienes permiso para utilizar este botón.",
                ephemeral=True
            )
            return

        historial = await obtener_historial(self.user_id)

        if not historial:
            await interaction.response.send_message(
                "\U0001F4CB Este usuario no tiene historial de sanciones.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="\U0001F4CB Historial de sanciones",
            description=f"Usuario: <@{self.user_id}>",
            color=discord.Color.blurple()
        )

        ultimas = historial[-10:]

        for numero, sancion in enumerate(reversed(ultimas), start=1):
            fecha = sancion.get("fecha", "Desconocida")

            if isinstance(fecha, datetime):
                fecha_texto = f"<t:{int(fecha.timestamp())}:F>"
            else:
                fecha_texto = str(fecha)

            texto = (
                f"\U0001F4DD **Razón:** {sancion.get('razon', 'Sin razón')}\n"
                f"\U0001F6E1\uFE0F **Moderador:** {sancion.get('moderador', 'Desconocido')}\n"
                f"\U0001F4C5 **Fecha:** {fecha_texto}\n"
                f"\U0001F512 **Estado:** {sancion.get('estado', 'Desconocido')}"
            )

            evidencia = sancion.get("evidencia", "")
            if evidencia:
                texto += f"\n\U0001F4F8 **Evidencia:** {evidencia}"

            embed.add_field(name=f"Registro {numero}", value=texto, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def desbanear(self, interaction: discord.Interaction):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "\U0000274C No tienes permiso.",
                ephemeral=True
            )
            return

        if not tiene_rol_autorizado(interaction.user):
            await interaction.response.send_message(
                "\U0000274C No tienes permiso para utilizar este botón.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"\U000026A0\uFE0F ¿Seguro que quieres quitar de la Blacklist a <@{self.user_id}>?",
            view=ConfirmUnbanView(self.user_id),
            ephemeral=True
        )


# ---------------------------------------------------------------------------
# CONFIRMACIÓN DE QUITAR DE BLACKLIST
# ---------------------------------------------------------------------------
class ConfirmUnbanView(discord.ui.View):

    def __init__(self, user_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id

    @discord.ui.button(
        label="Confirmar",
        emoji="\U0001F513",
        style=discord.ButtonStyle.success
    )
    async def confirmar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(
                "\U0000274C No tienes permiso.",
                ephemeral=True
            )
            return

        if not tiene_rol_autorizado(interaction.user):
            await interaction.response.send_message(
                "\U0000274C No tienes permiso.",
                ephemeral=True
            )
            return

        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "\U0000274C Este botón solo funciona dentro del servidor.",
                ephemeral=True
            )
            return

        try:
            await guild.fetch_ban(discord.Object(id=self.user_id))
        except discord.NotFound:
            await interaction.response.send_message(
                "\U00002705 Este usuario ya no está en la Blacklist.",
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"\U0000274C No pude comprobar la Blacklist.\n`{e}`",
                ephemeral=True
            )
            return

        try:
            await guild.unban(
                discord.Object(id=self.user_id),
                reason=f"Quitado de Blacklist por {interaction.user}"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "\U0000274C No tengo permisos para quitar de la Blacklist.",
                ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"\U0000274C Discord rechazó la acción.\n`{e}`",
                ephemeral=True
            )
            return

        await actualizar_ultima_sancion(self.user_id, "Fuera de Blacklist")

        log_channel = guild.get_channel(LOG_CHANNEL_ID)

        if log_channel:
            embed = discord.Embed(
                title="\U0001F513 Usuario fuera de Blacklist",
                description=f"<@{self.user_id}> ha sido quitado de la Blacklist.",
                color=discord.Color.green(),
                timestamp=datetime.now(timezone.utc)
            )
            embed.add_field(
                name="\U0001F464 Usuario",
                value=f"<@{self.user_id}>",
                inline=True
            )
            embed.add_field(
                name="\U0001F194 ID",
                value=f"`{self.user_id}`",
                inline=True
            )
            embed.add_field(
                name="\U0001F6E1\uFE0F Moderador",
                value=interaction.user.mention,
                inline=False
            )
            embed.set_footer(text="Blessed Moderation")

            await log_channel.send(embed=embed)

        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content=f"\U00002705 <@{self.user_id}> ha sido quitado de la Blacklist correctamente.",
            view=self
        )

    @discord.ui.button(
        label="Cancelar",
        emoji="\U0000274C",
        style=discord.ButtonStyle.secondary
    )
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="\U0000274C Acción cancelada.",
            view=self
        )


# ---------------------------------------------------------------------------
# COMANDOS SLASH
# ---------------------------------------------------------------------------
@tree.command(
    name="bl",
    description="Añade permanentemente a un usuario a la Blacklist de Blessed."
)
@app_commands.guild_only()
async def bl(interaction: discord.Interaction):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "\U0000274C No tienes permiso.",
            ephemeral=True
        )
        return

    if not tiene_rol_autorizado(interaction.user):
        await interaction.response.send_message(
            "\U0000274C No tienes permiso para utilizar este comando.",
            ephemeral=True
        )
        return

    await interaction.response.send_modal(BanBlessedModal())


@tree.command(
    name="unbl",
    description="Quita a un usuario de la Blacklist de Blessed."
)
@app_commands.guild_only()
@app_commands.describe(
    user_id="ID del usuario que quieres quitar de la Blacklist"
)
async def unbl(interaction: discord.Interaction, user_id: str):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "\U0000274C No tienes permiso.",
            ephemeral=True
        )
        return

    if not tiene_rol_autorizado(interaction.user):
        await interaction.response.send_message(
            "\U0000274C No tienes permiso para utilizar este comando.",
            ephemeral=True
        )
        return

    try:
        target_id = int(user_id)
    except ValueError:
        await interaction.response.send_message(
            "\U0000274C El ID no es válido.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"\U000026A0\uFE0F ¿Seguro que quieres quitar de la Blacklist a <@{target_id}>?",
        view=ConfirmUnbanView(target_id),
        ephemeral=True
    )


@tree.command(
    name="historial",
    description="Consulta el historial de sanciones de un usuario."
)
@app_commands.guild_only()
@app_commands.describe(user_id="ID del usuario")
async def historial(interaction: discord.Interaction, user_id: str):
    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "\U0000274C No tienes permiso.",
            ephemeral=True
        )
        return

    if not tiene_rol_autorizado(interaction.user):
        await interaction.response.send_message(
            "\U0000274C No tienes permiso para utilizar este comando.",
            ephemeral=True
        )
        return

    try:
        target_id = int(user_id)
    except ValueError:
        await interaction.response.send_message(
            "\U0000274C El ID no es válido.",
            ephemeral=True
        )
        return

    historial_data = await obtener_historial(target_id)

    if not historial_data:
        await interaction.response.send_message(
            "\U0001F4CB Este usuario no tiene historial de sanciones.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="\U0001F4CB Historial de sanciones",
        description=f"Usuario: <@{target_id}>",
        color=discord.Color.blurple()
    )

    ultimas = historial_data[-10:]

    for numero, sancion in enumerate(reversed(ultimas), start=1):
        fecha = sancion.get("fecha", "Desconocida")

        if isinstance(fecha, datetime):
            fecha_texto = f"<t:{int(fecha.timestamp())}:F>"
        else:
            fecha_texto = str(fecha)

        texto = (
            f"\U0001F4DD **Razón:** {sancion.get('razon', 'Sin razón')}\n"
            f"\U0001F6E1\uFE0F **Moderador:** {sancion.get('moderador', 'Desconocido')}\n"
            f"\U0001F4C5 **Fecha:** {fecha_texto}\n"
            f"\U0001F512 **Estado:** {sancion.get('estado', 'Desconocido')}"
        )

        evidencia = sancion.get("evidencia", "")
        if evidencia:
            texto += f"\n\U0001F4F8 **Evidencia:** {evidencia}"

        embed.add_field(name=f"Registro {numero}", value=texto, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
# MANEJO GLOBAL DE ERRORES
# ---------------------------------------------------------------------------
@tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"[ERROR] {error}")

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                f"\U0000274C Ocurrió un error: `{error}`",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"\U0000274C Ocurrió un error: `{error}`",
                ephemeral=True
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# EVENTOS
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    print(f"Bot conectado como {bot.user}")
    print(f"ID del bot: {bot.user.id}")


@bot.event
async def setup_hook():
    await inicializar_mongodb()

    # Borrar comandos globales antiguos y volver a sincronizar
    tree.clear_commands(guild=None)
    await tree.sync()

    print("Comandos slash sincronizados.")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("No se encontró la variable BOT_TOKEN.")

    bot.run(TOKEN)
