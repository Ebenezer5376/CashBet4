import asyncio
import random
import time
from datetime import datetime, timedelta, timezone

# Remplacé : aiosqlite -> utilisation centralisée via database.py
# database.py doit être placé à la racine du projet (fourni précédemment).
# Il expose init_pool, init_db, migrate_sqlite_to_postgres, et les helpers DB.
from database import init_db, init_channels_db

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ApplicationHandlerStop   # ✅ ajouté ici
)
from telegram.constants import ParseMode

nest_asyncio.apply()
# ====== CONFIG ======
import os
TOKEN = os.getenv("BOT_TOKEN")
# On garde le chemin sqlite original uniquement pour migration si besoin.
SQLITE_DB_FILE = "cash_bet4_secure.db"       # utilisé seulement pour la migration sqlite -> postgres
SQLITE_CHANNELS_DB = "channels_config.db"    # idem (ancien DB canaux)

# NOTE: après migration complète vers Postgres tu peux supprimer ces variables
SUPPORT_CHAT_ID = 6153940370
INFO_CHANNEL = "-1002304908989"  # canal infos bonus (forward)
CASH_BET4_INFOS = "-1002960906104"
USERS_PER_PAGE = 20
PARIBET4_BOT_LINK = "https://t.me/PariBet4_Bot"
CHECK_PERIOD_SECONDS = 30 * 60   # vérification auto toutes les 30 minutes
ALERT_COOLDOWN_HOURS = 0.5       # anti-spam: 1 alerte max par période (~30 min)

# Canal Retrait + Logo (exemple)
CANAL_RETRAIT_ID = "-1002935190893"
LOGO_URL = "https://files.catbox.moe/bt6map.jpg"
IMAG_URL = "https://files.catbox.moe/3yzspc.jpg"
# ====== Anti-fraude ======
BLOCK_DAYS = 3  # durée du blocage après 3 fausses preuves
IMAC_URL = "https://files.catbox.moe/6scqld.jpg"
print("Bot token chargé :", bool(TOKEN))
# =========================  
# STYLE GLOBAL POUR TOUS LES MESSAGES DU BOT  
# =========================  
from telegram.constants import ParseMode  
from functools import wraps  

def format_html(text: str) -> str:
    """Encapsule le texte avec <b><i>...</i></b> sans casser le HTML."""
    if not text:
        return ""
    # Si le texte contient déjà du HTML, on évite de le doubler
    if "<b>" in text or "<i>" in text or "<u>" in text or "<code>" in text or "</" in text:
        return text
    return f"<b><i>{text}</i></b>"

def auto_style(func):
    """Décorateur pour appliquer automatiquement le style gras+italique aux messages."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        text = kwargs.get("text")
        if isinstance(text, str):
            kwargs["text"] = format_html(text)
        kwargs["parse_mode"] = ParseMode.HTML
        return await func(*args, **kwargs)
    return wrapper

# --- Patch automatique des méthodes Telegram ---
from telegram import Message, Bot
Message.reply_text = auto_style(Message.reply_text)
Bot.send_message = auto_style(Bot.send_message)

# ============================  
# Fonction universelle d'envoi stylé (canaux + privés)  
# ============================  
async def send_styled(bot, chat_id, text, **kwargs):
    """Envoie un message stylé HTML partout (canaux, groupes, MP)."""
    styled_text = format_html(text)
    await bot.send_message(
        chat_id=chat_id,
        text=styled_text,
        parse_mode=ParseMode.HTML,
        **kwargs
    )
    # ============================
# REMPLACEMENT POSTGRES
# ============================

# Dans le main :
from database import update_user_field

# ------------------------------
# Marquer le premier retrait effectué (PostgreSQL)
# ------------------------------
async def mark_user_withdrawn(user_id: str):
    """Met à jour has_withdrawn = TRUE après un premier retrait."""
    try:
        await update_user_field(int(user_id), "has_withdrawn", True)
    except Exception as e:
        print(f"[mark_user_withdrawn] Erreur : {e}")
        # ================================
# IMPORT PostgreSQL
# ================================
from database import (
    create_user as pg_create_user,
    get_user as pg_get_user,
    update_user_field as pg_update_user_field,
    add_transaction as pg_add_tx,
    add_solde as pg_add_solde,
    remove_solde as pg_remove_solde,
    get_required_channels_all,
    set_channel_link_by_id,
    conn_cm               # pour requêtes directes PostgreSQL
)

from datetime import datetime
from telegram.constants import ParseMode

# ================================
# Utilitaires simples
# ================================
def mask_user_id(user_id: str) -> str:
    user_id = str(user_id)
    return user_id[:4] + "****" if len(user_id) > 4 else user_id + "****"


# ================================
# Mise à jour du solde (POSTGRES)
# ================================
async def update_user_solde(user_id: str, new_solde: int):
    """Met à jour directement le solde utilisateur (usage rare)."""
    await pg_update_user_field(int(user_id), "solde", int(new_solde))


# ================================
# CREATE USER (POSTGRESQL)
# ================================
async def create_user(user_id: str, parrain: str | None = None):
    """
    Crée l’utilisateur s’il n’existe pas,
    puis définit le parrain si pas déjà défini.
    """
    user_id = str(user_id)

    # création si absent
    await pg_create_user(int(user_id), int(parrain) if parrain else None)


# ================================
# GET USER (POSTGRESQL)
# ================================
async def get_user(user_id: str):
    """Retourne un dict avec toutes les colonnes PostgreSQL."""
    return await pg_get_user(int(user_id))


# ================================
# UPDATE ANY FIELD (POSTGRESQL)
# ================================
async def update_user_field(user_id: str, field: str, value):
    return await pg_update_user_field(int(user_id), field, value)


# ================================
# TRANSACTIONS
# ================================
async def add_transaction(user_id: str, type_op: str, montant: int):
    """Historique propre PostgreSQL."""
    await pg_add_tx(int(user_id), type_op, int(montant))


async def add_solde(user_id: str, montant: int, type_op="Bonus"):
    await pg_add_solde(int(user_id), int(montant), type_op)


async def remove_solde(user_id: str, montant: int, type_op="Retrait Support"):
    return await pg_remove_solde(int(user_id), int(montant), type_op)


# ================================
# Compter filleuls (Postgres)
# ================================
async def get_filleuls_count(user_id: str) -> int:
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT COUNT(*) FROM filleuls WHERE parrain_id=%s",
                (int(user_id),)
            )
            row = await cur.fetchone()
            return row[0] if row else 0


# =========================================================
#   BONUS FILLEUL / PARRAIN (CONVERSION POSTGRESQL)
# =========================================================
async def apply_referral_bonus_if_eligible(user_id: str, bot):
    user_id = str(user_id)

    # 1️⃣ Récupérer info user
    u = await pg_get_user(int(user_id))
    if not u:
        return

    parrain = u.get("parrain")
    check_ok = u.get("check_passed", 0)

    # Doit avoir validé les canaux
    if not check_ok:
        return

    # Pas de parrain ou auto-parrainage
    if not parrain or str(parrain) == user_id:
        return

    # 2️⃣ Vérifier si déjà validé comme filleul
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM filleuls WHERE parrain_id=%s AND filleul_id=%s",
                (int(parrain), int(user_id))
            )
            exists = await cur.fetchone()

    if exists:
        return

    # 3️⃣ Enregistrer filleul + bonus
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            # Ajouter filleul
            await cur.execute(
                "INSERT INTO filleuls(parrain_id, filleul_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (int(parrain), int(user_id))
            )

            # Crédit parrain
            await cur.execute(
                "UPDATE users SET solde = solde + 500 WHERE user_id=%s",
                (int(parrain),)
            )
            await pg_add_tx(int(parrain), "Bonus Parrainage (nouveau filleul)", 500, conn_override=conn)

            # Crédit filleul
            await cur.execute(
                "UPDATE users SET solde = solde + 200 WHERE user_id=%s",
                (int(user_id),)
            )
            await pg_add_tx(int(user_id), "Bonus Inscription (via parrain)", 200, conn_override=conn)

        await conn.commit()

    # ======================
    # 🔔 Notifications Telegram
    # ======================
    try:
        info = await bot.get_chat(int(user_id))
        filleul_name = info.first_name or "Un utilisateur"
    except:
        filleul_name = "Un utilisateur"

    # Notif parrain
    try:
        await bot.send_message(
            chat_id=int(parrain),
            text=(
                f"🎉 <b>Nouveau filleul validé !</b>\n\n"
                f"👤 <b>{filleul_name}</b> vient d’ouvrir le menu.\n"
                f"💰 <b>+500 FCFA</b> ajoutés à ton solde."
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print("[notif parrain]", e)

    # Notif filleul
    try:
        parrain_name = "ton parrain"
        try:
            pi = await bot.get_chat(int(parrain))
            parrain_name = pi.first_name or parrain_name
        except:
            pass

        await bot.send_message(
            chat_id=int(user_id),
            text=(
                f"🤝 Bienvenue sur <b>Cash Bet4</b> 🎯\n\n"
                f"Validé par <b>{parrain_name}</b>.\n"
                f"🎁 <b>200 FCFA</b> ajoutés à ton solde."
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print("[notif filleul]", e)

    print(f"[👥 BONUS OK] parrain={parrain} (+500) | filleul={user_id} (+200)")
    # =========================
# IMPORTS POSTGRESQL
# =========================
from database import (
    get_required_channels_all,
    set_channel_link_by_id,
    conn_cm,
    update_user_field,
    get_user as pg_get_user
)

from datetime import datetime, timedelta, timezone
from telegram.constants import ParseMode


# =========================
# HELPERS CANAUX
# =========================
def _normalize_username_and_url(text: str):
    """
    Convertit une entrée (ex: '@CashBet4_Pub2' ou 'https://t.me/CashBet4_Pub2')
    en username + URL propre.
    """
    t = text.strip()
    if t.startswith("https://t.me/"):
        usr = t.split("https://t.me/")[-1].strip().lstrip("@").split("?")[0]
        url = f"https://t.me/{usr}"
    else:
        usr = t.lstrip("@")
        url = f"https://t.me/{usr}"
    return usr, url


async def clear_channel_link_by_id(cid: int):
    """Efface username + url pour un canal."""
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "UPDATE required_channels SET username=NULL, url=NULL WHERE id=%s",
                (int(cid),)
            )
        await conn.commit()


# =========================
# Anti-spam vérification
# =========================
def _now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _older_than(ts_str: str | None, hours: float) -> bool:
    if not ts_str:
        return True
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except:
        return True
    return (datetime.now(timezone.utc) - ts) >= timedelta(hours=hours)


async def _get_check_row(user_id: str):
    """Récupère row dans channel_check_status (PostgreSQL)."""
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT last_ok, last_alert, last_missing FROM channel_check_status WHERE user_id=%s",
                (int(user_id),)
            )
            return await cur.fetchone()


async def _set_check_row(user_id: str, *, last_ok=None, last_alert=None, last_missing=None):
    """Insert/update propre PostgreSQL."""
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """
                INSERT INTO channel_check_status (user_id, last_ok, last_alert, last_missing)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (user_id) DO UPDATE SET
                    last_ok     = COALESCE(EXCLUDED.last_ok, channel_check_status.last_ok),
                    last_alert  = COALESCE(EXCLUDED.last_alert, channel_check_status.last_alert),
                    last_missing= COALESCE(EXCLUDED.last_missing,channel_check_status.last_missing)
                """,
                (int(user_id), last_ok, last_alert, last_missing)
            )
        await conn.commit()


# =========================
# Vérifier l'abonnement d'un utilisateur
# =========================
async def get_missing_channels_for_user(bot, user_id: str) -> list[str]:
    missing = []
    channels = await get_required_channels_all()

    for c in channels:
        if not c["url"]:
            continue  # canal désactivé

        chat = f"@{c['username']}"
        try:
            m = await bot.get_chat_member(chat, int(user_id))
            if m.status not in ("member", "administrator", "creator"):
                missing.append(c["label"])
        except:
            missing.append(c["label"])

    return missing


async def maybe_alert_user_missing(bot, user_id: str, missing_labels: list[str]):
    row = await _get_check_row(user_id)
    last_ok, last_alert, last_missing = row if row else (None, None, None)

    missing_csv = ",".join(sorted(missing_labels))

    # anti-spam
    if last_missing == missing_csv and not _older_than(last_alert, ALERT_COOLDOWN_HOURS):
        return

    txt = (
        "🚨 𝗡𝗢𝗨𝗩𝗘𝗔𝗨 𝗖𝗔𝗡𝗔𝗟 𝗔𝗝𝗢𝗨𝗧𝗘!\n\n"
        "Un nouveau canal vient d’être ajouté à la liste des canaux obligatoires 🔔\n\n"
        "👉 Cliquez sur /start pour actualiser votre abonnement et rejoindre le nouveau canal.\n\n"
        "Restez connecté pour ne rien manquer et continuer à recevoir vos paiements 💰"
    )

    try:
        await bot.send_message(chat_id=int(user_id), text=txt, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        print(f"[alert send fail {user_id}]", e)

    await _set_check_row(user_id, last_alert=_now_str(), last_missing=missing_csv)


# =========================
# Vérification auto toutes les 30 min
# =========================
async def periodic_channel_check(app):
    await asyncio.sleep(5)
    print("🔁 Vérification auto : démarrage")

    while True:
        try:
            # récupérer tous les user_id de PostgreSQL
            async with conn_cm() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("SELECT user_id FROM users")
                    rows = await cur.fetchall()
            user_ids = [str(r[0]) for r in rows]

            channels = await get_required_channels_all()
            active = [c for c in channels if c["url"]]

            if not active:
                print("[AUTO CHECK] Aucun canal actif.")
                await asyncio.sleep(CHECK_PERIOD_SECONDS)
                continue

            not_ok = 0
            for uid in user_ids:
                missing = await get_missing_channels_for_user(app.bot, uid)
                if missing:
                    not_ok += 1
                    await maybe_alert_user_missing(app.bot, uid, missing)
                else:
                    await _set_check_row(uid, last_ok=_now_str(), last_missing="")
                await asyncio.sleep(0.03)

            if not_ok:
                print(f"[AUTO CHECK] {not_ok} utilisateur(s) désabonné(s).")
            else:
                print("[AUTO CHECK] Tous abonnés ✅")

        except Exception as e:
            print("[periodic_channel_check]", e)

        await asyncio.sleep(CHECK_PERIOD_SECONDS)


# =========================
# Anti-fraude : helpers
# =========================
async def get_user_row_raw(user_id: str):
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT user_id, fake_count, blocked_until FROM users WHERE user_id=%s",
                (int(user_id),)
            )
            return await cur.fetchone()


async def can_send_proof(user_id: str) -> tuple[bool, str | None]:
    # support = jamais bloqué
    if str(user_id) == str(SUPPORT_CHAT_ID):
        return True, None

    # banni ?
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT 1 FROM banned_users WHERE user_id=%s", (int(user_id),)
            )
            banned = await cur.fetchone()

    if banned:
        return False, "🚫 Votre compte est banni pour fraude."

    row = await get_user_row_raw(user_id)
    if row:
        _, _, blocked_until = row
        if blocked_until:
            try:
                until = blocked_until
                if datetime.now() < until:
                    remaining = until - datetime.now()
                    h = int(remaining.total_seconds() // 3600)
                    m = int((remaining.total_seconds() % 3600) // 60)
                    return False, f"⛔ Vous êtes temporairement bloqué.\nRéessayez dans {h}h{m:02d}."
            except:
                pass

    return True, None


async def record_fake_and_maybe_block(user_id: str, context: ContextTypes.DEFAULT_TYPE):
    """Ajoute fake_count + applique blocage/bannissement."""
    if str(user_id) == str(SUPPORT_CHAT_ID):
        return

    user_id_int = int(user_id)

    # incrémenter fake_count
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE users
                SET fake_count = COALESCE(fake_count, 0) + 1
                WHERE user_id = %s
            """, (user_id_int,))
        await conn.commit()

    # récupérer compteur
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT fake_count FROM users WHERE user_id=%s",
                (user_id_int,)
            )
            row = await cur.fetchone()
            fake_count = row[0] if row else 0

    msg = None

    if fake_count == 1:
        msg = (
            "⚠️ Avertissement : fausse preuve.\n"
            "Merci d'envoyer uniquement des preuves valides."
        )
    elif fake_count == 2:
        msg = (
            "⚠️ 2e avertissement : encore une fausse preuve et vous serez suspendu."
        )
    elif fake_count == 3:
        until = datetime.now() + timedelta(days=BLOCK_DAYS)
        await update_user_field(user_id_int, "blocked_until", until)

        msg = f"⛔ Suspendu {BLOCK_DAYS} jours pour fraude répétée."
    elif fake_count >= 5:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        async with conn_cm() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO banned_users(user_id,reason,date) VALUES (%s,%s,%s) "
                    "ON CONFLICT (user_id) DO UPDATE SET reason=EXCLUDED.reason, date=EXCLUDED.date",
                    (user_id_int, "Fraude répétée", now)
                )
                await cur.execute(
                    "UPDATE users SET blocked_until=NULL WHERE user_id=%s",
                    (user_id_int,)
                )
            await conn.commit()

        msg = "🚫 Bannissement définitif pour fraude."

    if msg:
        try:
            await context.bot.send_message(chat_id=user_id_int, text=msg)
        except:
            pass
            # ------------------------------
# Menu principal (ADMI visible pour support)
# ------------------------------
def main_menu(is_support: bool = False):
    base = [
        ["🔵Mon Solde💰", "🔵Historique📜"],
        ["🔵Parrainage👥", "🔵Bonus 1XBET / MELBET🎁"],
        ["🔵Retrait💸", "🔵Bonus 7j/7j🎁"],
        ["🔵Rejoindre canal d'infos📢", "🔵Ecrivez au Support pour vos préoccupations☎️"],
        ["🎟️ Code mystère", "🔵Cash Bet4 🔵"],  # ✅ Nouveau bouton ajouté ici
        ["🔵Pariez et gagnez sur PariBet4⚽"]
    ]

    if is_support:
        base.append(["🔵ADMI💺"])

    return ReplyKeyboardMarkup(base, resize_keyboard=True)
    from database import (
    create_user as pg_create_user,
    get_user as pg_get_user,
    update_user_field as pg_update_user_field,
    add_solde as pg_add_solde,
    add_transaction as pg_add_tx,
    conn_cm
)

# ------------------------------
# /start (message d’accueil)
# ------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    args = context.args
    parrain = str(args[0]) if args else None

    # ✅ Création de l’utilisateur (PostgreSQL)
    await pg_create_user(int(user_id), int(parrain) if parrain else None)

    # 🔗 Récupération des canaux obligatoires (PostgreSQL)
    channels = await get_required_channels_all()
    lines = []

    for c in channels:
        label = c["label"]
        if c["url"]:
            lines.append(
                f"🔵 <b><i>𝐑𝐞𝐣𝐨𝐢𝐧𝐬</i></b>👉 <a href='{c['url']}'><b><i>{label}</i></b></a>\n\n"
            )
        else:
            lines.append(
                f"🔵 <b><i>𝐑𝐞𝐣𝐨𝐢𝐧𝐬</i></b>👉 <b><i>{label}</i></b>\n\n"
            )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Check", callback_data="check_channels")]
    ])

    await update.message.reply_text(
    "<blockquote>👋 <b>𝐁𝐢𝐞𝐧𝐯𝐞𝐧𝐮𝐞 𝐬𝐮𝐫 𝐥𝐚 𝐩𝐥𝐚𝐭𝐞𝐟𝐨𝐫𝐦𝐞 𝐨𝐟𝐟𝐢𝐜𝐢𝐞𝐥𝐥𝐞 🔵 𝐂𝐚𝐬𝐡𝐁𝐞𝐭𝟒 🔵 𝐈𝐜𝐢, 𝐜𝐡𝐚𝐪𝐮𝐞 𝐦𝐞𝐦𝐛𝐫𝐞 𝐩𝐫𝐨𝐟𝐢𝐭𝐞 𝐝’𝐮𝐧 𝐬𝐮𝐢𝐯𝐢 𝐩𝐫𝐨𝐟𝐞𝐬𝐬𝐢𝐨𝐧𝐧𝐞𝐥, 𝐝’𝐮𝐧 𝐬𝐞𝐫𝐯𝐢𝐜𝐞 𝐫𝐚𝐩𝐢𝐝𝐞 𝐞𝐭 𝐝𝐞 𝐩𝐚𝐢𝐞𝐦𝐞𝐧𝐭𝐬 𝐬𝐞𝐜𝐮𝐫𝐢𝐬𝐞́𝐬.</b></blockquote>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    "📢 <b>𝐏𝐨𝐮𝐫 𝐠𝐚𝐫𝐚𝐧𝐭𝐢𝐫 𝐥𝐚 𝐫𝐞𝐜𝐞𝐩𝐭𝐢𝐨𝐧 𝐝𝐞 𝐯𝐨𝐬 𝐠𝐚𝐢𝐧𝐬, 𝐢𝐥 𝐞𝐬𝐭 𝐨𝐛𝐥𝐢𝐠𝐚𝐭𝐨𝐢𝐫𝐞 𝐝𝐞 𝐫𝐞𝐣𝐨𝐢𝐧𝐝𝐫𝐞 𝐭𝐨𝐮𝐬 𝐥𝐞𝐬 𝐜𝐚𝐧𝐚𝐮𝐱 𝐜𝐢-𝐝𝐞𝐬𝐬𝐨𝐮𝐬 :</b>\n"
    "━━━━━━━━━━━━━━━━━━━━━━━\n"
    + "".join(lines) +
    "\n✅ 𝐀𝐩𝐫𝐞𝐬 𝐚𝐯𝐨𝐢𝐫 𝐫𝐞𝐣𝐨𝐢𝐧𝐭 𝐭𝐨𝐮𝐬 𝐥𝐞𝐬 𝐜𝐚𝐧𝐚𝐮𝐱, 𝐜𝐥𝐢𝐪𝐮𝐞𝐳 𝐬𝐮𝐫 “𝐂𝐡𝐞𝐜𝐤” 𝐩𝐨𝐮𝐫 𝐯𝐚𝐥𝐢𝐝𝐞𝐫 𝐯𝐨𝐭𝐫𝐞 𝐚𝐝𝐡𝐞𝐬𝐢𝐨𝐧.\n"
    "--------------------------------------------------\n"
    "<blockquote>🔷 <b>𝐍𝐨𝐭𝐞 𝐢𝐦𝐩𝐨𝐫𝐭𝐚𝐧𝐭𝐞 :</b>\n"
    "𝐏𝐨𝐮𝐫 𝐚𝐬𝐬𝐮𝐫𝐞𝐫 𝐥𝐚 𝐛𝐨𝐧𝐧𝐞 𝐫𝐞𝐜𝐞𝐩𝐭𝐢𝐨𝐧 𝐝𝐞 𝐯𝐨𝐬 𝐩𝐚𝐢𝐞𝐦𝐞𝐧𝐭𝐬, 𝐫𝐞𝐬𝐭𝐞𝐳 𝐚𝐛𝐨𝐧𝐧𝐞́ 𝐚̀ 𝐭𝐨𝐮𝐬 𝐥𝐞𝐬 𝐜𝐚𝐧𝐚𝐮𝐱 𝐣𝐮𝐬𝐪𝐮’𝐚̀ 𝐥𝐚 𝐜𝐨𝐧𝐟𝐢𝐫𝐦𝐚𝐭𝐢𝐨𝐧 𝐝𝐞 𝐯𝐨𝐭𝐫𝐞 𝐯𝐞𝐫𝐬𝐞𝐦𝐞𝐧𝐭 ✅</blockquote>\n"
    "--------------------------------------------------\n"
    "🚨 <b>𝐒𝐢 𝐥𝐞 𝐛𝐨𝐭 𝐯𝐨𝐮𝐬 𝐝𝐞𝐦𝐚𝐧𝐝𝐞 𝐝𝐞 𝐫𝐞𝐣𝐨𝐢𝐧𝐝𝐫𝐞 𝐞𝐧𝐜𝐨𝐫𝐞 𝐚̀ 𝐧𝐨𝐮𝐯𝐞𝐚𝐮</b>, 𝐜𝐥𝐢𝐪𝐮𝐞𝐳 𝐬𝐮𝐫 👉 /start 𝐩𝐨𝐮𝐫 𝐫𝐞𝐥𝐚𝐧𝐜𝐞𝐫 𝐥𝐚 𝐯𝐞𝐫𝐢𝐟𝐢𝐜𝐚𝐭𝐢𝐨𝐧 𝐚𝐮𝐭𝐨𝐦𝐚𝐭𝐢𝐪𝐮𝐞 🚨",
    reply_markup=keyboard,
    parse_mode=ParseMode.HTML,
    disable_web_page_preview=True
# ✅ empêche l’affichage d’un aperçu de lien
    )

# ------------------------------
# Check channels callback
# ------------------------------
async def check_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = str(query.from_user.id)
    await query.answer()

    all_subscribed = True
    channels = await get_required_channels_all()

    for c in channels:
        if not c["url"]:
            continue

        chat = f"@{c['username']}"
        try:
            member = await context.bot.get_chat_member(chat_id=chat, user_id=int(user_id))
            if member.status not in ["member", "administrator", "creator"]:
                all_subscribed = False
                break
        except:
            all_subscribed = False
            break

    if all_subscribed:
        user = await pg_get_user(int(user_id))

        # user est maintenant un dict → PAS user[5] ou user[6] !!
        check_passed = user.get("check_passed", False)
        welcome_bonus = user.get("welcome_bonus", 0)

        if not check_passed:
            await pg_update_user_field(int(user_id), "check_passed", True)

            # Accorder le bonus de bienvenue une seule fois
            if welcome_bonus == 0:
                await pg_add_solde(int(user_id), 2000, "Bonus Bienvenue")
                await pg_update_user_field(int(user_id), "welcome_bonus", 2000)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎛️ Ouvrir le Menu", callback_data="show_menu")]
        ])

        await context.bot.send_message(
            chat_id=user_id,
            text="<blockquote>✅Félicitation! Votre fidélité est récompensée.Un bonus de 2000𝗙𝗖𝗙𝗔  a été ajouté sur votre compte 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰✅ </blockquote>",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )

    else:
        await context.bot.send_message(
            chat_id=user_id,
            text="❌𝗩𝗼𝘂𝘀 𝗱𝗲𝘃𝗲𝘇 𝘃𝗼𝘂𝘀 𝗮𝗯𝗼𝗻𝗻𝗲𝗿 𝗮̀ 𝘁𝗼𝘂𝘀 𝗹𝗲𝘀 𝗰𝗮𝗻𝗮𝘂𝘅 𝗼𝗯𝗹𝗶𝗴𝗮𝘁𝗼𝗶𝗿𝗲𝘀.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Check", callback_data="check_channels")]
            ])
        )


# ------------------------------
# Menu principal callback
# ------------------------------
async def show_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)

    await query.edit_message_text(
        text="🎛️ 𝗠𝗲𝗻𝘂 𝗽𝗿𝗶𝗻𝗰𝗶𝗽𝗮𝗹",
        reply_markup=None
    )

    is_support = (int(user_id) == int(SUPPORT_CHAT_ID))

    await context.bot.send_message(
        chat_id=user_id,
        text="𝗩𝗼𝗶𝗰𝗶 𝘃𝗼𝘁𝗿𝗲 𝗺𝗲𝗻𝘂 👇",
        reply_markup=main_menu(is_support)
    )

    # Bonus parrain/filleul PostgreSQL
    await apply_referral_bonus_if_eligible(user_id, context.bot)


# ------------------------------
# Pagination des utilisateurs (ADMI)
# ------------------------------
async def get_users_page_async(page: int):
    offset = page * USERS_PER_PAGE

    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT COUNT(*) FROM users")
            total = (await cur.fetchone())[0]

            await cur.execute(
                """
                SELECT user_id, solde, bonus_claimed, welcome_bonus
                FROM users
                ORDER BY user_id
                LIMIT %s OFFSET %s
                """,
                (USERS_PER_PAGE, offset)
            )
            rows = await cur.fetchall()

    text = "📋 Liste des utilisateurs\n\n"
    if not rows:
        text += "⚠️ Aucun utilisateur trouvé."
    else:
        for r in rows:
            uid, sol, bonus_claimed, welcome = r
            bonus_amount = 4000 if bonus_claimed else 0
            text += (
                f"👤 ID: `{uid}` | Solde: {sol} FCFA | "
                f"Bonus1XBET: {bonus_amount} | Bienvenue: {welcome} FCFA\n"
            )

    buttons = []
    if offset > 0:
        buttons.append(InlineKeyboardButton("⬅️ Précédent", callback_data=f"admi_users_{page-1}"))
    if offset + USERS_PER_PAGE < total:
        buttons.append(InlineKeyboardButton("➡️ Suivant", callback_data=f"admi_users_{page+1}"))

    keyboard = []
    if buttons:
        keyboard.append(buttons)
    keyboard.append([InlineKeyboardButton("⬅️ Retour", callback_data="admi_main")])

    return text, InlineKeyboardMarkup(keyboard)
    # ------------------------------
# Menu ADMI (inline)
# ------------------------------
async def admi_menu_callback_from_message(chat_id: str, bot, context):
    buttons = [
        [InlineKeyboardButton("📋 Liste des utilisateurs", callback_data="admi_users_0")],
        [InlineKeyboardButton("⚠️ Avertir un utilisateur", callback_data="admi_warn")],
        [InlineKeyboardButton("💸 Retirer des gains", callback_data="admi_remove")],
        [InlineKeyboardButton("🚫 Bannir un utilisateur", callback_data="admi_ban")],
        [InlineKeyboardButton("🔗 Gérer les canaux obligatoires", callback_data="admi_channels")],
        [InlineKeyboardButton("💸 Essaie de retrait", callback_data="admi_try_withdraw")],
        [InlineKeyboardButton("🚫 Gestion des blocages", callback_data="admi_block_menu")],
        [InlineKeyboardButton("📢 Publier faux bonus 1XBET/MELBET", callback_data="admi_fake_bonus")],
        [InlineKeyboardButton("🌀 Générer code mystère", callback_data="admi_generate_code")],
        [InlineKeyboardButton("⬅️ Retour", callback_data="admi_back_to_main")]
    ]
    await bot.send_message(
        chat_id=chat_id,
        text="👉 Menu Support (ADMI) :",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admi_menu_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)

    if int(user_id) != int(SUPPORT_CHAT_ID):
        await update.message.reply_text("❌ Accès refusé.")
        return

    await admi_menu_callback_from_message(user_id, context.bot, context)


async def admi_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    # 🔒 Vérification support
    if int(uid) != int(SUPPORT_CHAT_ID):
        await query.answer("❌ Accès refusé")
        return

    data = query.data

    # --------------------
    # Retour au menu
    # --------------------
    if data in ("admi_main", "admi_back_to_main"):
        buttons = [
            [InlineKeyboardButton("📋 Liste des utilisateurs", callback_data="admi_users_0")],
            [InlineKeyboardButton("⚠️ Avertir un utilisateur", callback_data="admi_warn")],
            [InlineKeyboardButton("💸 Retirer des gains", callback_data="admi_remove")],
            [InlineKeyboardButton("🚫 Bannir un utilisateur", callback_data="admi_ban")],
            [InlineKeyboardButton("🔗 Gérer les canaux obligatoires", callback_data="admi_channels")],
            [InlineKeyboardButton("💸 Essaie de retrait", callback_data="admi_try_withdraw")],
            [InlineKeyboardButton("🚫 Gestion des blocages", callback_data="admi_block_menu")],
            [InlineKeyboardButton("⬅️ Retour", callback_data="admi_back_to_main")]
        ]
        await query.edit_message_text(
            "👉 Menu Support (ADMI) :",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        return

    # --------------------
    # Pagination utilisateurs
    # --------------------
    if data.startswith("admi_users_"):
        try:
            page = int(data.split("_")[-1])
        except:
            page = 0

        text, markup = await get_users_page_async(page)

        try:
            await query.edit_message_text(
                text=text,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            await context.bot.send_message(
                chat_id=SUPPORT_CHAT_ID,
                text=text,
                reply_markup=markup,
                parse_mode=ParseMode.MARKDOWN
            )
        return

    # --------------------
    # Commandes simples
    # --------------------
    if data == "admi_warn":
        await query.edit_message_text(
            "⚠️ Pour avertir un utilisateur, utilisez :\n`/warn <user_id> <message>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "admi_remove":
        await query.edit_message_text(
            "💸 Pour retirer des gains :\n`/remove <user_id> <montant>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "admi_ban":
        await query.edit_message_text(
            "🚫 Pour bannir un utilisateur :\n`/ban <user_id> [raison]`",
            parse_mode=ParseMode.MARKDOWN
        )
        return
        # ------------------------------
# ADMIN : gestion complète des canaux (public + privé)
# ------------------------------
async def admi_channels_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # 🔐 Vérifie que seul le support peut gérer les canaux
    if q.from_user.id != SUPPORT_CHAT_ID:
        await q.edit_message_text("❌ Accès refusé.")
        return

    data = q.data

    # 📋 Liste des canaux enregistrés
    if data == "admi_channels":
        rows = await get_required_channels_all()
        lines, kb = [], []

        if not rows:
            lines.append("Aucun canal configuré pour le moment.")

        for r in rows:
            show_pub = r.get("public_username") or "—"
            show_priv = r.get("private_link") or "—"

            lines.append(
                f"{r['id']}. {r['label']}\n"
                f"🔓Public : {show_pub}\n"
                f"🔐Privé : {show_priv}"
            )

            kb.append([
                InlineKeyboardButton(
                    f"🔄 Remplacer {r['id']}",
                    callback_data=f"admi_ch_replace_{r['id']}"
                ),
                InlineKeyboardButton(
                    f"🗑️ Supprimer {r['id']}",
                    callback_data=f"admi_ch_delete_{r['id']}"
                )
            ])

        kb.append([InlineKeyboardButton("➕ Ajouter un canal", callback_data="admi_ch_add")])
        kb.append([InlineKeyboardButton("⬅️ Retour ADMI", callback_data="admi_main")])

        await q.edit_message_text(
            "⚙️ <b>Gestion des canaux obligatoires</b>\n\n" + "\n\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    # ------------------------------
    # 🔄 Remplacer un canal existant
    # ------------------------------
    if data.startswith("admi_ch_replace_"):
        cid = int(data.split("_")[-1])
        context.user_data["await_ch_replace_id"] = cid

        await q.edit_message_text(
            f"✏️ Envoie les informations du canal ID:{cid} au format suivant :\n\n"
            "<b>https://t.me/lien_publique | https://t.me/lien_privé</b>\n\n"
            "Exemple : <code>https://t.me/Bet4_Pub1 | https://t.me/+7DgHghxxxx</code>",
            parse_mode=ParseMode.HTML
        )
        return

    # ------------------------------
    # ➕ Ajouter canal : nom + 2 liens
    # ------------------------------
    if data == "admi_ch_add":
        context.user_data["await_ch_add"] = True

        await q.edit_message_text(
            "➕ Envoie au format :\n"
            "<b>@NomDuCanal | https://t.me/lien_Public | https://t.me/lien_privé</b>\n\n"
            "Exemple : <code>@CashBet4_Pub8 | https://t.me/CashBet4_Pub8 | https://t.me/+kHGyxxxx</code>",
            parse_mode=ParseMode.HTML
        )
        return

    # ------------------------------
    # 🗑️ Supprimer un canal (POSTGRESQL)
    # ------------------------------
    if data.startswith("admi_ch_delete_"):
        cid = int(data.split("_")[-1])

        async with conn_cm() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE required_channels
                    SET url=NULL,
                        username=label,
                        public_username=NULL,
                        private_link=NULL
                    WHERE id=%s
                """, (cid,))
            await conn.commit()

        await q.edit_message_text(
            f"🗑️ Les liens du canal ID:{cid} ont été supprimés.\n"
            f"🔹 Le @pseudo reste.\n"
            f"🔹 Tu peux maintenant ajouter un nouveau lien via « Remplacer ».",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Retour ADMI", callback_data="admi_main")]
            ])
        )
        return
        # ------------------------------
# ADMIN : Gestion des réponses texte pour AJOUT / REMPLACEMENT
# ------------------------------
async def admi_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Seul le compte support peut gérer les canaux
    if update.effective_user.id != SUPPORT_CHAT_ID:
        return

    txt = (update.message.text or "").strip()

    # ----------------------------------------------------------------------
    # 🔄 Remplacement d’un canal (public + privé)
    # ----------------------------------------------------------------------
    if context.user_data.get("await_ch_replace_id"):
        cid = context.user_data.pop("await_ch_replace_id")

        try:
            public_link, private_link = [p.strip() for p in txt.split("|")]

            # Normaliser username
            username = public_link.replace("https://t.me/", "").replace("@", "")

            async with conn_cm() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        UPDATE required_channels
                        SET username=%s,
                            url=%s,
                            public_username=%s,
                            private_link=%s
                        WHERE id=%s
                    """, (username, public_link, public_link, private_link, cid))

                await conn.commit()

            await update.message.reply_text(
                f"✅ Canal ID:{cid} mis à jour avec succès !\n"
                f"🔓 Public : {public_link}\n"
                f"🔐 Privé : {private_link}"
            )

        except Exception as e:
            await update.message.reply_text(f"❌ Erreur : {e}")

        return

    # ----------------------------------------------------------------------
    # ➕ Ajout d’un NOUVEAU canal (label + public + privé)
    # ----------------------------------------------------------------------
    if context.user_data.get("await_ch_add"):
        context.user_data.pop("await_ch_add")

        try:
            label, public_link, private_link = [p.strip() for p in txt.split("|")]

            # Normaliser username
            username = public_link.replace("https://t.me/", "").replace("@", "")

            async with conn_cm() as conn:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        INSERT INTO required_channels (label, username, url, public_username, private_link)
                        VALUES (%s, %s, %s, %s, %s)
                    """, (label, username, public_link, public_link, private_link))

                await conn.commit()

            await update.message.reply_text(
                f"✅ Canal ajouté : <b>{label}</b>\n"
                f"🔓 Public : {public_link}\n"
                f"🔐 Privé : {private_link}",
                parse_mode=ParseMode.HTML
            )

        except Exception as e:
            await update.message.reply_text(f"❌ Erreur : {e}")

        return
        from database import conn_cm, update_user_field
from datetime import datetime
from telegram.constants import ParseMode

# ------------------------------
# Commande support : /warn
# ------------------------------
async def cmd_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPPORT_CHAT_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage : /warn <user_id> <message>")
        return

    target = args[0]
    message = " ".join(args[1:])

    try:
        await context.bot.send_message(
            chat_id=target,
            text=f"⚠️ AVERTISSEMENT du Support :\n{message}"
        )
        await update.message.reply_text(f"✅ Avertissement envoyé à {target}.")
    except Exception as e:
        await update.message.reply_text(f"❌ Erreur lors de l'envoi : {e}")


# ------------------------------
# Commande support : /remove
# ------------------------------
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPPORT_CHAT_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage : /remove <user_id> <montant>")
        return

    target = args[0]

    try:
        montant = int(args[1])
    except:
        await update.message.reply_text("❌ Montant invalide.")
        return

    ok, err = await remove_solde(target, montant, "Retrait Support")
    if not ok:
        await update.message.reply_text(f"❌ Échec : {err}")
        return

    try:
        await context.bot.send_message(
            chat_id=target,
            text=f"💸 {montant} FCFA a été retiré de votre compte par le support."
        )
    except:
        pass

    await update.message.reply_text(f"✅ {montant} FCFA retirés du compte {target}.")


# ------------------------------
# Commande support : /ban
# ------------------------------
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPPORT_CHAT_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage : /ban <user_id> [raison]")
        return

    target = args[0]
    reason = " ".join(args[1:]) if len(args) > 1 else "Violation des règles"
    date_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            # 🔥 PostgreSQL : UPSERT
            await cur.execute("""
                INSERT INTO banned_users(user_id, reason, date)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    reason = EXCLUDED.reason,
                    date   = EXCLUDED.date
            """, (int(target), reason, date_now))

            # ❌ On enlève l’utilisateur de la table users
            await cur.execute("DELETE FROM users WHERE user_id=%s", (int(target),))

        await conn.commit()

    try:
        await context.bot.send_message(
            chat_id=target,
            text=f"🚫 Vous avez été banni. Raison : {reason}"
        )
    except:
        pass

    await update.message.reply_text(f"✅ Utilisateur {target} banni. Raison : {reason}")


# ------------------------------
# Commande support : /unblock
# ------------------------------
async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPPORT_CHAT_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    args = context.args
    if len(args) < 1:
        await update.message.reply_text("Usage : /unblock <user_id>")
        return

    target = args[0]

    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                UPDATE users
                SET blocked_until=NULL,
                    fake_count=0
                WHERE user_id=%s
            """, (int(target),))

            await cur.execute("DELETE FROM banned_users WHERE user_id=%s", (int(target),))

        await conn.commit()

    try:
        await context.bot.send_message(
            chat_id=target,
            text="✅ Votre compte a été débloqué par le support."
        )
    except:
        pass

    await update.message.reply_text(f"✅ Utilisateur {target} débloqué avec succès.")


# ------------------------------
# Commande support : /listblocked
# ------------------------------
async def cmd_listblocked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPPORT_CHAT_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    text = "📋 <b>Liste des utilisateurs bloqués/bannis</b>\n\n"
    now = datetime.now()
    count = 0

    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            # 🔥 Blocages temporaires
            await cur.execute("""
                SELECT user_id, blocked_until, fake_count
                FROM users
                WHERE blocked_until IS NOT NULL
            """)
            temp_rows = await cur.fetchall()

            # 🔥 Bannissements
            await cur.execute("""
                SELECT user_id, reason, date
                FROM banned_users
            """)
            ban_rows = await cur.fetchall()

    # Aucun utilisateur bloqué
    if not temp_rows and not ban_rows:
        await update.message.reply_text("✅ Aucun utilisateur bloqué ni banni.")
        return

    # Blocages temporaires
    if temp_rows:
        text += "⏳ <b>Blocages temporaires :</b>\n"
        for uid, until, fake_count in temp_rows:
            try:
                if now < until:
                    remaining = until - now
                    hours = int(remaining.total_seconds() // 3600)
                    mins = int((remaining.total_seconds() % 3600) // 60)
                    text += (
                        f"• ID <code>{uid}</code> → encore {hours}h{mins:02d} "
                        f"(fausses preuves: {fake_count})\n"
                    )
                    count += 1
            except:
                pass
        text += "\n"

    # Bannissements définitifs
    if ban_rows:
        text += "🚫 <b>Bannissements définitifs :</b>\n"
        for uid, reason, date in ban_rows:
            text += f"• ID <code>{uid}</code> — Raison: {reason} ({date})\n"
            count += 1

    text += f"\n🧾 Total: {count} utilisateur(s)\n"

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ------------------------------
# Commande support : /clearblocked
# ------------------------------
async def cmd_clearblocked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != SUPPORT_CHAT_ID:
        await update.message.reply_text("❌ Accès refusé.")
        return

    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM banned_users")
            await cur.execute("UPDATE users SET blocked_until=NULL, fake_count=0")
        await conn.commit()

    await update.message.reply_text(
        "🧹 Tous les utilisateurs ont été débloqués et les compteurs réinitialisés."
    )
    from database import (
    conn_cm,
    get_user as pg_get_user,
    update_user_field as pg_update_user_field,
    add_solde as pg_add_solde
)
from datetime import datetime
from telegram.constants import ParseMode

# ------------------------------
# Support : créditer/rejeter/partager bonus 1XBET/MELBET
# ------------------------------
async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Décodage des actions
    if data.startswith("bonus_"):
        _, montant, target_id = data.split("_", 2)
        action = "bonus"
        montant = int(montant)

    elif data.startswith("rejeter_"):
        _, target_id = data.split("_", 1)
        action = "rejeter"
        montant = None

    elif data.startswith("forward_"):
        _, target_id = data.split("_", 1)
        action = "forward"
        montant = None

    else:
        return

    # 🔒 sécurité support
    if str(query.from_user.id) != str(SUPPORT_CHAT_ID):
        try:
            await query.edit_message_text("⚠️ Seul le support peut valider ou rejeter.")
        except:
            pass
        return

    # Récupération utilisateur (PostgreSQL → dict)
    user = await pg_get_user(int(target_id))

    # ============================================================
    #  VALIDATION BONUS
    # ============================================================
    if action == "bonus":

        if not user:
            await query.edit_message_text(f"⚠️ Utilisateur {target_id} introuvable.")
            return

        # Déjà validé ?
        if user.get("bonus_claimed", 0) == 1:
            await query.edit_message_text(f"⚠️ L'utilisateur {target_id} a déjà reçu ce bonus.")
            return

        # Créditer l’utilisateur
        await pg_add_solde(int(target_id), montant, "Bonus 1XBET/MELBET")
        await pg_update_user_field(int(target_id), "bonus_claimed", 1)

        # Réinitialiser l’antifraude
        async with conn_cm() as conn:
            async with conn.cursor() as cur:
                await cur.execute("""
                    UPDATE users
                    SET fake_count=0,
                        blocked_until=NULL
                    WHERE user_id=%s
                """, (int(target_id),))
            await conn.commit()

        # Nouveau solde = solde actuel + bonus
        new_solde = (user.get("solde", 0) or 0) + montant

        # Message utilisateur
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"🎉 Félicitations ! Votre bonus de {montant} 𝗙𝗖𝗙𝗔 a été crédité sur votre compte 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 ✅\n\n"
                f"💰 Nouveau solde disponible : {(user[1] or 0) + montant} 𝗙𝗖𝗙𝗔\n\n"
                "⚽ Ne garde pas ton bonus dormant ! Utilise-le dès maintenant pour *parier et gagner encore plus* sur notre second Bot 𝗣𝗮𝗿𝗶𝗕𝗲𝘁𝟰 💸\n\n"
                "👉 Clique ici pour commencer à parier : https://t.me/PariBet4_Bot"
            )
        )

        # Message de confirmation
        try:
            await query.edit_message_text(f"✅ Bonus {montant} FCFA confirmé pour {target_id}")
        except:
            pass

        # Publication auto pour support
        masked = mask_user_id(target_id)

        info_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📤 PARTAGER", callback_data=f"forward_{target_id}")]
        ])

        # Message photo pour support
        pre_msg = await context.bot.send_photo(
            chat_id=SUPPORT_CHAT_ID,
            photo=IMAG_URL,
            caption=(
        "<b><i>"
        "🔵 𝗕𝗢𝗡𝗨𝗦 𝟭𝗫𝗕𝗘𝗧 / 𝗠𝗘𝗟𝗕𝗘𝗧 🔵\n\n"
        "🎉 Félicitations ! Cet abonné vient de créer son compte en utilisant le code promo 👉 BUSS6 sur la plateforme de son choix (𝟭𝗫𝗕𝗘𝗧 ou 𝗠𝗘𝗟𝗕𝗘𝗧).\n"
        "-------------------------------------------------\n"
        f"💰 Après son dépôt, il reçoit un bonus exceptionnel retirable de {montant} 𝗙𝗖𝗙𝗔 sur son compte 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 💸🚀.\n"
        "Toi aussi, tu peux gagner jusqu'à 20 500 𝗙𝗖𝗙𝗔 en fonction du montant déposé sur ton compte 𝟭𝗫𝗕𝗘𝗧 ou 𝗠𝗘𝗟𝗕𝗘𝗧.\n"
        "-------------------------------------------------\n"
        "🔷 État : Réclamé / Validé ✅\n\n"
        f"🔷 ID Bénéficiaire : {masked}\n\n"
        "🔷 Bénéficiaire : Abonné fidèle\n\n"
        f"🔷 Montant Bonus : {montant} 𝗙𝗖𝗙𝗔\n\n"
        f"📅 Date : {fr_datetime_now_str()}\n"
        "-------------------------------------------------\n"
        "🔵𝗖𝗢𝗗𝗘 𝟭𝗫𝗕𝗘𝗧 : BUSS6 ou BAF8\n"
        "🟡𝗖𝗢𝗗𝗘 𝗠𝗘𝗟𝗕𝗘𝗧 : BUSS6\n"
        "🤖 @CashBet4_bot"
        "</i></b>"
    ),
            parse_mode="HTML",
            reply_markup=info_keyboard
        )

        # Sauvegarde message_id dans la DB
        await pg_update_user_field(int(target_id), "bonus_message_id", pre_msg.message_id)

        return

    # ============================================================
    #  REJET PREUVE
    # ============================================================
    if action == "rejeter":

        await context.bot.send_message(
            chat_id=target_id,
            text=(
            "<b><i>"
            "❌ Désolé, vos preuves ont été rejetées par le support.\n\n"
            "Vous devez vous inscrire sur 𝟭𝗫𝗕𝗘𝗧 ou 𝗠𝗘𝗟𝗕𝗘𝗧, le site de votre choix, en utilisant :\n\n"
            "🔹 Le code promo <b>BUSS6</b> ou <b>BAF8</b> sur 𝟭𝗫𝗕𝗘𝗧\n"
            "🔹 Le code promo <b>BUSS6</b> sur 𝗠𝗘𝗟𝗕𝗘𝗧\n\n"
            "💰 Fais un dépôt d’au moins 1 000 𝗙𝗖𝗙𝗔 et reviens réclamer ton bonus.\n"
            "Tu gagneras jusqu’à 20 500 𝗙𝗖𝗙𝗔 selon le montant déposé.\n"
            "</i></b>"
        ),
        parse_mode=ParseMode.HTML
    )
        await record_fake_and_maybe_block(target_id, context)

        try:
            await query.edit_message_text(
                text=f"<b><i>❌ Demande rejetée pour {target_id}</i></b>",
                parse_mode=ParseMode.HTML
            )
        except:
            pass

        # Supprime son message bonus précédent s’il existe
        premsg = user.get("bonus_message_id")
        if premsg:
            try:
                await context.bot.delete_message(
                    chat_id=SUPPORT_CHAT_ID,
                    message_id=premsg
                )
            except:
                pass
            await pg_update_user_field(int(target_id), "bonus_message_id", None)

        return

    # ============================================================
    #  PARTAGE DANS CANAL d’INFO
    # ============================================================
    if action == "forward":
        msg_id = user.get("bonus_message_id")
        if not msg_id:
            await query.edit_message_text("⚠️ Aucun message à partager.")
            return

        try:
            await context.bot.forward_message(
                chat_id=INFO_CHANNEL,
                from_chat_id=SUPPORT_CHAT_ID,
                message_id=msg_id
            )
            await query.edit_message_text("✅ Message partagé dans le canal Infos Bonus.")

            # Supprime l’ID après partage
            await pg_update_user_field(int(target_id), "bonus_message_id", None)

        except Exception as e:
            await context.bot.send_message(
                chat_id=SUPPORT_CHAT_ID,
                text=f"❌ Erreur lors du partage : {e}"
            )

        return
        # ------------------------------
# Reset callback handler
# ------------------------------
async def reset_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "reset_daily_":
        await query.edit_message_text("✅ Réinitialisation journalière effectuée avec succès.")
    elif data == "reset_1xbet_":
        await query.edit_message_text("✅ Réinitialisation du compteur 1XBET effectuée.")
    else:
        await query.edit_message_text("⚠️ Action de reset inconnue.")


# ------------------------------
# Historique (PostgreSQL)
# ------------------------------
from database import conn_cm

async def historique(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_chat.id)

    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute("""
                SELECT type, montant, date
                FROM transactions
                WHERE user_id = %s
                ORDER BY id DESC
                LIMIT 20
            """, (user_id,))
            rows = await cur.fetchall()

    if not rows:
        await update.message.reply_text("📜 Aucun historique trouvé.")
        return

    msg = "📜 𝗛𝗶𝘀𝘁𝗼𝗿𝗶𝗾𝘂𝗲 𝗱𝗲𝘀 𝘁𝗿𝗮𝗻𝘀𝗮𝗰𝘁𝗶𝗼𝗻𝘀 (20 dernières) :\n\n"

    for ttype, montant, date in rows:
        msg += f"• {date} → {ttype} : {montant} FCFA\n"

    await update.message.reply_text(msg)
    from database import get_user as pg_get_user
from telegram.constants import ParseMode

# ------------------------------
# Preuves -> envoi au support (avec anti-fraude)
# ------------------------------
async def preuve_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(update.effective_chat.id)
    user = await pg_get_user(user_id)   # dict PostgreSQL

    # 🔐 Vérification basique
    if not user or not user.get("check_passed", 0):
        await update.message.reply_text("⚠️ Tape /start et rejoins les canaux avant d’envoyer une preuve.")
        return

    # 🔁 Déjà réclamé ?
    if user.get("bonus_claimed", 0) == 1:
        await update.message.reply_text("⚠️ Vous avez déjà réclamé ce bonus.")
        return

    # 🔎 Contrôle anti-fraude
    allowed, reason = await can_send_proof(user_id)
    if not allowed:
        await update.message.reply_text(reason)
        return

    # 🔍 Récupération éventuelle du site
    site = None
    bstate = context.user_data.get("bonus")
    if isinstance(bstate, dict) and bstate.get("stage") == "await_proof":
        site = bstate.get("site")

    # ------------------------------
    # 🔘 Boutons envoyés au support
    # ------------------------------
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("2000 FCFA", callback_data=f"bonus_2000_{user_id}"),
            InlineKeyboardButton("4500 FCFA", callback_data=f"bonus_4500_{user_id}")
        ],
        [
            InlineKeyboardButton("10.000 FCFA", callback_data=f"bonus_10000_{user_id}"),
            InlineKeyboardButton("20.500 FCFA", callback_data=f"bonus_20500_{user_id}")
        ],
        [InlineKeyboardButton("❌ Rejeter", callback_data=f"rejeter_{user_id}")]
    ])

    # ------------------------------
    # Message d’avertissement anti-fraude
    # ------------------------------
    try:
        await update.message.reply_text(
            "🕵️‍♂️ Système anti-fraude actif : chaque preuve est contrôlée.\n"
            "❌ Les fausses preuves entraînent un blocage automatique."
        )
    except:
        pass

    # ------------------------------
    # Construction de la légende envoyée au support
    # ------------------------------
    base_caption = f"📩 Preuve reçue de l’utilisateur {user_id}"

    if site:
        base_caption += f"\n🌍 Site : {site}"

    now = datetime.now()
    base_caption += f"\n🕓 Reçu le {now.strftime('%d/%m/%Y à %Hh%M')}"

    # Texte utilisateur éventuel
    user_text = update.message.caption or update.message.text or ""
    if user_text:
        base_caption += f"\n\n🗒 <i>{user_text}</i>"

    # ------------------------------
    # Envoi au support (PHOTO / DOCUMENT / VIDEO / TEXTE)
    # ------------------------------
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        await context.bot.send_photo(
            chat_id=SUPPORT_CHAT_ID,
            photo=file_id,
            caption=base_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

    elif update.message.document:
        file_id = update.message.document.file_id
        await context.bot.send_document(
            chat_id=SUPPORT_CHAT_ID,
            document=file_id,
            caption=base_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

    elif update.message.video:
        file_id = update.message.video.file_id
        await context.bot.send_video(
            chat_id=SUPPORT_CHAT_ID,
            video=file_id,
            caption=base_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

    else:
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            text=base_caption,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

    # ------------------------------
    # Confirmation utilisateur
    # ------------------------------
    await update.message.reply_text(
        "✅ Merci ! Vos preuves ont été envoyées au support.\n"
        "⏳ Vous recevrez votre bonus après vérification."
    )

    # Nettoyage état bonus
    if site:
        context.user_data.pop("bonus", None)
        # ------------------------------
# Helpers : formatage FR + génération valeurs aléatoires
# ------------------------------

MONTHS_FR = [
    "Janvier", "Février", "Mars", "Avril", "Mai", "Juin",
    "Juillet", "Août", "Septembre", "Octobre", "Novembre", "Décembre"
]

def fr_datetime_now_str() -> str:
    """Retourne la date et l’heure actuelle au format français lisible."""
    now = datetime.now()
    jour = now.day
    mois = MONTHS_FR[now.month - 1]
    annee = now.year
    hh = f"{now.hour:02d}"
    mm = f"{now.minute:02d}"
    return f"{jour} {mois} {annee} à {hh}h{mm}"


# ✅ Génération d’un ID du type 4576****
def gen_mask() -> str:
    """Retourne un identifiant masqué du type 4576****."""
    chiffres = "".join(random.choice("0123456789") for _ in range(4))
    return f"{chiffres}****"


def rand_amount_first() -> int:
    """Montant aléatoire pour un premier retrait."""
    return random.choice([14000, 14500, 15000, 17000, 20000, 22000, 25000])


def rand_amount_next() -> int:
    """Montant aléatoire pour les retraits suivants."""
    return random.choice([500, 1000, 1500, 2000, 3000, 5000, 7000, 9000, 10000])


def build_retrait_caption(mask: str, montant: int, is_first: bool) -> str:
    """Construit la légende (caption) du message de retrait avec mise en forme HTML complète."""
    if is_first:
        header = "🔵 𝗘𝗻𝗰𝗼𝗿𝗲 𝗣𝗮𝗶𝗲𝗺𝗲𝗻𝘁 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 🔵"
        intro = f"🎉 Cet abonné vient d’obtenir son tout premier retrait de {montant} 𝗙𝗖𝗙𝗔 sur 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 !"
    else:
        header = "🔵 𝗘𝗻𝗰𝗼𝗿𝗲 𝗣𝗮𝗶𝗲𝗺𝗲𝗻𝘁 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 🔵"
        intro = f"💪 Cet abonné avait déjà effectué son premier retrait et vient encore d’encaisser {montant} 𝗙𝗖𝗙𝗔 sur 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 !"

    caption = (
        "<b><i>"
        f"{header}\n\n"
        f"{intro}\n"
        "-------------------------------------------------\n"
        "🔷 État : Payé ✅\n\n"
        f"🔷 𝗜𝗗 Bénéficiaire : {mask}\n\n"
        f"🔷 Montant Payé : {montant} 𝗙𝗖𝗙𝗔\n\n"
        f"📅 Date : {fr_datetime_now_str()}\n"
        "-------------------------------------------------\n"
        "🔷 Rien n’est magique, seul l’effort paye !\n"
        "Grâce à sa persévérance et à sa fidélité, cet abonné profite encore des avantages de 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 ✅\n"
        "-------------------------------------------------\n"
        "🔵𝗖𝗢𝗗𝗘 𝟭𝗫𝗕𝗘𝗧 :BUSS6 ou BAF8\n"
        "🟡𝗖𝗢𝗗𝗘 𝗠𝗘𝗟𝗕𝗘𝗧 :BUSS6\n"
        "🤖 @CashBet4_bot\n"
        "</i></b>"
    )
    return caption
    # ------------------------------
# ADMI : Flux “💸 Essaie de retrait”
# ------------------------------
async def admi_try_withdraw_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # 🔐 Sécurité support
    if q.from_user.id != SUPPORT_CHAT_ID:
        await q.message.reply_text("❌ Accès refusé — réservé au support.")
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔰 Premier retrait (≥ 14 000 FCFA)", callback_data="admi_try_choice:first")],
        [InlineKeyboardButton("♻️ Retrait suivant (≥ 500 FCFA)", callback_data="admi_try_choice:next")],
        [InlineKeyboardButton("↩️ Annuler", callback_data="admi_try_choice:cancel")]
    ])

    try:
        await q.edit_message_text("Choisis le type de retrait à simuler :", reply_markup=kb)
    except:
        await context.bot.send_message(
            chat_id=q.message.chat_id,
            text="Choisis le type de retrait à simuler :",
            reply_markup=kb
        )


async def admi_try_withdraw_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    # 🔐 Sécurité
    if q.from_user.id != SUPPORT_CHAT_ID:
        await q.message.reply_text("❌ Accès refusé — réservé au support.")
        return

    data = q.data

    # Annulation
    if data.endswith(":cancel"):
        try:
            await q.edit_message_text("❌ Opération annulée.")
        except:
            await q.message.reply_text("❌ Opération annulée.")
        return

    # Déterminer type retrait
    is_first = data.endswith(":first")
    mask = gen_mask()
    montant = rand_amount_first() if is_first else rand_amount_next()
    caption = build_retrait_caption(mask, montant, is_first)

    try:
        # 1️⃣ Envoi au support
        msg = await context.bot.send_photo(
            chat_id=SUPPORT_CHAT_ID,
            photo=LOGO_URL,
            caption=caption,
            parse_mode=ParseMode.HTML
        )

        # 2️⃣ Forward automatique vers le canal retraits
        await context.bot.forward_message(
            chat_id=CANAL_RETRAIT_ID,
            from_chat_id=SUPPORT_CHAT_ID,
            message_id=msg.message_id
        )

        # 📩 Confirmation support
        await q.message.reply_text("✅ Message créé et transféré dans le canal des retraits.")

        try:
            await q.edit_message_text("✅ Opération terminée.")
        except:
            pass

    except Exception as e:
        await q.message.reply_text(f"❌ Erreur en envoyant dans le canal : {e}")
        return
        # ------------------------------
# ADMI : FAUX MESSAGE BONUS 1XBET/MELBET
# ------------------------------

def gen_mask_digits() -> str:
    """Retourne un masque '5654****' (4 chiffres + '****')."""
    digits = "".join(random.choice("0123456789") for _ in range(4))
    return digits + "****"


async def admi_fake_bonus_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche au support un menu pour créer un faux message bonus à publier."""
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) != int(SUPPORT_CHAT_ID):
        try:
            await q.edit_message_text("<b><i>❌ Accès refusé — réservé au support.</i></b>", parse_mode=ParseMode.HTML)
        except:
            if q.message:
                await q.message.reply_text("<b><i>❌ Accès refusé — réservé au support.</i></b>", parse_mode=ParseMode.HTML)
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 2 000 FCFA", callback_data="admi_fake_bonus_send:2000")],
        [InlineKeyboardButton("🟢 4 500 FCFA", callback_data="admi_fake_bonus_send:4500")],
        [InlineKeyboardButton("🟢 10 000 FCFA", callback_data="admi_fake_bonus_send:10000")],
        [InlineKeyboardButton("🟢 20 500 FCFA", callback_data="admi_fake_bonus_send:20500")],
        [InlineKeyboardButton("↩️ Annuler", callback_data="admi_fake_bonus_cancel")]
    ])

    try:
        await q.edit_message_text(
            "<b><i>📝 Choisis le montant du faux bonus à publier :</i></b>",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
    except:
        # fallback si edit impossible (message supprimé ou autre)
        if q.message:
            await q.message.reply_text(
                "<b><i>📝 Choisis le montant du faux bonus à publier :</i></b>",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )


async def admi_fake_bonus_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envoie le faux message de bonus dans le canal."""
    q = update.callback_query
    await q.answer()
    if int(q.from_user.id) != int(SUPPORT_CHAT_ID):
        try:
            await q.edit_message_text("<b><i>❌ Accès refusé — réservé au support.</i></b>", parse_mode=ParseMode.HTML)
        except:
            if q.message:
                await q.message.reply_text("<b><i>❌ Accès refusé — réservé au support.</i></b>", parse_mode=ParseMode.HTML)
        return

    data = q.data
    if data.endswith(":cancel") or data == "admi_fake_bonus_cancel":
        try:
            await q.edit_message_text("<b><i>❌ Opération annulée.</i></b>", parse_mode=ParseMode.HTML)
        except:
            if q.message:
                await q.message.reply_text("<b><i>❌ Opération annulée.</i></b>", parse_mode=ParseMode.HTML)
        return

    try:
        _, montant_str = data.split(":", 1)
        montant = int(montant_str)
    except:
        montant = 2000

    masked = gen_mask_digits()

    text = (
        "<b><i>"
        "🔵 𝗕𝗢𝗡𝗨𝗦 𝟭𝗫𝗕𝗘𝗧 / 𝗠𝗘𝗟𝗕𝗘𝗧 🔵\n\n"
        "🎉 Félicitations ! Cet abonné vient de créer son compte en utilisant le code promo 👉 BUSS6 sur la plateforme de son choix (𝟭𝗫𝗕𝗘𝗧 ou 𝗠𝗘𝗟𝗕𝗘𝗧).\n"
        "-------------------------------------------------\n"
        f"💰 Après son dépôt, il reçoit un bonus exceptionnel retirable de {montant} 𝗙𝗖𝗙𝗔 sur son compte 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 💸🚀.\n"
        "Toi aussi, tu peux gagner jusqu'à 20 500 𝗙𝗖𝗙𝗔 en fonction du montant déposé sur ton compte 𝟭𝗫𝗕𝗘𝗧 ou 𝗠𝗘𝗟𝗕𝗘𝗧.\n"
        "-------------------------------------------------\n"
        "🔷 État : Réclamé / Validé ✅\n\n"
        f"🔷 ID Bénéficiaire : {masked}\n\n"
        "🔷 Bénéficiaire : Abonné fidèle\n\n"
        f"🔷 Montant Bonus : {montant} 𝗙𝗖𝗙𝗔\n\n"
        f"📅 Date : {fr_datetime_now_str()}\n"
        "-------------------------------------------------\n"
        "🔵𝗖𝗢𝗗𝗘 𝟭𝗫𝗕𝗘𝗧 : BUSS6 ou BAF8\n"
        "🟡𝗖𝗢𝗗𝗘 𝗠𝗘𝗟𝗕𝗘𝗧 : BUSS6\n"
        "🤖 @CashBet4_bot"
        "</i></b>"
    )

    try:
        # 1️⃣ Envoi au support (prévisualisation)
        fake_msg = await context.bot.send_photo(
            chat_id=SUPPORT_CHAT_ID,
            photo=IMAG_URL,
            caption=text,
            parse_mode=ParseMode.HTML
        )

        # 2️⃣ Forward vers le canal (tentative sécurisée)
        try:
            await context.bot.forward_message(
                chat_id=INFO_CHANNEL,
                from_chat_id=SUPPORT_CHAT_ID,
                message_id=fake_msg.message_id
            )
            note = "<b><i>✅ Faux message publié dans le canal Cash Bet4 Infos Bonus (avec transfert).</i></b>"
        except Exception as fe:
            note = f"<b><i>⚠️ Message envoyé au support mais échec du transfert : {fe}</i></b>"
            # log l'erreur si tu as un logger, sinon print
            print("[admi_fake_bonus_send forward error]", fe)

        try:
            await q.edit_message_text(note, parse_mode=ParseMode.HTML)
        except:
            if q.message:
                await q.message.reply_text(note, parse_mode=ParseMode.HTML)

    except Exception as e:
        # Erreur d'envoi initial
        print("[admi_fake_bonus_send error]", e)
        if q.message:
            await q.message.reply_text(f"<b><i>❌ Erreur : {e}</i></b>", parse_mode=ParseMode.HTML)
            # ------------------------------
# ADMI : Générer code mystère (PostgreSQL)
# ------------------------------

import string
from database import conn_cm


def generate_code(length=6):
    """Crée un code mystère unique avec préfixe BET4."""
    chars = string.ascii_uppercase + string.digits
    core = ''.join(random.choice(chars) for _ in range(length))
    return f"BET4-{core}"


async def admi_generate_code_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche les boutons 1 à 5 pour choisir combien de codes générer."""
    q = update.callback_query
    await q.answer()

    if q.from_user.id != SUPPORT_CHAT_ID:
        await q.edit_message_text("<b><i>❌ Accès refusé.</i></b>", parse_mode=ParseMode.HTML)
        return

    kb = [
        [InlineKeyboardButton(str(i), callback_data=f"admi_generate_code_count_{i}") for i in range(1, 6)],
        [InlineKeyboardButton("↩️ Annuler", callback_data="admi_generate_code_cancel")]
    ]

    await q.edit_message_text(
        "<b><i>🧩 Choisis combien de codes mystères générer :</i></b>",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode=ParseMode.HTML
    )


async def admi_generate_code_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Génère le nombre de codes choisis, enregistre en base et publie dans le canal infos."""
    q = update.callback_query
    await q.answer()

    if q.from_user.id != SUPPORT_CHAT_ID:
        await q.edit_message_text("<b><i>❌ Accès refusé.</i></b>", parse_mode=ParseMode.HTML)
        return

    data = q.data
    if data.endswith("_cancel"):
        await q.edit_message_text("<b><i>❌ Opération annulée.</i></b>", parse_mode=ParseMode.HTML)
        return

    try:
        count = int(data.split("_")[-1])
    except:
        await q.edit_message_text("<b><i>⚠️ Erreur de nombre.</i></b>", parse_mode=ParseMode.HTML)
        return

    codes = []
    now = datetime.now()
    expires = now + timedelta(minutes=5)

    # ------------------------------
    # 🔵 PostgreSQL : insert propre
    # ------------------------------
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            for _ in range(count):
                code = generate_code(6)
                codes.append(code)

                await cur.execute(
                    """
                    INSERT INTO codes_mystere (code, created_at, expires_at, used_count, max_uses)
                    VALUES (%s, %s, %s, 0, 10)
                    ON CONFLICT (code) DO UPDATE SET
                        created_at = EXCLUDED.created_at,
                        expires_at = EXCLUDED.expires_at,
                        used_count = 0,
                        max_uses = 10
                    """,
                    (code, now, expires)
                )

    # ------------------------------
    # Message à publier
    # ------------------------------

    text = (
        "<b><i>"
        "🔵 𝗙𝗟𝗔𝗦𝗛 𝗘𝗩𝗘𝗡𝗧 𝗖𝗔𝗦𝗛 𝗕𝗘𝗧𝟰 🔵\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "⚡ Les Codes Mystères viennent d’être lâchés !\n"
        "🎯 Essaie ta chance avant que le chrono s’éteigne…\n\n"
        "</i></b>"
        + "\n".join([f"<b><i>🔷 𝗖𝗼𝗱𝗲 👉</i></b> <code>{c}</code>" for c in codes]) +
        "\n\n"
        "<b><i>"
        "🔷 Durée : <u>Seulement 5 minutes !</u>\n"
        "🔷 Disponibles pour : <u>les 10 plus rapides</u>\n\n"
        "🔵 𝗖𝗢𝗗𝗘 𝟭𝗫𝗕𝗘𝗧 : BUSS6 ou BAF8\n"
        "🟡 𝗖𝗢𝗗𝗘 𝗠𝗘𝗟𝗕𝗘𝗧 : BUSS6\n"
        "🔥 Joue maintenant sur :\n"
        "👉 <a href='https://t.me/CashBet4_bot'>@CashBet4_bot</a>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "🎲 La chance ne frappe qu’une fois… saisis-la !"
        "</i></b>"
    )

    # ------------------------------
    # Envoi + transfert
    # ------------------------------
    try:
        sent = await context.bot.send_photo(
            chat_id=SUPPORT_CHAT_ID,
            photo=IMAC_URL,
            caption=text,
            parse_mode=ParseMode.HTML
        )

        await context.bot.forward_message(
            chat_id=CASH_BET4_INFOS,
            from_chat_id=sent.chat_id,
            message_id=sent.message_id
        )

        await q.edit_message_text(
            f"<b><i>✅ {count} code(s) mystère généré(s) et transféré(s) dans le canal infos.</i></b>",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        await q.edit_message_text(
            f"<b><i>⚠️ Erreur d’envoi : {e}</i></b>",
            parse_mode=ParseMode.HTML
        )
      # ------------------------------
# Menu (gestion principale)
# ------------------------------
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_chat.id)
    text = update.message.text

    # 👇 Vérifie si l'utilisateur est en train de saisir un code mystère
    if context.user_data.get("awaiting_code_mystere"):
        await process_code_mystere(update, context)
        return

    # 🔵 Charger l'utilisateur depuis PostgreSQL
    user = await get_user(user_id)  # version PostgreSQL
    if not user:
        await update.message.reply_text("⚠️ Tape /start pour commencer.")
        return

    # check_passed
    if not user["check_passed"]:
        await update.message.reply_text("❌ Clique sur ✅Check avant d’accéder au menu.")
        return

    # ------------------------------
    # ADMIN: saisie nouveau lien après "Remplacer"
    # ------------------------------
    if update.effective_user.id == SUPPORT_CHAT_ID and context.user_data.get("await_ch_replace_id"):
        cid = context.user_data.pop("await_ch_replace_id")
        new_value = (update.message.text or "").strip()
        try:
            # 🔵 Mise à jour PostgreSQL
            await set_channel_link_by_id(cid, new_value)

            rows = await get_required_channels_all()
            lab = next((r["label"] for r in rows if r["id"] == cid), None)

            # 🔵 Notifier tous les utilisateurs
            await notify_all_users_new_channel(context.bot, lab, new_value)

            await update.message.reply_text(
                f"✅ Lien du canal {lab} mis à jour et notification envoyée."
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Erreur : {e}")
        return

    # ------------------------------
    # ADMIN: saisie lors de "Ajouter"
    # ------------------------------
    if update.effective_user.id == SUPPORT_CHAT_ID and context.user_data.get("await_ch_add"):
        context.user_data.pop("await_ch_add")

        txt = (update.message.text or "").strip()
        try:
            parts = [p.strip() for p in txt.split("|")]
            if len(parts) >= 2:
                label = parts[0]
                candidate = parts[1]

                usr, url = _normalize_username_and_url(candidate)

                # 🔵 INSERT/UPDATE PostgreSQL
                await insert_or_update_channel(label, usr, url)

                # 🔵 Notifier tous les utilisateurs
                await notify_all_users_new_channel(context.bot, label, url)

                await update.message.reply_text(
                    f"✅ Canal ajouté/mis à jour : {label} ({url}). Notification envoyée."
                )
                return

        except Exception as e:
            await update.message.reply_text(f"❌ Erreur : {e}")
            return

        await update.message.reply_text(
            "❌ Format invalide. Exemple: `@CashBet4_Pub8 | @MonCanal`"
        )
        return
    # ------------------------------
    # Menu utilisateur principal
    # ------------------------------
    # columns: 0=user_id,1=solde,2=last_bonus,3=bonus_days,4=cycle_end_date,5=check_passed,
    # 6=welcome_bonus,7=parrain,8=bonus_claimed,9=bonus_message_id

    if "🔵Mon Solde💰" in text:
        solde_actuel = user[1] or 0
        msg = (
            f"💰 <b>Solde actuel :</b> {solde_actuel} 𝗙𝗖𝗙𝗔\n\n"
            "🌟 <b>Invitez et gagnez davantage !</b> 💸\n\n"
            "🔑 <b>Le retrait est possible à partir de :</b> 𝟭𝟰 𝟬𝟬𝟬𝗙𝗖𝗙𝗔 pour le premier retrait, "
            "puis dès 𝟱𝟬𝟬𝗙𝗖𝗙𝗔 les fois suivantes 🚀"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return

    if text == "🔵Historique📜":
        await historique(update, context)
        return

    if text == "🔵Parrainage👥":
        count = await get_filleuls_count(user_id)
        lien = f"https://t.me/{context.bot.username}?start={user_id}"
        msg = (
            "💼 <b>Voici ton lien de parrainage pour gagner avec 𝗖𝗮𝘀𝗵 𝗕𝗲𝘁𝟰 !</b> 💰⬇️\n\n"
            f"{lien}\n\n"
            f"🚀 <b>Nombre total d'invités :</b> {count} personne(s) 👥\n\n"
            "💵 <b>Tu gagnes 𝟱𝟬𝟬𝗙𝗖𝗙𝗔</b> pour chaque personne invitée ✅\n\n"
            "💼 <b>Tu peux demander un retrait à partir de 𝟭𝟰 𝟬𝟬𝟬𝗙𝗖𝗙𝗔 pour le premier,</b>\n"
            "et dès 𝟱𝟬𝟬𝗙𝗖𝗙𝗔 les fois suivantes 🚀"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return

    if text == "🔵Bonus 1XBET / MELBET🎁":
        await handle_bonus_choice(update, context)  # ouvre le nouveau menu
        return

    if text == "🔵Retrait💸":
        await send_withdraw_menu(update, context)
        return

    if text == "🔵Rejoindre canal d'infos📢":
        await update.message.reply_text(
            "🔗 Rejoins le canal d'infos ici👇: https://t.me/cashbet4infos"
           
        )
        return

    if text == "🔵Bonus 7j/7j🎁":
        today = datetime.now().date()
        last_bonus = user[2]
        bonus_days = user[3] or 0
        cycle_end_date = user[4]
        if cycle_end_date and today < datetime.strptime(cycle_end_date, "%Y-%m-%d").date():
            await update.message.reply_text(f"⏳ Cycle terminé. Nouveau cycle le {cycle_end_date}")
            return
        if last_bonus == str(today):
            await update.message.reply_text("⚠️ Bonus déjà réclamé aujourd'hui. 𝗥𝗲𝘃𝗲𝗻𝗲𝘇 𝗱𝗲𝗺𝗮𝗶𝗻 !")
            return
        await add_solde(user_id, 500, "Bonus Journalier")
        bonus_days += 1
        await update_user_field(user_id, "last_bonus", str(today))
        await update_user_field(user_id, "bonus_days", bonus_days)
        if bonus_days >= 7:
            new_cycle = today + timedelta(days=90)
            await update_user_field(user_id, "cycle_end_date", str(new_cycle))
            await update_user_field(user_id, "bonus_days", 0)
            await update.message.reply_text(f"🎉 Cycle 7 jours terminé ✅ Nouveau cycle le {new_cycle}")
        else:
            await update.message.reply_text(f"🎉 Bonus du jour : 500 𝗙𝗖𝗙𝗔 ✅ Progression : {bonus_days}/7")
        return

    if text == "🔵Ecrivez au Support pour vos préoccupations☎️":
        await update.message.reply_text("📞 Contacte le support👇 @telechargeur1")
        return

    if text == "🎟️ Code mystère":
        await update.message.reply_text("🎟️ Entre ici ton code mystère (exemple : BET4-XXXXXX) :")
        context.user_data["awaiting_code_mystere"] = True
        return
        
    if text == "🔵Cash Bet4 🔵":
        await update.message.reply_text(
        "📢 <b>Découvre toutes les informations officielles sur <u>Cash Bet4</u> ici :</b>\n\n"
        "👉 <a href='https://t.me/infocashbet4'>@CashBet4_Info</a>\n\n"
        "ℹ️ <i>Tu y trouveras le fonctionnement, les opportunités, les objectifs et toutes les actualités du projet.</i>",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )
        return
        
    if text == "🔵Pariez et gagnez sur PariBet4⚽":
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("➡️ Ouvrir PariBet4", url=PARIBET4_BOT_LINK)]])
        await update.message.reply_text(
            "🎯 Accédez à PariBet4 pour parier maintenant !",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb
        )
        return

    if text == "🔵ADMI💺":
        await admi_menu_from_message(update, context)
        return
        # =====================================================
# 🎟️ FONCTION : Vérification et utilisation du code mystère (PostgreSQL)
# =====================================================
async def process_code_mystere(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Vérifie et traite le code mystère envoyé par un utilisateur (version PostgreSQL)."""

    if not context.user_data.get("awaiting_code_mystere"):
        return

    context.user_data["awaiting_code_mystere"] = False

    code = (update.message.text or "").strip().upper()
    user_id = str(update.effective_chat.id)

    # 🔎 Lecture du code mystère dans PostgreSQL
    async with conn_cm() as conn:
        async with conn.cursor() as cur:

            await cur.execute(
                """
                SELECT created_at, expires_at, used_count, max_uses
                FROM codes_mystere
                WHERE code = %s
                """,
                (code,)
            )
            row = await cur.fetchone()

            if not row:
                await update.message.reply_text("❌ Ce code est invalide ou inexistant.")
                return

            created_at, expires_at, used_count, max_uses = row
            now = datetime.now()

            # ⏳ Expiration
            if expires_at < now:
                await update.message.reply_text("⏰ Ce code est déjà expiré ❌")
                return

            # 🚫 Trop d'utilisations
            if used_count >= max_uses:
                await update.message.reply_text("🚫 Ce code a déjà été utilisé par trop de personnes.")
                return

            # 🔐 Vérifier si cet utilisateur a déjà utilisé ce code
            await cur.execute(
                """
                SELECT 1 FROM codes_mystere_usage
                WHERE code = %s AND user_id = %s
                """,
                (code, user_id)
            )
            used = await cur.fetchone()

            if used:
                await update.message.reply_text("⚠️ Tu as déjà utilisé ce code mystère une fois.")
                return

            # 💰 Gain aléatoire attribué
            gain = random.choice([200, 500, 1000, 2000])
            await add_solde(user_id, gain, f"Gain Code Mystère {code}")

            # 📝 Enregistrer l'utilisation + mise à jour du compteur
            await cur.execute(
                """
                INSERT INTO codes_mystere_usage(code, user_id)
                VALUES (%s, %s)
                """,
                (code, user_id)
            )

            await cur.execute(
                """
                UPDATE codes_mystere
                SET used_count = used_count + 1
                WHERE code = %s
                """,
                (code,)
            )

    # 🎉 Réponse utilisateur
    await update.message.reply_text(
        f"🎉 <b>Félicitations ! Tu viens d'utiliser le code mystère {code} et gagnes {gain} FCFA</b> 💰",
        parse_mode=ParseMode.HTML
    )
    # ------------------------------
# ADMI : Gestion Blocages / Bannis (PostgreSQL)
# ------------------------------
async def admi_block_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != SUPPORT_CHAT_ID:
        await q.edit_message_text("❌ Accès refusé.")
        return

    now = datetime.now()
    text = "📋 <b>Gestion des blocages / bannis</b>\n\n"
    kb = []

    async with conn_cm() as conn:
        async with conn.cursor() as cur:

            # 🔐 Blocages temporaires
            await cur.execute("""
                SELECT user_id, blocked_until, fake_count
                FROM users
                WHERE blocked_until IS NOT NULL
            """)
            temp_rows = await cur.fetchall()

            # 🚫 Bannissements
            await cur.execute("""
                SELECT user_id, reason, date
                FROM banned_users
            """)
            ban_rows = await cur.fetchall()

    if not temp_rows and not ban_rows:
        text += "✅ Aucun utilisateur bloqué ni banni.\n"
        kb.append([InlineKeyboardButton("⬅️ Retour ADMI", callback_data="admi_main")])
        await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
        return

    # ---------------- Blocages temporaires ----------------
    if temp_rows:
        text += "⏳ <b>Blocages temporaires :</b>\n"

        for uid, until_dt, fake_count in temp_rows:
            try:
                if now < until_dt:
                    remain = until_dt - now
                    h = int(remain.total_seconds() // 3600)
                    m = int((remain.total_seconds() % 3600) // 60)
                    text += f"• <code>{uid}</code> → {h}h{m:02d} restantes (fausses preuves : {fake_count})\n"
                    kb.append([InlineKeyboardButton(f"🔓 Débloquer {uid}", callback_data=f"admi_unblock_{uid}")])
            except:
                pass

        text += "\n"

    # ---------------- Bannissements ----------------
    if ban_rows:
        text += "🚫 <b>Bannis définitifs :</b>\n"

        for uid, reason, date in ban_rows:
            text += f"• <code>{uid}</code> — {reason} ({date})\n"
            kb.append([InlineKeyboardButton(f"🔓 Débloquer {uid}", callback_data=f"admi_unblock_{uid}")])

        text += "\n"

    kb.append([InlineKeyboardButton("🧹 Tout débloquer", callback_data="admi_clear_all_blocked")])
    kb.append([InlineKeyboardButton("⬅️ Retour ADMI", callback_data="admi_main")])

    await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))


# ------------------------------
# ADMI : Débloquer un utilisateur
# ------------------------------
async def admi_unblock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != SUPPORT_CHAT_ID:
        return

    user_id = q.data.split("_")[-1]

    async with conn_cm() as conn:
        async with conn.cursor() as cur:

            await cur.execute("""
                UPDATE users
                SET blocked_until = NULL, fake_count = 0
                WHERE user_id = %s
            """, (user_id,))

            await cur.execute("""
                DELETE FROM banned_users
                WHERE user_id = %s
            """, (user_id,))

    # Notification utilisateur
    try:
        await context.bot.send_message(chat_id=user_id,
            text="✅ Votre compte a été débloqué par le support. Vous pouvez à nouveau envoyer des preuves.")
    except:
        pass

    await q.edit_message_text(f"✅ Utilisateur {user_id} débloqué avec succès.")
    await context.bot.send_message(SUPPORT_CHAT_ID, f"🔓 Déblocage effectué pour {user_id} ✅")


# ------------------------------
# ADMI : Tout débloquer
# ------------------------------
async def admi_clear_all_blocked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()

    if q.from_user.id != SUPPORT_CHAT_ID:
        await q.edit_message_text("❌ Accès refusé.")
        return

    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute("DELETE FROM banned_users")
            await cur.execute("UPDATE users SET blocked_until = NULL, fake_count = 0")

    await q.edit_message_text("🧹 Tous les utilisateurs ont été débloqués et les compteurs remis à zéro ✅")
    # ------------------------------
# Notifications globales lors ajout/remplacement canal (PostgreSQL)
# ------------------------------
async def notify_all_users_new_channel(bot, label: str, new_value: str):
    usr, url = _normalize_username_and_url(new_value)

    # Message stylé en HTML (plus propre que Markdown)
    text = (
        "<b>🔔 Nouveau canal obligatoire mis à jour !</b>\n\n"
        "Pour continuer à recevoir vos gains et bonus, rejoignez ce canal :\n"
        f"🔵 <a href='{url}'>{label}</a>\n\n"
        "<i>Merci de rester abonné(e) jusqu’à la validation de vos paiements.</i>"
    )

    # 🔍 Récupération de tous les utilisateurs en PostgreSQL
    async with conn_cm() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT user_id FROM users")
            rows = await cur.fetchall()

    # 🔔 Notification envoyée à chaque utilisateur
    for (uid,) in rows:
        try:
            await bot.send_message(
                chat_id=int(uid),
                text=text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
            await asyncio.sleep(0.03)  # anti-flood
        except Exception as e:
            print(f"[notify user {uid}] {e}")
            # =========================
# BONUS 1XBET / MELBET : menu + flux (clavier)
# =========================
from telegram.ext import ApplicationHandlerStop   # ✅ Ajout essentiel ici

def _kb_bonus_root():
    """Clavier du menu Bonus (racine)."""
    return ReplyKeyboardMarkup(
        [
            ["❓ Comment obtenir le bonus"],
            ["📤 Envoyer ma preuve de dépôt"],
            ["🔙 Retour"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def _kb_bonus_choose_site():
    """Sous-menu pour choisir le site avant d'envoyer la preuve."""
    return ReplyKeyboardMarkup(
        [
            ["🟦CHEZ 1XBET🟦", "🟨CHEZ MELBET🟨"],
            ["🔙 Retour"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
    )


async def send_bonus_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le menu Bonus 1XBET/MELBET."""
    context.user_data.pop("bonus", None)
    await update.message.reply_text(
        "🎁 Menu Bonus 1XBET / MELBET",
        reply_markup=_kb_bonus_root(),
    )
    raise ApplicationHandlerStop  # ✅ empêche la propagation (évite doublon)


async def handle_bonus_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère tout le flux Bonus (comment, envoyer, choix site, retour)."""
    text = (update.message.text or "").strip()
    user_id = str(update.effective_user.id)

    user = await get_user(user_id)
    if not user or not user[5]:
        return

    # ---- Retour vers menu principal
    if text == "🔙 Retour":
        context.user_data.pop("bonus", None)
        is_sup = (int(user_id) == int(SUPPORT_CHAT_ID))
        await update.message.reply_text(
            "🎛️ 𝗠𝗲𝗻𝘂 𝗽𝗿𝗶𝗻𝗰𝗶𝗽𝗮𝗹\n\n𝗩𝗼𝗶𝗰𝗶 𝘃𝗼𝘁𝗿𝗲 𝗺𝗲𝗻𝘂 𝗽𝗿𝗶𝗻𝗰𝗶𝗽𝗮𝗹👇 :",
            reply_markup=main_menu(is_sup),
        )
        raise ApplicationHandlerStop  # ✅ stop ici

    # ---- Ouverture du menu Bonus (depuis le bouton principal)
    if text == "🔵Bonus 1XBET / MELBET🎁":
        if user[8] == 1:
            await update.message.reply_text("⚠️ Vous avez déjà réclamé ce bonus.")
            raise ApplicationHandlerStop
        await send_bonus_menu(update, context)
        raise ApplicationHandlerStop

    # ---- Comment obtenir le bonus
    if text == "❓ Comment obtenir le bonus":
        if user[8] == 1:
            await update.message.reply_text(
                "<b><i>⚠️ Vous avez déjà réclamé ce bonus.</i></b>",
                parse_mode=ParseMode.HTML
            )
            raise ApplicationHandlerStop

        image_url = "https://files.catbox.moe/8g3nzc.jpg"
        caption = (
    "<b><i>🎁 𝗢𝗕𝗧𝗜𝗘𝗡𝗦 𝗧𝗢𝗡 𝗕𝗢𝗡𝗨𝗦 𝟭𝗫𝗕𝗘𝗧 / 𝗠𝗘𝗟𝗕𝗘𝗧 𝗘𝗡 𝟯 É𝗧𝗔𝗣𝗘𝗦 ⚡</i></b>\n\n"
    "<b><i>1️⃣ Inscris-toi sur ton site préféré avec le code promo :</i></b>\n"
    "🔵 <b><i>1XBET :</i></b> <b><i>BUSS6</i></b> <b><i>ou</i></b> <b><i>BAF8</i></b>\n"
    "🟡 <b><i>MELBET :</i></b> <b><i>BUSS6</i></b>\n\n"
    "<b><i>2️⃣ Fais un dépôt minimum de 1 000 FCFA sur ton compte joueur 💳</i></b>\n\n"
    "<b><i>3️⃣ Reviens ici et envoie :</i></b>\n"
    "📸 <b><i>Capture d’écran du dépôt</i></b>\n"
    "🆔 <b><i>ID joueur</i></b>\n"
    "🌍 <b><i>Nom du site (1XBET ou MELBET)</i></b>\n\n"
    "💼 <b><i>Après vérification par le support, ton bonus sera crédité selon ton dépôt 💰👇</i></b>\n\n"
    "💰 <b><i>1 000 FCFA ➜ BONUS 2 000 FCFA</i></b>\n"
    "💰 <b><i>2 000 FCFA ➜ BONUS 4 500 FCFA</i></b>\n"
    "💰 <b><i>5 000 FCFA ➜ BONUS 10 000 FCFA</i></b>\n"
    "💰 <b><i>10 000 FCFA ➜ BONUS 20 500 FCFA</i></b>\n\n"
    "⚙️ <b><i>Le support analysera ta preuve et créditera automatiquement ton solde.</i></b>\n"
    "🚀 <b><i>Chez Cash Bet4, chaque dépôt te rapproche de la victoire !</i></b>"
    )

        await update.message.reply_photo(
            photo=image_url,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
        await update.message.reply_text(
            "<b><i>Que souhaites-tu faire ?</i></b>",
            reply_markup=_kb_bonus_root(),
            parse_mode=ParseMode.HTML
        )
        raise ApplicationHandlerStop
        
# ---- Envoyer ma preuve -> choix du site
    if text == "📤 Envoyer ma preuve de dépôt":
        if user[8] == 1:
            await update.message.reply_text("⚠️ Vous avez déjà réclamé ce bonus.")
            raise ApplicationHandlerStop
        context.user_data["bonus"] = {"stage": "choose_site"}
        await update.message.reply_text(
            "Choisis d’abord la plateforme où tu t’es inscrit :",
            reply_markup=_kb_bonus_choose_site(),
        )
        raise ApplicationHandlerStop
        
    # ---- Choix de site
    if text in ("🟦CHEZ 1XBET🟦", "🟨CHEZ MELBET🟨"):
        st = context.user_data.get("bonus", {})
        st["stage"] = "await_proof"
        st["site"] = "1XBET" if "1XBET" in text else "MELBET"
        context.user_data["bonus"] = st

        await update.message.reply_text(
            "<b><i>"
            "Parfait ✅\n\n"
            "Envoie maintenant :\n"
            "• 📸 La capture d’écran du dépôt\n"
            "• 🆔 Ton ID joueur\n"
            "• 🌍 Le site (déjà choisi)\n\n"
            "Je transmettrai au support 😉"
            "</i></b>",
            parse_mode=ParseMode.HTML,
        )
        await update.message.reply_text(
            "<b><i>Quand tu es prêt(e), envoie ta preuve.</i></b>",
            reply_markup=_kb_bonus_choose_site(),
            parse_mode=ParseMode.HTML,
        )
        raise ApplicationHandlerStop

    # Sinon, on ne répond pas → ne rien casser
    return
    # =========================
# RETRAIT : menu + étapes (clavier) + validations indicatif & crypto
# =========================
from telegram.ext import ApplicationHandlerStop
from telegram import ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode

FIRST_WITHDRAW_MIN = 14000
INVITES_MIN = 22

# --- Indicateurs des pays et opérateurs disponibles ---
PREFIX_MOMO_AVAIL = {
    "229": ["MTN", "Moov"],                   # 🇧🇯 Bénin
    "225": ["MTN", "Moov", "Orange", "Wave"], # 🇨🇮 Côte d’Ivoire
    "221": ["Orange", "Wave"],                # 🇸🇳 Sénégal
    "227": ["Airtel", "Moov"],                # 🇳🇪 Niger
    "228": ["Moov"],                          # 🇹🇬 Togo
    "226": ["Moov", "Orange"],                # 🇧🇫 Burkina Faso
    "243": ["Airtel", "Orange"],              # 🇨🇩 RDC
    "242": ["MTN", "Airtel"],                 # 🇨🇬 Congo Brazzaville
    "233": ["MTN"],                           # 🇬🇭 Ghana
    "237": ["MTN", "Orange"],                 # 🇨🇲 Cameroun
    "241": ["Airtel"],                        # 🇬🇦 Gabon
    "236": ["Orange"],                        # 🇨🇫 Centrafrique
    "235": ["Airtel"],                        # 🇹🇩 Tchad
    "224": ["MTN", "Orange"],                 # 🇬🇳 Guinée
    "223": ["Orange", "Moov"],                # 🇲🇱 Mali
    "234": ["Airtel", "MTN"],                 # 🇳🇬 Nigéria
    "250": ["MTN"],                           # 🇷🇼 Rwanda
    "256": ["MTN", "Airtel"],                 # 🇺🇬 Ouganda
    "255": ["Airtel", "MTN"],                 # 🇹🇿 Tanzanie
    "260": ["Airtel", "MTN"],                 # 🇿🇲 Zambie
    "265": ["Airtel"],                        # 🇲🇼 Malawi
    "232": ["Orange"],                        # 🇸🇱 Sierra Leone
    "231": ["Orange"],                        # 🇱🇷 Libéria
    "258": ["Airtel", "MTN"],                 # 🇲🇿 Mozambique
    "27":  ["MTN"],                           # 🇿🇦 Afrique du Sud
    "254": ["Airtel"],                        # 🇰🇪 Kenya
}

# --- Dictionnaire indicatif → pays ---
PREFIX_TO_COUNTRY = {
    "229": "Bénin",
    "225": "Côte d’Ivoire",
    "221": "Sénégal",
    "227": "Niger",
    "228": "Togo",
    "226": "Burkina Faso",
    "243": "RDC",
    "242": "Congo Brazzaville",
    "233": "Ghana",
    "237": "Cameroun",
    "241": "Gabon",
    "236": "Centrafrique",
    "235": "Tchad",
    "224": "Guinée",
    "223": "Mali",
    "234": "Nigéria",
    "250": "Rwanda",
    "256": "Ouganda",
    "255": "Tanzanie",
    "260": "Zambie",
    "265": "Malawi",
    "232": "Sierra Leone",
    "231": "Libéria",
    "258": "Mozambique",
    "27":  "Afrique du Sud",
    "254": "Kenya",
}

# ---- Réseaux crypto pris en charge
ALLOWED_CRYPTO_NETWORKS = {"TRC20", "USDT-TRC20", "TRON", "BTC"}

# ---- Map étiquette bouton -> opérateur pour vérification
METHOD_TO_OPERATOR = {
    "🟡MTN Money": "MTN",
    "🔵Moov Money": "Moov",
    "⚪Wave": "Wave",
    "🔴Airtel money": "Airtel",
    "🟠Orange money": "Orange",
}

def _kb_withdraw_root():
    return ReplyKeyboardMarkup(
        [
            ["🟡MTN Money", "🔵Moov Money"],
            ["⚪Wave", "🟣Crypto"],
            ["🔴Airtel money", "🟠Orange money"],
            ["🔙 Retour"],
        ],
        resize_keyboard=True,
    )

def _kb_cancel_only():
    return ReplyKeyboardMarkup([["❌ Annuler"]], resize_keyboard=True)

def _extract_phone_info(raw: str):
    s = "".join(ch for ch in raw if ch.isdigit() or ch == "+")
    if not s.startswith("+"):
        return None, None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) < 4:
        return None, None
    prefix = digits[:3]
    local = digits[3:]
    return prefix, local

def _validate_phone_for_method(raw: str, method_label: str):
    op = METHOD_TO_OPERATOR.get(method_label)
    if not op:
        return False, "❌ Moyen non reconnu. Réessaie."

    prefix, local = _extract_phone_info(raw)
    if not prefix:
        return False, "❌ Format invalide. Utilise par ex. <b>+22507000000</b>."

    if prefix not in PREFIX_MOMO_AVAIL:
        return False, f"❌ Indicatif <b>+{prefix}</b> non supporté pour le retrait Mobile Money."

    if op not in PREFIX_MOMO_AVAIL[prefix]:
        country = PREFIX_TO_COUNTRY.get(prefix, f"+{prefix}")
        return False, f"❌ Le moyen <b>{op}</b> n’est pas disponible pour  <b>{country}</b>."

    if len(local) < 8:
        return False, "❌ Le numéro doit contenir au moins <b>8 chiffres</b> après l’indicatif."

    return True, None

def _validate_crypto_input(raw: str):
    if ":" not in raw:
        return False, None, None, "❌ Format invalide. Exemple: <b>TRC20: TBa1c...XYZ</b>"

    net, addr = raw.split(":", 1)
    net = net.strip().upper()
    addr = addr.strip()
    if net == "USDT":
        net = "USDT-TRC20"

    if net not in ALLOWED_CRYPTO_NETWORKS:
        nets = ", ".join(sorted(ALLOWED_CRYPTO_NETWORKS))
        return False, None, None, f"❌ Réseau non pris en charge. Réseaux valides: <b>{nets}</b>."

    if len(addr) < 12:
        return False, None, None, "❌ Adresse trop courte. Vérifie et renvoie: <b>RÉSEAU: adresse</b>."

    return True, net, addr, None

async def send_withdraw_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche le menu des moyens de retrait + bouton 🔙 Retour."""
    context.user_data.pop("wd", None)
    await update.message.reply_text(
        "💸 Choisis un moyen de retrait :",
        reply_markup=_kb_withdraw_root(),
    )
    raise ApplicationHandlerStop

async def handle_withdraw_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gère tout le flux Retrait (choix moyen, saisies, validations)."""
    text = (update.message.text or "").strip()
    user_id = str(update.effective_user.id)

    # --- Navigation / annulation ---
    if text == "🔙 Retour":
        context.user_data.pop("wd", None)
        is_support = (int(user_id) == int(SUPPORT_CHAT_ID))
        await update.message.reply_text(
            "🎛️ 𝗠𝗲𝗻𝘂 𝗽𝗿𝗶𝗻𝗰𝗶𝗽𝗮𝗹\n\n𝗩𝗼𝗶𝗰𝗶 𝘃𝗼𝘁𝗿𝗲 𝗺𝗲𝗻𝘂 𝗽𝗿𝗶𝗻𝗰𝗶𝗽𝗮𝗹👇 :",
            reply_markup=main_menu(is_support),
        )
        raise ApplicationHandlerStop

    if text == "❌ Annuler":
        context.user_data.pop("wd", None)
        await update.message.reply_text(
            "❌ Retrait annulé.",
            reply_markup=_kb_withdraw_root(),
        )
        raise ApplicationHandlerStop

    # --- Démarrage d'un parcours (choix du moyen) ---
    if text in ("🟡MTN Money", "🔵Moov Money", "⚪Wave", "🟣Crypto", "🔴Airtel money", "🟠Orange money"):
        wd = {"method": text}
        if text == "🟣Crypto":
            wd["stage"] = "crypto_addr"
            context.user_data["wd"] = wd
            await update.message.reply_text(
                "🪙 Indique ton <b>réseau</b> et ton <b>adresse</b> au format:\n"
                "<b>TRC20: TBa1c...XYZ</b>\n\n"
                "✅ Réseaux acceptés: <b>TRC20, USDT-TRC20, TRON, BTC</b>.\n"
                "⚠️ <i>Vérifie bien ton adresse. Une erreur peut entraîner la perte définitive des fonds.</i>",
                parse_mode=ParseMode.HTML,
                reply_markup=_kb_cancel_only(),
            )
        else:
            wd["stage"] = "phone"
            context.user_data["wd"] = wd
            label = {
                "🟡MTN Money": "MTN",
                "🔵Moov Money": "Moov",
                "⚪Wave": "Wave",
                "🔴Airtel money": "Airtel",
                "🟠Orange money": "Orange"
            }[text]
            await update.message.reply_text(
                "📱 Envoie ton numéro au format <b>+CCCXXXXXXXX</b> (ex: <b>+22997989898</b>).\n"
                "⚠️ <i>Entre correctement ton numéro</i> sinon <b>tes gains peuvent être envoyés à un autre numéro</b> et tu perdras ton argent.\n"
                f"ℹ️ Opérateur choisi: <b>{label}</b>.",
                parse_mode=ParseMode.HTML,
                reply_markup=_kb_cancel_only(),
            )
        raise ApplicationHandlerStop

    # --- Si un parcours est en cours, on traite la saisie ---
    wd = context.user_data.get("wd")
    if not wd or "stage" not in wd:
        return  # pas un message du parcours

    # 1️⃣ Saisie du numéro (Mobile Money)
    if wd["stage"] == "phone":
        ok, err = _validate_phone_for_method(text, wd["method"])
        if not ok:
            await update.message.reply_text(err, parse_mode=ParseMode.HTML, reply_markup=_kb_cancel_only())
            raise ApplicationHandlerStop

        wd["phone"] = text
        wd["stage"] = "amount"
        await update.message.reply_text(
            "💰 Envoie maintenant le <b>montant à retirer</b> (FCFA) :",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_cancel_only(),
        )
        raise ApplicationHandlerStop

    # 2️⃣ Saisie crypto
    if wd["stage"] == "crypto_addr":
        ok, net, addr, err = _validate_crypto_input(text)
        if not ok:
            await update.message.reply_text(err, parse_mode=ParseMode.HTML, reply_markup=_kb_cancel_only())
            raise ApplicationHandlerStop
        wd["crypto_network"] = net
        wd["crypto_addr"] = addr
        wd["stage"] = "amount"
        await update.message.reply_text(
            "💰 Envoie le <b>montant à retirer</b> (FCFA) :",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb_cancel_only(),
        )
        raise ApplicationHandlerStop

    # 3️⃣ Saisie du montant
    if wd["stage"] == "amount":
        try:
            amount = int(text.replace(" ", ""))
            if amount <= 0:
                raise ValueError()
        except ValueError:
            await update.message.reply_text(
                "❌ Montant invalide. Exemple : <b>15000</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=_kb_cancel_only(),
            )
            raise ApplicationHandlerStop

        user = await get_user(user_id)
        solde = user[1] or 0
        invites = await get_filleuls_count(user_id)
        bonus_claimed_flag = user[8]
        has_withdrawn = user[10] if len(user) > 10 else 0  # ✅ Sécurité

        # a) Solde insuffisant
        if solde < amount:
            await update.message.reply_text(
                f"❌ Ton solde ({solde} FCFA) est insuffisant pour retirer {amount} FCFA.\n"
                "Continue les tâches pour gagner plus 💪",
                reply_markup=_kb_withdraw_root(),
            )
            context.user_data.pop("wd", None)
            raise ApplicationHandlerStop

        # b) Premier retrait ≥ 14 000 FCFA
        if has_withdrawn == 0 and amount < FIRST_WITHDRAW_MIN:
            await update.message.reply_text(
                f"❌ Premier retrait à partir de {FIRST_WITHDRAW_MIN} FCFA.\n"
                f"Tu as demandé : {amount} FCFA.",
                reply_markup=_kb_withdraw_root(),
            )
            context.user_data.pop("wd", None)
            raise ApplicationHandlerStop

        # c) Bonus obligatoire
        if bonus_claimed_flag == 0:
            await update.message.reply_text(
                "⚠️ Tu dois d'abord réclamer ton bonus 1XBET/MELBET pour pouvoir retirer tes gains.",
                reply_markup=_kb_withdraw_root(),
            )
            context.user_data.pop("wd", None)
            raise ApplicationHandlerStop

        # d) 22 invités requis
        if invites < INVITES_MIN:
            restant = INVITES_MIN - invites
            await update.message.reply_text(
                f"⚠️ Il te manque encore {restant} personne(s) pour atteindre les {INVITES_MIN} invités requis pour retirer.",
                reply_markup=_kb_withdraw_root(),
            )
            context.user_data.pop("wd", None)
            raise ApplicationHandlerStop

        # ✅ Succès → notifier le support avec boutons validation
    method = wd["method"]
    summary = (
        "🆕 <b>Demande de retrait</b>\n"
        f"👤 <b>User :</b> <code>{user_id}</code>\n"
        f"💰 <b>Montant :</b> {amount} FCFA\n"
        f"🏦 <b>Méthode :</b> {method}\n"
    )
    if method == "🟣Crypto":
        summary += f"🌐 <b>Réseau :</b> {wd.get('crypto_network','—')}\n"
        summary += f"🏷️ <b>Adresse :</b> <code>{wd.get('crypto_addr','—')}</code>\n"
    else:
        summary += f"📱 <b>Numéro :</b> <code>{wd.get('phone','—')}</code>\n"

    # 💾 Déduction du solde et marquage retrait
    try:
        new_solde = solde - amount
        await update_user_solde(user_id, new_solde)
        await mark_user_withdrawn(user_id)
    except Exception as e:
        print(f"[withdraw update solde] {e}")

    # 🔘 Boutons pour le support
    kb_support = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Retrait validé", callback_data=f"validate_withdraw:{user_id}:{amount}"),
            InlineKeyboardButton("❌ Retrait rejeté", callback_data=f"reject_withdraw:{user_id}:{amount}")
        ]
    ])

    # 📩 Envoi au support
    try:
        await context.bot.send_message(
            chat_id=SUPPORT_CHAT_ID,
            text=summary,
            parse_mode=ParseMode.HTML,
            reply_markup=kb_support
        )
    except Exception as e:
        print(f"[withdraw notify support] {e}")
        await update.message.reply_text(
            "⚠️ Erreur : impossible de contacter le support pour le moment. Réessaie dans quelques minutes.",
            reply_markup=_kb_withdraw_root(),
        )
        raise ApplicationHandlerStop

    # ✅ Message utilisateur : statut “en attente” (qu’on pourra supprimer après validation)
    pending_msg = await update.message.reply_text(
        f"⏳ <b>Statut :</b> Retrait en attente\n\n"
        f"💵 <b>Montant :</b> {amount} FCFA\n"
        f"🏦 <b>Méthode :</b> {method}\n"
        f"📱 <b>Numéro :</b> {wd.get('phone','—')}\n\n"
        "🔔 Le support confirmera dès que possible ✅",
        parse_mode=ParseMode.HTML,
        reply_markup=_kb_withdraw_root(),
    )

    # 🔖 On stocke le message d’attente pour pouvoir le supprimer après (validation/rejet)
    context.user_data["pending_withdraw_msg_id"] = pending_msg.message_id
    context.user_data.pop("wd", None)
    raise ApplicationHandlerStop
    # =========================
# CALLBACKS SUPPORT : validation ou rejet retrait
# =========================
async def support_withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    try:
        action, user_id, amount = data.split(":")
        amount = int(amount)
    except ValueError:
        return

    if q.from_user.id != SUPPORT_CHAT_ID:
        await q.edit_message_text("❌ Accès refusé (non support).")
        return

    # 🔄 Supprime le message “retrait en attente” de l’utilisateur
    try:
        msg_id = context.user_data.get("pending_withdraw_msg_id")
        if msg_id:
            await context.bot.delete_message(chat_id=int(user_id), message_id=msg_id)
    except Exception as e:
        print(f"[delete pending withdraw msg] {e}")

    # ✅ RETRAIT VALIDÉ
    if action == "validate_withdraw":
        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                f"✅ <b>Retrait validé !</b>\n\n"
                f"💰 <b>Montant :</b> {amount} FCFA\n"
                f"📱 <b>Crédité sur ton numéro indiqué.</b>\n"
                "Merci d’avoir utilisé <b>Cash Bet4</b> 💙"
            ),
            parse_mode=ParseMode.HTML
        )
        await q.edit_message_text(
            f"✅ Retrait validé pour l’utilisateur : <code>{user_id}</code>\nMontant : {amount} FCFA",
            parse_mode=ParseMode.HTML
        )

    # ❌ RETRAIT REJETÉ
    elif action == "reject_withdraw":
        # ⚠️ Remettre l’argent dans le solde utilisateur
        user = await get_user(user_id)
        solde = user[1] or 0
        new_solde = solde + amount
        await update_user_solde(user_id, new_solde)

        await context.bot.send_message(
            chat_id=int(user_id),
            text=(
                "❌ <b>Retrait rejeté</b>\n\n"
                "Les informations fournies ne sont pas correctes.\n"
                "Vérifie ton numéro ou ta méthode et réessaie 🔁"
            ),
            parse_mode=ParseMode.HTML
        )
        await q.edit_message_text(
            f"❌ Retrait rejeté pour l’utilisateur : <code>{user_id}</code>",
            parse_mode=ParseMode.HTML
        )
        # =========================
# 🔮 Code magique de fiabilisation Cash Bet4 — Version PostgreSQL
# =========================
from datetime import datetime
import asyncio
from database import conn_cm   # ✅ ton context manager psycopg3

async def magic_channels_sanity_check():
    """
    Vérifie et répare automatiquement la table des canaux obligatoires
    (version PostgreSQL).
    """
    print("🔮 Vérification magique des canaux Cash Bet4...")

    async with conn_cm() as conn:
        async with conn.cursor() as cur:

            # 1️⃣ Créer la table si elle n'existe pas
            await cur.execute("""
                CREATE TABLE IF NOT EXISTS required_channels (
                    id SERIAL PRIMARY KEY,
                    label TEXT UNIQUE,
                    username TEXT,
                    url TEXT,
                    public_username TEXT,
                    private_link TEXT
                );
            """)

            # 2️⃣ Récupérer les lignes existantes
            await cur.execute("SELECT id, url, username FROM required_channels;")
            rows = await cur.fetchall()

            for cid, url, username in rows:

                # 🔹 Ignorer lignes totalement vides
                if not url and not username:
                    continue

                # ---- A : Correction du username manquant
                if (not username) and url:
                    usr = (
                        url.replace("https://t.me/", "")
                           .lstrip("@")
                           .split("?")[0]
                    )

                    # Ignorer si lien spécial (invitation par numéro)
                    if not usr.startswith("+"):
                        await cur.execute(
                            "UPDATE required_channels SET username=%s WHERE id=%s;",
                            (usr, cid)
                        )

                # ---- B : Corriger les liens privés mal placés
                if url and "https://t.me/+" in url:
                    usr = username or url.replace("https://t.me/", "").lstrip("@")
                    clean = f"https://t.me/{usr}"
                    await cur.execute(
                        "UPDATE required_channels SET url=%s WHERE id=%s;",
                        (clean, cid)
                    )

        await conn.commit()

    print("✅ Vérification magique terminée — base PostgreSQL prête à l’emploi !")
    # ---------- début : main() mis à jour pour Railway + PostgreSQL ----------
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, filters
from database import init_db, init_channels_db

async def main():
    # ⭐️ INITIALISATION POSTGRESQL
    await init_db()              # Initialise DB principale
    await init_channels_db()     # Initialise DB des canaux

    # 🔮 Vérification magique des canaux (version PG)
    await magic_channels_sanity_check()

    app = ApplicationBuilder().token(TOKEN).build()

    # === COMMANDES DE BASE ===
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("historique", historique))

    # === COMMANDES SUPPORT ===
    app.add_handler(CommandHandler("warn", cmd_warn))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unblock", cmd_unblock))
    app.add_handler(CommandHandler("listblocked", cmd_listblocked))
    app.add_handler(CommandHandler("clearblocked", cmd_clearblocked))

    # === CALLBACKS GÉNÉRAUX ===
    app.add_handler(CallbackQueryHandler(check_channels, pattern=r"^check_channels$"))
    app.add_handler(CallbackQueryHandler(show_menu_callback, pattern=r"^show_menu$"))

    # === HANDLERS DE PREUVE (photo/document) ===
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, preuve_handler))

    # === ACTIONS SUPPORT BONUS ===
    app.add_handler(CallbackQueryHandler(support_callback, pattern=r"^bonus_"))
    app.add_handler(CallbackQueryHandler(support_callback, pattern=r"^rejeter_"))

    # === CALLBACK VALIDATION/REJET RETRAIT ===
    app.add_handler(CallbackQueryHandler(support_withdraw_callback, pattern=r"^(validate_withdraw|reject_withdraw):"))

    # === FORWARD ===
    app.add_handler(CallbackQueryHandler(forward_callback, pattern=r"^forward_"))

    # === RESET ===
    app.add_handler(CallbackQueryHandler(reset_callback, pattern=r"^reset_daily_"))
    app.add_handler(CallbackQueryHandler(reset_callback, pattern=r"^reset_1xbet_"))

    # === ADMI MENU PRINCIPAL ===
    app.add_handler(CallbackQueryHandler(
        admi_menu_callback,
        pattern=r"^admi_(?:main|warn|remove|ban|back_to_main|users_\d+)$"
    ))

    # === GESTION DES CANAUX ===
    app.add_handler(CallbackQueryHandler(
        admi_channels_callback,
        pattern=r"^(?:admi_channels|admi_ch_replace_\d+|admi_ch_delete_\d+|admi_ch_add)$"
    ))

    # === RETRAIT ===
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_withdraw_choice), group=0)

    # === BONUS 1XBET/MELBET ===
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_bonus_choice), group=1)

    # === HANDLER SUPPORT ===
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admi_text_handler), group=2)

    # === MENU UTILISATEUR ===
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu), group=3)

    # === BLOQUAGE ===
    app.add_handler(CallbackQueryHandler(admi_block_menu, pattern=r"^admi_block_menu$"))
    app.add_handler(CallbackQueryHandler(admi_unblock_user, pattern=r"^admi_unblock_\d+$"))
    app.add_handler(CallbackQueryHandler(admi_clear_all_blocked, pattern=r"^admi_clear_all_blocked$"))

    # === ESSAIE DE RETRAIT ===
    app.add_handler(CallbackQueryHandler(admi_try_withdraw_prompt, pattern=r"^admi_try_withdraw$"))
    app.add_handler(CallbackQueryHandler(admi_try_withdraw_choice, pattern=r"^admi_try_choice:(?:first|next|cancel)$"))

    # === FAUX BONUS / CODES MYSTÈRE ===
    app.add_handler(CallbackQueryHandler(admi_fake_bonus_prompt, pattern=r"^admi_fake_bonus$"))
    app.add_handler(CallbackQueryHandler(admi_fake_bonus_send, pattern=r"^admi_fake_bonus_send:\d+$"))
    app.add_handler(CallbackQueryHandler(admi_fake_bonus_send, pattern=r"^admi_fake_bonus_cancel$"))
    app.add_handler(CallbackQueryHandler(admi_generate_code_prompt, pattern=r"^admi_generate_code$"))
    app.add_handler(CallbackQueryHandler(admi_generate_code_count, pattern=r"^admi_generate_code_count_\d+$|^admi_generate_code_cancel$"))


    # === VÉRIFICATION RÉGULIÈRE DES CANAUX ===
    asyncio.create_task(periodic_channel_check(app))

    print("🤖 Cash_Bet4 (PostgreSQL / Railway) prêt à fonctionner...")

    await app.run_polling()


# ---------- fin main() -------------------------------------

# ---------- lancement Railway ----------
if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
