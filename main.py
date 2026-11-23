"""
====================================================================
🤖 MULTI-INSTANCE BOT CLONER (Persistent Setup & Dynamic Cloners)
====================================================================
"""

import os
import json
import time
import asyncio
import threading
import re
import secrets
from html import escape as esc

import telebot
from telebot import types
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError
)
from telethon.tl.types import (
    ReplyKeyboardMarkup,
    ReplyInlineMarkup
)

# ============================ CONFIG ============================
MASTER_SETUP_PASS    = "pardhanji"
RESPONSE_TIMEOUT     = 30

# 👉 Setup Bot Hamesha Live Rahega (Fixed Hardcoded Token)
SETUP_BOT_TOKEN      = "9105187750:AAEIzytveC0P60gnGYqS-cs6eRaCuhwRh1c"

DATA_DIR             = "bots_data"
os.makedirs(DATA_DIR, exist_ok=True)

# Active engines & threads tracker
RUNNING_CLONES = {}  # token: {"engine": MasterCloneEngine, "bot": telebot.TeleBot}


# ===================== TEXT FILTER =====================
def apply_branding_filter(text, replacements):
    if not text or not replacements:
        return text

    modified_text = text
    for old_val, new_val in replacements.items():
        if old_val and new_val is not None:
            pattern = re.compile(re.escape(old_val), re.IGNORECASE)
            modified_text = pattern.sub(new_val, modified_text)
            
    return modified_text


# ====================== CLONE ENGINE ======================
class MasterCloneEngine:
    def __init__(self, api_id, api_hash, session_str, target):
        self.api_id      = int(api_id)
        self.api_hash    = api_hash
        self.session_str = session_str
        t = target.strip()
        self.target      = t if t.startswith("@") else "@" + t

        self.loop      = None
        self.client    = None
        self.event     = None
        self.last_msg  = None
        self.error     = None
        self.send_lock = threading.Lock()
        self._ready    = threading.Event()

    def start(self):
        th = threading.Thread(target=self._run, daemon=True)
        th.start()
        self._ready.wait(timeout=25)
        return self.error is None

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.event = asyncio.Event()
        self.client = TelegramClient(
            StringSession(self.session_str),
            self.api_id, self.api_hash, loop=self.loop
        )
        try:
            self.loop.run_until_complete(self.client.connect())
            if not self.loop.run_until_complete(self.client.is_user_authorized()):
                raise RuntimeError("Invalid or expired session string.")
            self._register_handlers()
            self._ready.set()
            self.loop.run_forever()
        except Exception as e:
            self.error = str(e)
            self._ready.set()
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    def _register_handlers(self):
        @self.client.on(events.NewMessage(chats=self.target))
        @self.client.on(events.MessageEdited(chats=self.target))
        async def _handler(event):
            if event.out:
                return
            msg = event.message
            text = (msg.text or msg.caption or "").strip()
            low = text.lower()

            if not msg.reply_markup and any(k in low for k in ("loading", "fetching", "processing", "please wait", "...")):
                return
            if not msg.reply_markup and not text and not msg.media:
                return

            self.last_msg = msg
            self.event.set()

    async def _send_and_wait(self, text):
        self.event.clear()
        self.last_msg = None
        await self.client.send_message(self.target, text)
        await asyncio.wait_for(self.event.wait(), RESPONSE_TIMEOUT)
        return self.last_msg

    async def _click_and_wait(self, row, col):
        msg = self.last_msg
        if not msg or not msg.buttons:
            return None
        self.event.clear()
        try:
            btn = msg.buttons[row][col]
            if getattr(btn, "url", None):
                return None
            await btn.click()
        except Exception:
            try:
                btn_text = msg.buttons[row][col].text
                await self.client.send_message(self.target, btn_text)
            except Exception:
                return None

        await asyncio.wait_for(self.event.wait(), RESPONSE_TIMEOUT)
        return self.last_msg

    def send_text(self, text):
        with self.send_lock:
            fut = asyncio.run_coroutine_threadsafe(self._send_and_wait(text), self.loop)
            return fut.result(timeout=RESPONSE_TIMEOUT + 10)

    def click_button(self, row, col):
        with self.send_lock:
            fut = asyncio.run_coroutine_threadsafe(self._click_and_wait(row, col), self.loop)
            return fut.result(timeout=RESPONSE_TIMEOUT + 10)

    def stop(self):
        try:
            fut = asyncio.run_coroutine_threadsafe(self.client.disconnect(), self.loop)
            fut.result(timeout=10)
        except Exception:
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass


# ========================= UI BUILDERS =========================
def parse_keyboards(tmsg, replacements):
    if not tmsg or not tmsg.reply_markup:
        return None, None

    markup_raw = tmsg.reply_markup

    if isinstance(markup_raw, ReplyKeyboardMarkup):
        reply_kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
        if hasattr(tmsg, "buttons") and tmsg.buttons:
            for row in tmsg.buttons:
                row_btns = [types.KeyboardButton(apply_branding_filter(b.text, replacements)) for b in row]
                reply_kb.row(*row_btns)
            return None, reply_kb

    if isinstance(markup_raw, ReplyInlineMarkup):
        inline_kb = types.InlineKeyboardMarkup()
        if hasattr(tmsg, "buttons") and tmsg.buttons:
            for r, row in enumerate(tmsg.buttons):
                row_btns = []
                for c, b in enumerate(row):
                    url = getattr(b, "url", None)
                    btn_text = apply_branding_filter(b.text, replacements)
                    if url:
                        custom_url = apply_branding_filter(url, replacements)
                        row_btns.append(types.InlineKeyboardButton(btn_text, url=custom_url))
                    else:
                        row_btns.append(types.InlineKeyboardButton(btn_text, callback_data=f"clk:{r}:{c}"))
                inline_kb.row(*row_btns)
            return inline_kb, None

    return None, None


def relay_to_user(bot_instance, chat_id, tmsg, replacements):
    raw_text = (tmsg.text or tmsg.caption or "").strip()

    if tmsg.media and not raw_text:
        raw_text = "📎 Media content received."
    if not raw_text:
        raw_text = "✅ Done"

    clean_text = apply_branding_filter(raw_text, replacements)
    inline_markup, reply_markup = parse_keyboards(tmsg, replacements)
    active_markup = inline_markup or reply_markup

    def safe_send(content, markup=None):
        try:
            bot_instance.send_message(chat_id, content, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            try:
                bot_instance.send_message(chat_id, esc(content), reply_markup=markup, parse_mode="HTML")
            except Exception:
                bot_instance.send_message(chat_id, content, reply_markup=markup, parse_mode=None)

    if len(clean_text) > 4000:
        for i in range(0, len(clean_text), 4000):
            part = clean_text[i:i + 4000]
            if i == 0 and active_markup:
                safe_send(part, markup=active_markup)
            else:
                safe_send(part)
        return

    safe_send(clean_text, markup=active_markup)


def get_otp_numpad_markup():
    markup = types.InlineKeyboardMarkup(row_width=3)
    markup.row(
        types.InlineKeyboardButton("1", callback_data="num:1"),
        types.InlineKeyboardButton("2", callback_data="num:2"),
        types.InlineKeyboardButton("3", callback_data="num:3")
    )
    markup.row(
        types.InlineKeyboardButton("4", callback_data="num:4"),
        types.InlineKeyboardButton("5", callback_data="num:5"),
        types.InlineKeyboardButton("6", callback_data="num:6")
    )
    markup.row(
        types.InlineKeyboardButton("7", callback_data="num:7"),
        types.InlineKeyboardButton("8", callback_data="num:8"),
        types.InlineKeyboardButton("9", callback_data="num:9")
    )
    markup.row(
        types.InlineKeyboardButton("⌫ Clear", callback_data="num:clear"),
        types.InlineKeyboardButton("0", callback_data="num:0"),
        types.InlineKeyboardButton("✅ Submit", callback_data="num:submit")
    )
    return markup


# ====================================================================
# PHASE 1: PERMANENT SETUP BOT (ALWAYS ON, READY FOR NEXT SETUP)
# ====================================================================
def start_setup_bot_service():
    setup_bot = telebot.TeleBot(SETUP_BOT_TOKEN)
    setup_state = {}

    try:
        bot_info = setup_bot.get_me()
        setup_bot_username = f"@{bot_info.username}" if bot_info.username else "Setup Bot"
    except Exception:
        setup_bot_username = "@SetupBot"

    print(f"🛠️ Setup Bot Live & Permanent on: {setup_bot_username}")

    def is_intercepted_command(message):
        txt = (message.text or "").strip()
        if txt.startswith("/"):
            chat_id = message.chat.id
            setup_state.pop(chat_id, None)
            if txt == "/start":
                on_start(message)
            else:
                setup_bot.send_message(chat_id, "⚠️ Wizard reset. Send /start to begin fresh.", parse_mode="HTML")
            return True
        return False

    @setup_bot.message_handler(commands=["start"])
    def on_start(message):
        chat_id = message.chat.id
        setup_state.pop(chat_id, None)
        welcome = (
            f"🤖 <b>Universal Bot Cloner Setup</b>\n\n"
            f"Welcome to the installer wizard ({setup_bot_username}).\n"
            f"Tap below to configure a new clone bot."
        )
        m = types.InlineKeyboardMarkup()
        m.add(types.InlineKeyboardButton("🚀 Start Setup", callback_data="begin"))
        setup_bot.send_message(chat_id, welcome, reply_markup=m, parse_mode="HTML")

    @setup_bot.callback_query_handler(func=lambda c: c.data == "begin")
    def on_begin(call):
        chat_id = call.message.chat.id
        try:
            setup_bot.answer_callback_query(call.id)
        except Exception:
            pass
        setup_state[chat_id] = {"step": "password", "data": {}}
        msg = setup_bot.send_message(chat_id, "🔑 <b>Enter Master Setup Password:</b>", parse_mode="HTML")
        setup_bot.register_next_step_handler(msg, step_password)

    def step_password(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "password":
            return
        if message.text and message.text.strip() == MASTER_SETUP_PASS:
            setup_state[chat_id]["step"] = "admin_password"
            msg = setup_bot.send_message(
                chat_id,
                "🔒 <b>Set Your /admin Password:</b>\n<i>(This password will unlock the admin panel in your clone)</i>",
                parse_mode="HTML"
            )
            setup_bot.register_next_step_handler(msg, step_admin_password)
        else:
            msg = setup_bot.send_message(chat_id, "❌ <b>Incorrect password.</b> Please try again:", parse_mode="HTML")
            setup_bot.register_next_step_handler(msg, step_password)

    def step_admin_password(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "admin_password":
            return
        pwd = (message.text or "").strip()
        if len(pwd) < 4:
            msg = setup_bot.send_message(chat_id, "❌ Admin password must be at least 4 characters long. Try again:")
            setup_bot.register_next_step_handler(msg, step_admin_password)
            return

        setup_state[chat_id]["data"]["admin_panel_pass"] = pwd
        setup_state[chat_id]["step"] = "bot_token"
        msg = setup_bot.send_message(chat_id, "🤖 <b>Enter Cloned Bot Token:</b>\n<i>(From @BotFather)</i>", parse_mode="HTML")
        setup_bot.register_next_step_handler(msg, step_bot_token)

    def step_bot_token(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "bot_token":
            return
        txt = (message.text or "").strip()
        if ":" not in txt or len(txt) < 30:
            msg = setup_bot.send_message(chat_id, "❌ Invalid bot token format. Please re-enter:")
            setup_bot.register_next_step_handler(msg, step_bot_token)
            return

        setup_state[chat_id]["data"]["bot_token"] = txt
        setup_state[chat_id]["step"] = "api_id"
        msg = setup_bot.send_message(chat_id, "🆔 <b>Enter Telegram API ID:</b>\n<i>(my.telegram.org)</i>", parse_mode="HTML")
        setup_bot.register_next_step_handler(msg, step_api_id)

    def step_api_id(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "api_id":
            return
        txt = (message.text or "").strip()
        if not txt.isdigit():
            msg = setup_bot.send_message(chat_id, "❌ API ID must be numeric digits only. Enter again:")
            setup_bot.register_next_step_handler(msg, step_api_id)
            return
        setup_state[chat_id]["data"]["api_id"] = txt
        setup_state[chat_id]["step"] = "api_hash"
        msg = setup_bot.send_message(chat_id, "🔐 <b>Enter Telegram API HASH:</b>", parse_mode="HTML")
        setup_bot.register_next_step_handler(msg, step_api_hash)

    def step_api_hash(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "api_hash":
            return
        txt = (message.text or "").strip()
        if len(txt) < 10:
            msg = setup_bot.send_message(chat_id, "❌ Invalid API HASH. Enter again:")
            setup_bot.register_next_step_handler(msg, step_api_hash)
            return
        setup_state[chat_id]["data"]["api_hash"] = txt

        m = types.InlineKeyboardMarkup(row_width=1)
        m.add(
            types.InlineKeyboardButton("⚡ Generate String Session (Numpad)", callback_data="gen_session_now"),
            types.InlineKeyboardButton("📋 Paste Existing Session String", callback_data="paste_session_now")
        )
        setup_bot.send_message(
            chat_id,
            "🔑 <b>Choose String Session Option:</b>\n\n"
            "• Tap <b>Generate</b> to login via Secure Numpad.\n"
            "• Tap <b>Paste</b> if you already have a String Session.",
            reply_markup=m,
            parse_mode="HTML"
        )

    @setup_bot.callback_query_handler(func=lambda c: c.data == "paste_session_now")
    def on_paste_session(call):
        chat_id = call.message.chat.id
        try:
            setup_bot.answer_callback_query(call.id)
        except Exception:
            pass
        setup_state[chat_id]["step"] = "session_manual"
        msg = setup_bot.send_message(chat_id, "📋 <b>Paste Telethon Session String:</b>", parse_mode="HTML")
        setup_bot.register_next_step_handler(msg, step_session_manual)

    def step_session_manual(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "session_manual":
            return
        txt = (message.text or "").strip()
        if len(txt) < 50:
            msg = setup_bot.send_message(chat_id, "❌ Session string too short. Enter again:")
            setup_bot.register_next_step_handler(msg, step_session_manual)
            return
        setup_state[chat_id]["data"]["session"] = txt
        setup_state[chat_id]["step"] = "target"
        msg = setup_bot.send_message(chat_id, "🎯 <b>Enter Target Bot Username:</b>\n(e.g., <code>@TargetBot</code>)", parse_mode="HTML")
        setup_bot.register_next_step_handler(msg, step_target)

    @setup_bot.callback_query_handler(func=lambda c: c.data == "gen_session_now")
    def on_gen_session(call):
        chat_id = call.message.chat.id
        try:
            setup_bot.answer_callback_query(call.id)
        except Exception:
            pass
        setup_state[chat_id]["step"] = "ask_phone"
        msg = setup_bot.send_message(
            chat_id,
            "📱 <b>Enter Phone Number:</b>\n"
            "Include country code (e.g., <code>+919876543210</code>):",
            parse_mode="HTML"
        )
        setup_bot.register_next_step_handler(msg, step_ask_phone)

    def step_ask_phone(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "ask_phone":
            return
        phone = (message.text or "").strip().replace(" ", "")
        if not re.match(r"^\+[1-9]\d{6,14}$", phone):
            msg = setup_bot.send_message(chat_id, "❌ Invalid phone format. Example: <code>+919876543210</code>:", parse_mode="HTML")
            setup_bot.register_next_step_handler(msg, step_ask_phone)
            return

        status = setup_bot.send_message(chat_id, "⏳ <i>Requesting login code from Telegram...</i>", parse_mode="HTML")
        d = setup_state[chat_id]["data"]

        loop = asyncio.new_event_loop()
        client = TelegramClient(StringSession(), int(d["api_id"]), d["api_hash"], loop=loop)

        async def _req_code():
            await client.connect()
            res = await client.send_code_request(phone)
            return res.phone_code_hash

        try:
            hash_code = loop.run_until_complete(_req_code())
            setup_state[chat_id]["gen_ctx"] = {
                "loop": loop,
                "client": client,
                "phone": phone,
                "hash": hash_code
            }
            try:
                setup_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass

            setup_state[chat_id]["step"] = "entering_otp_numpad"
            setup_state[chat_id]["entered_otp"] = ""
            setup_bot.send_message(
                chat_id,
                "📩 <b>Enter Telegram Login Code:</b>\n\n"
                "<i>Niche keypad buttons se OTP tap karein:</i>\n\n"
                "Entered Code: <code>[ Empty ]</code>",
                reply_markup=get_otp_numpad_markup(),
                parse_mode="HTML"
            )
        except Exception as e:
            try:
                setup_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
            setup_bot.send_message(chat_id, f"❌ Failed to request code: <code>{esc(str(e))}</code>\nSend /start to try again.", parse_mode="HTML")

    @setup_bot.callback_query_handler(func=lambda c: c.data.startswith("num:"))
    def on_otp_numpad_press(call):
        chat_id = call.message.chat.id
        if chat_id not in setup_state or setup_state[chat_id].get("step") != "entering_otp_numpad":
            setup_bot.answer_callback_query(call.id, "Session expired.", show_alert=True)
            return

        action = call.data.split(":")[1]
        curr = setup_state[chat_id].get("entered_otp", "")

        if action == "clear":
            curr = ""
            setup_state[chat_id]["entered_otp"] = curr
            setup_bot.answer_callback_query(call.id, "Cleared")
            setup_bot.edit_message_text(
                "📩 <b>Enter Telegram Login Code:</b>\n\n"
                "<i>Niche keypad buttons se OTP tap karein:</i>\n\n"
                "Entered Code: <code>[ Empty ]</code>",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=get_otp_numpad_markup(),
                parse_mode="HTML"
            )
            return

        if action.isdigit():
            if len(curr) < 6:
                curr += action
                setup_state[chat_id]["entered_otp"] = curr
                setup_bot.answer_callback_query(call.id, f"Entered: {action}")
                display_code = " ".join(list(curr))
                setup_bot.edit_message_text(
                    "📩 <b>Enter Telegram Login Code:</b>\n\n"
                    "<i>Niche keypad buttons se OTP tap karein:</i>\n\n"
                    f"Entered Code: <code>{display_code}</code>",
                    chat_id=chat_id,
                    message_id=call.message.message_id,
                    reply_markup=get_otp_numpad_markup(),
                    parse_mode="HTML"
                )
            else:
                setup_bot.answer_callback_query(call.id, "Max 6 digits", show_alert=True)
            return

        if action == "submit":
            if len(curr) < 5:
                setup_bot.answer_callback_query(call.id, "Kam se kam 5 digits dalein!", show_alert=True)
                return

            setup_bot.answer_callback_query(call.id, "Verifying OTP...")
            ctx = setup_state.get(chat_id, {}).get("gen_ctx")
            if not ctx:
                setup_bot.send_message(chat_id, "Session expired. Send /start.")
                return

            status = setup_bot.send_message(chat_id, "⏳ <i>Verifying credentials securely...</i>", parse_mode="HTML")

            async def _sign_in():
                try:
                    await ctx["client"].sign_in(phone=ctx["phone"], code=curr, phone_code_hash=ctx["hash"])
                    return "ok", ctx["client"].session.save()
                except SessionPasswordNeededError:
                    return "2fa_needed", None
                except PhoneCodeInvalidError:
                    return "invalid_code", None
                except Exception as e:
                    return "err", f"{type(e).__name__}: {str(e)}"

            res, data = ctx["loop"].run_until_complete(_sign_in())
            try:
                setup_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass

            if res == "ok":
                setup_state[chat_id]["data"]["session"] = data
                setup_bot.send_message(
                    chat_id,
                    "✅ <b>Session Successfully Generated & Saved!</b>\n"
                    "<i>String session has been linked securely in memory.</i>",
                    parse_mode="HTML"
                )
                setup_state[chat_id]["step"] = "target"
                msg = setup_bot.send_message(chat_id, "🎯 <b>Enter Target Bot Username:</b>\n(e.g., <code>@TargetBot</code>)", parse_mode="HTML")
                setup_bot.register_next_step_handler(msg, step_target)
                return

            elif res == "2fa_needed":
                setup_state[chat_id]["step"] = "ask_2fa"
                msg = setup_bot.send_message(chat_id, "🔐 <b>Account has Two-Step Verification (2FA).</b>\nEnter your 2FA Cloud Password:", parse_mode="HTML")
                setup_bot.register_next_step_handler(msg, step_ask_2fa)
                return
            elif res == "invalid_code":
                setup_bot.send_message(chat_id, "❌ <b>Invalid Code.</b> Please enter again via numpad:")
                setup_state[chat_id]["entered_otp"] = ""
                setup_bot.send_message(
                    chat_id,
                    "📩 <b>Enter Telegram Login Code:</b>\n\n"
                    "Entered Code: <code>[ Empty ]</code>",
                    reply_markup=get_otp_numpad_markup(),
                    parse_mode="HTML"
                )
                return
            else:
                setup_bot.send_message(chat_id, f"❌ <b>Error:</b> <code>{esc(str(data))}</code>", parse_mode="HTML")

    def step_ask_2fa(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "ask_2fa":
            return
        pwd = (message.text or "").strip()
        ctx = setup_state.get(chat_id, {}).get("gen_ctx")
        status = setup_bot.send_message(chat_id, "⏳ <i>Verifying 2FA Password...</i>", parse_mode="HTML")

        async def _sign_2fa():
            try:
                await ctx["client"].sign_in(password=pwd)
                return "ok", ctx["client"].session.save()
            except PasswordHashInvalidError:
                return "invalid_2fa", None
            except Exception as e:
                return "err", str(e)

        res, data = ctx["loop"].run_until_complete(_sign_2fa())
        try:
            setup_bot.delete_message(chat_id, status.message_id)
        except Exception:
            pass

        if res == "ok":
            setup_state[chat_id]["data"]["session"] = data
            setup_bot.send_message(
                chat_id,
                "✅ <b>2FA Verified & Session Generated!</b>",
                parse_mode="HTML"
            )
            setup_state[chat_id]["step"] = "target"
            msg = setup_bot.send_message(chat_id, "🎯 <b>Enter Target Bot Username:</b>\n(e.g., <code>@TargetBot</code>)", parse_mode="HTML")
            setup_bot.register_next_step_handler(msg, step_target)
        else:
            msg = setup_bot.send_message(chat_id, "❌ Incorrect 2FA Password. Re-enter:")
            setup_bot.register_next_step_handler(msg, step_ask_2fa)

    def step_target(message):
        if is_intercepted_command(message):
            return
        chat_id = message.chat.id
        if setup_state.get(chat_id, {}).get("step") != "target":
            return
        txt = (message.text or "").strip()
        if not re.match(r"^@?[a-zA-Z][a-zA-Z0-9_]{2,}$", txt):
            msg = setup_bot.send_message(chat_id, "❌ Invalid username format. e.g., <code>@username</code>:")
            setup_bot.register_next_step_handler(msg, step_target)
            return

        data = setup_state[chat_id]["data"]
        target = txt if txt.startswith("@") else "@" + txt

        status = setup_bot.send_message(chat_id, f"⚡ Connecting to <code>{esc(target)}</code>...", parse_mode="HTML")

        test_engine = MasterCloneEngine(data["api_id"], data["api_hash"], data["session"], target)
        ok = test_engine.start()
        try:
            setup_bot.delete_message(chat_id, status.message_id)
        except Exception:
            pass

        if not ok:
            err = test_engine.error or "Handshake error."
            setup_bot.send_message(chat_id, f"❌ <b>Setup Failed:</b>\n<code>{esc(err)}</code>\nRestart with /start.", parse_mode="HTML")
            test_engine.stop()
            return

        test_engine.stop()

        bot_token = data["bot_token"]
        bot_id = bot_token.split(":")[0]
        cfg_path = os.path.join(DATA_DIR, f"{bot_id}.json")

        cfg = {
            "admin_panel_pass": data["admin_panel_pass"],
            "setup_bot_username": setup_bot_username,
            "bot_token": bot_token,
            "api_id": data["api_id"],
            "api_hash": data["api_hash"],
            "session": data["session"],
            "target": target,
            "admin_ids": [chat_id],
            "replacements": {}
        }
        with open(cfg_path, "w") as f:
            json.dump(cfg, f, indent=4)

        # Start clone bot process in background thread
        start_cloned_bot_instance(cfg_path)

        setup_bot.send_message(
            chat_id,
            f"✅ <b>Setup Completed!</b>\n\n"
            f"Your dedicated Cloned Bot is now <b>LIVE</b>!\n"
            f"Open it and send <code>/start</code>.\n\n"
            f"<i>(Setup Bot is ready for the next setup anytime!)</i>",
            parse_mode="HTML"
        )
        
        # 👉 Clean up state: Setup bot bhool gaya aur fresh ready ho gaya!
        setup_state.pop(chat_id, None)

    # Setup bot infinite polling in background
    setup_bot.infinity_polling(skip_pending=True)


# ====================================================================
# PHASE 2: INDEPENDENT CLONED BOT INSTANCES
# ====================================================================
def start_cloned_bot_instance(cfg_path):
    th = threading.Thread(target=_cloned_bot_worker, args=(cfg_path,), daemon=True)
    th.start()

def _cloned_bot_worker(cfg_path):
    if not os.path.exists(cfg_path):
        return

    with open(cfg_path, "r") as f:
        config = json.load(f)

    # Per-clone license/access data (kept independently in each bot config).
    config.setdefault("access_locked", False)
    config.setdefault("access_keys", {})
    config.setdefault("user_access", {})
    config.setdefault("blocked_users", [])

    bot_token = config["bot_token"]
    # Persist newly introduced defaults for older clone configurations.
    with open(cfg_path, "w") as f:
        json.dump(config, f, indent=4)
    print(f"🚀 Launching Cloned Bot for Target: {config.get('target')}...")

    cloned_bot = telebot.TeleBot(bot_token)
    admin_state = {}

    clone_engine = MasterCloneEngine(
        config["api_id"],
        config["api_hash"],
        config["session"],
        config["target"]
    )

    if not clone_engine.start():
        print(f"❌ Failed to launch clone {bot_token}: {clone_engine.error}")
        return

    RUNNING_CLONES[bot_token] = {
        "engine": clone_engine,
        "bot": cloned_bot
    }

    def save_current_config():
        with open(cfg_path, "w") as f:
            json.dump(config, f, indent=4)

    def _user_has_valid_key(user_id):
        uid = str(user_id)
        access = config.setdefault("user_access", {}).get(uid)
        if not access:
            return False
        if float(access.get("expires_at", 0)) <= time.time():
            config["user_access"].pop(uid, None)
            blocked = config.setdefault("blocked_users", [])
            if uid not in blocked:
                blocked.append(uid)
            save_current_config()
            return False
        return True

    def require_user_access(message, prompt=True):
        uid = str(message.chat.id)
        if message.chat.id in config.get("admin_ids", []):
            return True
        # Check an issued user's expiry on every interaction, even if the
        # global lock was later switched off.
        has_key_record = uid in config.setdefault("user_access", {})
        key_is_valid = _user_has_valid_key(uid) if has_key_record else False
        is_blocked = uid in config.setdefault("blocked_users", [])
        is_locked = bool(config.get("access_locked", False))
        if not is_blocked and not is_locked:
            return True
        if key_is_valid:
            return True
        if prompt:
            cloned_bot.send_message(
                message.chat.id,
                "🔐 <b>Access Locked</b>\n\nEnter your access key to use this bot:",
                parse_mode="HTML"
            )
        return False

    def is_intercepted_clone_command(message):
        txt = (message.text or "").strip()
        if txt.startswith("/"):
            chat_id = message.chat.id
            admin_state.pop(chat_id, None)
            if txt == "/start":
                on_clone_start(message)
                return True
            if txt == "/admin":
                cmd_admin(message)
                return True
            if txt == "/session":
                cmd_session(message)
                return True
            if txt == "/changepass":
                cmd_changepass(message)
                return True
        return False

    def get_admin_panel_markup():
        m = types.InlineKeyboardMarkup(row_width=2)
        m.add(
            types.InlineKeyboardButton("🎯 Change Target", callback_data="adm_target"),
            types.InlineKeyboardButton("📝 Edit Watermarks", callback_data="adm_watermark"),
            types.InlineKeyboardButton("📋 View Active Rules", callback_data="adm_view"),
            types.InlineKeyboardButton("🔑 View Session", callback_data="adm_view_session"),
            types.InlineKeyboardButton("🔒 Change Password", callback_data="adm_changepass"),
            types.InlineKeyboardButton("🗑️ Clear Replacements", callback_data="adm_clear_rules"),
            types.InlineKeyboardButton("🔒 Lock Bot", callback_data="adm_lock"),
            types.InlineKeyboardButton("🔓 Unlock Bot", callback_data="adm_unlock"),
            types.InlineKeyboardButton("🔑 Generate Key", callback_data="adm_genkey"),
            types.InlineKeyboardButton("📋 Key List", callback_data="adm_keylist"),
            types.InlineKeyboardButton("⚠️ Reset & Stop Clone", callback_data="adm_reset")
        )
        return m

    def open_admin_panel(chat_id):
        rep_count = len(config.get("replacements", {}))
        text = (
            "⚡ <b>SYSTEM CONTROL PANEL</b>\n\n"
            f"🎯 <b>Target Service:</b> <code>{esc(config.get('target', 'N/A'))}</code>\n"
            f"🔄 <b>Watermark Rules Active:</b> <code>{rep_count}</code>\n"
            f"🛡️ <b>Status:</b> <code>{'LOCKED' if config.get('access_locked') else 'UNLOCKED'}</code>\n"
            f"🔑 <b>Generated Keys:</b> <code>{len(config.get('access_keys', {}))}</code>"
        )
        cloned_bot.send_message(chat_id, text, reply_markup=get_admin_panel_markup(), parse_mode="HTML")

    @cloned_bot.message_handler(commands=["session"])
    def cmd_session(message):
        chat_id = message.chat.id
        if chat_id not in config.get("admin_ids", []):
            cloned_bot.send_message(chat_id, "❌ <b>Access Denied:</b> Restricted to administrators.", parse_mode="HTML")
            return

        sess = config.get("session", "N/A")
        reply = (
            "🔑 <b>TELETHON STRING SESSION:</b>\n\n"
            f"<code>{esc(sess)}</code>\n\n"
            "<i>Keep this session confidential.</i>"
        )
        cloned_bot.send_message(chat_id, reply, parse_mode="HTML")

    @cloned_bot.message_handler(commands=["admin"])
    def cmd_admin(message):
        chat_id = message.chat.id
        if chat_id in config.get("admin_ids", []):
            open_admin_panel(chat_id)
            return

        admin_state[chat_id] = {"action": "verify_admin_pass"}
        msg = cloned_bot.send_message(chat_id, "🔒 <b>Enter Admin Password:</b>", parse_mode="HTML")
        cloned_bot.register_next_step_handler(msg, step_check_admin_pass)

    def step_check_admin_pass(message):
        if is_intercepted_clone_command(message):
            return
        chat_id = message.chat.id
        st = admin_state.pop(chat_id, None)
        if not st or st.get("action") != "verify_admin_pass":
            return
        entered_pass = (message.text or "").strip()
        correct_pass = config.get("admin_panel_pass") or MASTER_SETUP_PASS

        if entered_pass == correct_pass:
            if "admin_ids" not in config:
                config["admin_ids"] = []
            if chat_id not in config["admin_ids"]:
                config["admin_ids"].append(chat_id)
                save_current_config()

            try:
                cloned_bot.delete_message(chat_id, message.message_id)
            except Exception:
                pass
            open_admin_panel(chat_id)
        else:
            cloned_bot.send_message(chat_id, "❌ <b>Access Denied:</b> Incorrect password.", parse_mode="HTML")

    @cloned_bot.message_handler(commands=["changepass"])
    def cmd_changepass(message):
        chat_id = message.chat.id
        admin_state[chat_id] = {"action": "verify_master_pass"}
        msg = cloned_bot.send_message(
            chat_id,
            "🔑 <b>Authorization Required:</b>\nEnter Master Authorization Password to proceed:",
            parse_mode="HTML"
        )
        cloned_bot.register_next_step_handler(msg, step_verify_master_for_reset)

    def step_verify_master_for_reset(message):
        if is_intercepted_clone_command(message):
            return
        chat_id = message.chat.id
        st = admin_state.pop(chat_id, None)
        if not st or st.get("action") != "verify_master_pass":
            return
        entered = (message.text or "").strip()
        if entered == MASTER_SETUP_PASS:
            admin_state[chat_id] = {"action": "set_new_admin_pass"}
            msg = cloned_bot.send_message(chat_id, "✅ <b>Authorization Successful.</b>\nEnter your <b>NEW Admin Password:</b>", parse_mode="HTML")
            cloned_bot.register_next_step_handler(msg, step_save_new_admin_pass)
        else:
            cloned_bot.send_message(chat_id, "❌ <b>Access Denied:</b> Invalid master credentials.", parse_mode="HTML")

    def step_save_new_admin_pass(message):
        if is_intercepted_clone_command(message):
            return
        chat_id = message.chat.id
        st = admin_state.pop(chat_id, None)
        if not st or st.get("action") != "set_new_admin_pass":
            return
        new_pass = (message.text or "").strip()
        if len(new_pass) < 4:
            cloned_bot.send_message(chat_id, "❌ Password must be at least 4 characters long.")
            return

        config["admin_panel_pass"] = new_pass
        save_current_config()
        cloned_bot.send_message(chat_id, "✅ <b>Admin password updated.</b> Send <code>/admin</code> to open control panel.", parse_mode="HTML")

    def step_generate_key(message):
        if is_intercepted_clone_command(message):
            return
        chat_id = message.chat.id
        st = admin_state.pop(chat_id, None)
        if not st or st.get("action") != "generate_key":
            return
        raw = (message.text or "").strip().lower().replace(" ", "")
        match = re.fullmatch(r"([1-9]\d*)(h|hr|hrs|hour|hours|d|day|days)", raw)
        if not match:
            cloned_bot.send_message(chat_id, "❌ Invalid duration. Example: <code>12h</code> or <code>7d</code>.", parse_mode="HTML")
            return
        amount = int(match.group(1))
        unit = match.group(2)
        seconds = amount * (3600 if unit in ("h", "hr", "hrs", "hour", "hours") else 86400)
        key = "KEY-" + secrets.token_hex(4).upper()
        config.setdefault("access_keys", {})[key] = {
            "duration_seconds": seconds,
            "duration_label": raw,
            "created_at": time.time(),
            "used_by": None,
            "activated_at": None,
            "expires_at": None
        }
        save_current_config()
        cloned_bot.send_message(
            chat_id,
            f"✅ <b>Access Key Generated</b>\n\n<code>{key}</code>\nValidity: <b>{esc(raw)}</b> after activation\nUsage: <b>One user only</b>",
            parse_mode="HTML"
        )

    def send_key_list(chat_id):
        keys = config.setdefault("access_keys", {})
        if not keys:
            cloned_bot.send_message(chat_id, "📋 No keys generated yet.")
            return
        for key, info in list(keys.items()):
            used_by = info.get("used_by")
            expires = info.get("expires_at")
            if not used_by:
                status = "UNUSED"
            elif expires and float(expires) <= time.time():
                status = "EXPIRED"
            else:
                status = "USED"
            exp_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expires)) if expires else "Starts after activation"
            body = (f"🔑 <code>{esc(key)}</code>\n"
                    f"Status: <b>{status}</b>\n"
                    f"User ID: <code>{esc(str(used_by or '-'))}</code>\n"
                    f"Expiry: <code>{esc(exp_text)}</code>")
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("🗑️ Delete Key / Re-lock User", callback_data=f"adm_delkey:{key}"))
            cloned_bot.send_message(chat_id, body, reply_markup=kb, parse_mode="HTML")

    @cloned_bot.callback_query_handler(func=lambda c: c.data.startswith("adm_"))
    def admin_callbacks(call):
        chat_id = call.message.chat.id
        if chat_id not in config.get("admin_ids", []):
            try:
                cloned_bot.answer_callback_query(call.id, "Session expired. Send /admin again.", show_alert=True)
            except Exception:
                pass
            return

        try:
            cloned_bot.answer_callback_query(call.id)
        except Exception:
            pass

        data = call.data

        if data == "adm_lock":
            config["access_locked"] = True
            save_current_config()
            cloned_bot.send_message(chat_id, "🔒 <b>Bot locked.</b> Users must enter a valid one-time key.", parse_mode="HTML")
            return

        if data == "adm_unlock":
            config["access_locked"] = False
            save_current_config()
            cloned_bot.send_message(chat_id, "🔓 <b>Bot unlocked.</b> Users without individual revocation can use it normally.", parse_mode="HTML")
            return

        if data == "adm_genkey":
            admin_state[chat_id] = {"action": "generate_key"}
            msg = cloned_bot.send_message(chat_id, "🔑 <b>Enter key validity:</b>\nExamples: <code>6h</code>, <code>12h</code>, <code>1d</code>, <code>7d</code>\n<i>Validity starts when the first user activates the key.</i>", parse_mode="HTML")
            cloned_bot.register_next_step_handler(msg, step_generate_key)
            return

        if data == "adm_keylist":
            send_key_list(chat_id)
            return

        if data.startswith("adm_delkey:"):
            key = data.split(":", 1)[1]
            info = config.setdefault("access_keys", {}).pop(key, None)
            if info and info.get("used_by") is not None:
                uid = str(info["used_by"])
                config.setdefault("user_access", {}).pop(uid, None)
                blocked = config.setdefault("blocked_users", [])
                if uid not in blocked:
                    blocked.append(uid)
            save_current_config()
            cloned_bot.send_message(chat_id, "🗑️ Key deleted. Its assigned user has been re-locked and must use a new key.")
            return

        if data == "adm_view_session":
            sess = config.get("session", "N/A")
            reply = (
                "🔑 <b>TELETHON STRING SESSION:</b>\n\n"
                f"<code>{esc(sess)}</code>\n\n"
                "<i>Tap above to copy. Keep it confidential.</i>"
            )
            cloned_bot.send_message(chat_id, reply, parse_mode="HTML")
            return

        if data == "adm_target":
            admin_state[chat_id] = {"action": "change_target"}
            msg = cloned_bot.send_message(chat_id, "🎯 <b>Send new target bot username:</b>\n(e.g., <code>@NewBot</code>)", parse_mode="HTML")
            cloned_bot.register_next_step_handler(msg, step_change_target)
            return

        if data == "adm_changepass":
            admin_state[chat_id] = {"action": "set_new_admin_pass"}
            msg = cloned_bot.send_message(chat_id, "🔑 <b>Enter New Admin Password:</b>", parse_mode="HTML")
            cloned_bot.register_next_step_handler(msg, step_save_new_admin_pass)
            return

        if data == "adm_watermark":
            admin_state[chat_id] = {"action": "add_rule"}
            guide = (
                "📝 <b>Add Replacement Rules:</b>\n\n"
                "Format me reply karein:\n"
                "<code>old_text | new_text</code>\n\n"
                "Example:\n"
                "<code>@suryaxz | @MyCustomBot</code>\n"
                "<code>HiTeckGroop.in | MySecureDomain.com</code>"
            )
            msg = cloned_bot.send_message(chat_id, guide, parse_mode="HTML")
            cloned_bot.register_next_step_handler(msg, step_save_rule)
            return

        if data == "adm_view":
            rules = config.get("replacements", {})
            if not rules:
                cloned_bot.send_message(chat_id, "ℹ️ No custom watermark rules defined.")
                return
            report = "📋 <b>Active Text/Watermark Filters:</b>\n\n"
            for o, n in rules.items():
                report += f"• <code>{esc(o)}</code> ➔ <code>{esc(n)}</code>\n"
            cloned_bot.send_message(chat_id, report, parse_mode="HTML")
            return

        if data == "adm_clear_rules":
            config["replacements"] = {}
            save_current_config()
            cloned_bot.send_message(chat_id, "✅ All custom watermark rules wiped.")
            return

        if data == "adm_reset":
            if os.path.exists(cfg_path):
                os.remove(cfg_path)

            remove_kb = types.ReplyKeyboardRemove()
            msg_text = (
                "⚠️ <b>This Cloned Bot has been stopped & deleted!</b>\n\n"
                "To configure a new bot, visit the main Setup Bot:\n"
                f"👉 <b>{config.get('setup_bot_username', '@SetupBot')}</b>"
            )
            cloned_bot.send_message(chat_id, msg_text, reply_markup=remove_kb, parse_mode="HTML")
            clone_engine.stop()
            cloned_bot.stop_polling()
            RUNNING_CLONES.pop(bot_token, None)
            return

    def step_change_target(message):
        if is_intercepted_clone_command(message):
            return
        chat_id = message.chat.id
        st = admin_state.pop(chat_id, None)
        if not st or st.get("action") != "change_target":
            return
        txt = (message.text or "").strip()
        if not re.match(r"^@?[a-zA-Z][a-zA-Z0-9_]{2,}$", txt):
            cloned_bot.send_message(chat_id, "❌ Invalid username format.")
            return
        new_target = txt if txt.startswith("@") else "@" + txt
        status = cloned_bot.send_message(chat_id, f"⚡ Initializing clean switch to <code>{esc(new_target)}</code>...", parse_mode="HTML")

        new_eng = MasterCloneEngine(config["api_id"], config["api_hash"], config["session"], new_target)
        if new_eng.start():
            nonlocal clone_engine
            clone_engine.stop()
            clone_engine = new_eng
            config["target"] = new_target
            save_current_config()
            try:
                cloned_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass

            clean_markup = types.ReplyKeyboardRemove()
            cloned_bot.send_message(
                chat_id,
                f"✅ <b>Successfully connected to</b> <code>{esc(new_target)}</code>!\n"
                f"Old UI buttons have been cleaned. Initializing new bot menu...",
                reply_markup=clean_markup,
                parse_mode="HTML"
            )

            try:
                init_res = clone_engine.send_text("/start")
                if init_res:
                    relay_to_user(cloned_bot, chat_id, init_res, config.get("replacements", {}))
            except Exception:
                pass
        else:
            try:
                cloned_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
            cloned_bot.send_message(chat_id, f"❌ Link failed: {new_eng.error}")

    def step_save_rule(message):
        if is_intercepted_clone_command(message):
            return
        chat_id = message.chat.id
        st = admin_state.pop(chat_id, None)
        if not st or st.get("action") != "add_rule":
            return
        txt = (message.text or "").strip()
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        added = []

        if "replacements" not in config:
            config["replacements"] = {}

        for line in lines:
            if "|" in line:
                old_val, new_val = line.split("|", 1)
                old_val = old_val.strip()
                new_val = new_val.strip()
                if old_val:
                    config["replacements"][old_val] = new_val
                    added.append(f"<code>{esc(old_val)}</code> ➔ <code>{esc(new_val)}</code>")

        if not added:
            cloned_bot.send_message(chat_id, "❌ Format error. Har line par likhein: <code>old_text | new_text</code>", parse_mode="HTML")
            return

        save_current_config()
        cloned_bot.send_message(chat_id, f"✅ <b>Rules saved:</b>\n" + "\n".join(added), parse_mode="HTML")

    # ================== STEALTH USER FLOW ==================
    @cloned_bot.message_handler(commands=["start"])
    def on_clone_start(message):
        chat_id = message.chat.id
        if not require_user_access(message):
            return
        status = cloned_bot.send_message(chat_id, "🔍 <i>Loading...</i>", parse_mode="HTML")
        try:
            res = clone_engine.send_text("/start")
            try:
                cloned_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
            if res:
                relay_to_user(cloned_bot, chat_id, res, config.get("replacements", {}))
            else:
                cloned_bot.send_message(chat_id, "⚠️ Service unavailable. Please try again.", parse_mode="HTML")
        except Exception as e:
            try:
                cloned_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
            cloned_bot.send_message(chat_id, f"❌ Error: <code>{esc(str(e))}</code>", parse_mode="HTML")

    @cloned_bot.callback_query_handler(func=lambda c: c.data.startswith("clk:"))
    def on_button_click(call):
        chat_id = call.message.chat.id
        if chat_id not in config.get("admin_ids", []) and not require_user_access(call.message):
            try:
                cloned_bot.answer_callback_query(call.id, "Enter a valid access key first.", show_alert=True)
            except Exception:
                pass
            return
        try:
            cloned_bot.answer_callback_query(call.id)
        except Exception:
            pass

        _, r, c = call.data.split(":")
        status = cloned_bot.send_message(chat_id, "⚡ <i>Processing...</i>", parse_mode="HTML")
        try:
            res = clone_engine.click_button(int(r), int(c))
            try:
                cloned_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
            if res:
                relay_to_user(cloned_bot, chat_id, res, config.get("replacements", {}))
            else:
                cloned_bot.send_message(chat_id, "⚠️ No response received.", parse_mode="HTML")
        except Exception as e:
            try:
                cloned_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
            cloned_bot.send_message(chat_id, f"❌ Error: <code>{esc(str(e))}</code>", parse_mode="HTML")

    @cloned_bot.message_handler(func=lambda m: True, content_types=["text"])
    def on_text(message):
        chat_id = message.chat.id
        if chat_id in admin_state:
            return

        uid = str(chat_id)
        needs_key = (uid in config.setdefault("blocked_users", []) or bool(config.get("access_locked", False))) and chat_id not in config.get("admin_ids", [])
        if needs_key and not _user_has_valid_key(uid):
            entered_key = (message.text or "").strip().upper()
            key_info = config.setdefault("access_keys", {}).get(entered_key)
            if not key_info:
                cloned_bot.send_message(chat_id, "❌ Invalid key. Please enter a valid access key.")
                return
            if key_info.get("used_by") is not None:
                cloned_bot.send_message(chat_id, "❌ This key has already been used by another user.")
                return
            duration = int(key_info.get("duration_seconds", 0))
            if duration <= 0:
                cloned_bot.send_message(chat_id, "❌ This key is invalid. Ask admin for a new key.")
                return
            expiry = time.time() + duration
            key_info["used_by"] = chat_id
            key_info["activated_at"] = time.time()
            key_info["expires_at"] = expiry
            config.setdefault("user_access", {})[uid] = {"key": entered_key, "expires_at": expiry}
            if uid in config.setdefault("blocked_users", []):
                config["blocked_users"].remove(uid)
            save_current_config()
            cloned_bot.send_message(chat_id, "✅ Key accepted! Access granted. You can now use the bot.")
            on_clone_start(message)
            return

        if not require_user_access(message):
            return

        status = cloned_bot.send_message(chat_id, "🔍 <i>Processing request...</i>", parse_mode="HTML")
        try:
            res = clone_engine.send_text(message.text)
            try:
                cloned_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
            if res:
                relay_to_user(cloned_bot, chat_id, res, config.get("replacements", {}))
            else:
                cloned_bot.send_message(chat_id, "⏱ Request timed out. Please try again.", parse_mode="HTML")
        except Exception as e:
            try:
                cloned_bot.delete_message(chat_id, status.message_id)
            except Exception:
                pass
            cloned_bot.send_message(chat_id, f"❌ Error: <code>{esc(str(e))}</code>", parse_mode="HTML")

    cloned_bot.infinity_polling(skip_pending=True)


# ============================ MAIN ROUTER ============================
if __name__ == "__main__":
    # 1. Pehle se saved sabhi cloned bots ko run karo
    for fname in os.listdir(DATA_DIR):
        if fname.endswith(".json"):
            cfg_p = os.path.join(DATA_DIR, fname)
            start_cloned_bot_instance(cfg_p)

    # 2. Main thread par Setup Bot hamesha 24/7 on rahega
    start_setup_bot_service()
