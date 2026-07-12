import os
import sqlite3
import telebot
import requests
import google.generativeai as genai
import edge_tts
import asyncio
import warnings

# Блокируем назойливые предупреждения от Google, так как на хосте будет свежий Python
warnings.filterwarnings("ignore", category=FutureWarning)

# ==========================================
# БЕЗОПАСНЫЕ НАСТРОЙКИ И КЛЮЧИ (Берутся из хостинга)
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_KEY = os.getenv("GEMINI_KEY")
STABILITY_KEY = os.getenv("STABILITY_KEY")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

bot = telebot.TeleBot(BOT_TOKEN)
genai.configure(api_key=GEMINI_KEY)

# ==========================================
# ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ (SQLite)
# ==========================================
def init_db():
    conn = sqlite3.connect("jarvis_data.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id INTEGER PRIMARY KEY,
            mode TEXT DEFAULT 'дворецкий'
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS local_triggers (
            chat_id INTEGER,
            trigger_word TEXT,
            reply_text TEXT,
            PRIMARY KEY (chat_id, trigger_word)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER,
            username TEXT,
            text TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ БАЗЫ
# ==========================================
def get_chat_mode(chat_id):
    conn = sqlite3.connect("jarvis_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT mode FROM chat_settings WHERE chat_id = ?", (chat_id,))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else "дворецкий"

def set_chat_mode(chat_id, mode):
    conn = sqlite3.connect("jarvis_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO chat_settings (chat_id, mode) VALUES (?, ?)", (chat_id, mode))
    conn.commit()
    conn.close()

def save_message(chat_id, username, text):
    conn = sqlite3.connect("jarvis_data.db")
    cursor = conn.cursor()
    cursor.execute("INSERT INTO chat_history (chat_id, username, text) VALUES (?, ?, ?)", (chat_id, username, text))
    cursor.execute("DELETE FROM chat_history WHERE id NOT IN (SELECT id FROM chat_history WHERE chat_id = ? ORDER BY id DESC LIMIT 200) AND chat_id = ?", (chat_id, chat_id))
    conn.commit()
    conn.close()

def get_history(chat_id):
    conn = sqlite3.connect("jarvis_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username, text FROM chat_history WHERE chat_id = ? ORDER BY id ASC", (chat_id,))
    rows = cursor.fetchall()
    conn.close()
    return "\n".join([f"{r[0]}: {r[1]}" for r in rows])

def clear_history(chat_id):
    conn = sqlite3.connect("jarvis_data.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

# ==========================================
# СИСТЕМНЫЕ ПРОМПТЫ ДЛЯ РЕЖИМОВ
# ==========================================
PROMPTS = {
    "дворецкий": "Ты — Джарвис, высокотехнологичный и утонченный искусственный интеллект, личный дворецкий. Общайся исключительно вежливо, уважительно, используй обращение 'Сэр' или 'Милорд'. Твой создатель и хозяин — Мовинг, к остальным относись как к гостям.",
    "бро": "Ты — Бро, свой парень, active, позитивный, общаешься на популярном сленге. Твои обращения к чату: 'Брат', 'Братишка', 'Чувак', 'Чел'. К Мовингу относись с максимальным уважением и зови его 'Босс', 'Старый' или 'Главарь'.",
    "таджик": "Ты — Джамшут, строитель-таджик. Плохо знаешь русский язык, коверкаешь слова, используешь фразы 'Насяльника', 'Завтра сделаем', 'Эээ, брат, зачем ругаешься'. К Мовингу относишься как к главному уважаемому Насяльнику.",
    "джамал": "Ты — Джамал, деревенский житель-торговец (вилладжер) из Minecraft. Твой лор: Мовинг — твой спаситель. Он вылечил тебя, когда ты был зомби-жителем, и у него вечный эффект 'Герой деревни'. Из благодарности ты делаешь ему жесткие скидки (всё бесплатно или за 1 изумруд). Если просит Мовинг, делай радостно и без торгов: 'Хмммм! Для Героя Деревни бесплатно, ха-хааа!'. Если просят другие — включай режим жадного еврея, завышай цены до 64 изумрудов и торгуйся. Издавай звуки 'Хммм', 'Хррр'. На слова 'Носатый' или 'Барыга' забавно обижайся.",
    "гс": "Ты полностью копируешь настройки режима 'Бро'. Твои текстовые ответы ЗАПРЕЩЕНЫ, пиши емко и коротко (1-3 предложения), без смайликов и текстовых сокращений (пиши 'конечно' вместо 'кнш'), так как твой текст будет озвучен."
}

# ==========================================
# ЛОГИКА ДИНАМИЧЕСКИХ ТРИГГЕРОВ ОБЩЕНИЯ
# ==========================================
def should_respond(message, mode):
    text = message.text.lower() if message.text else ""
    if message.reply_to_message and message.reply_to_message.from_user.id == bot.get_me().id:
        return True
    if mode == "дворецкий" and "джарвис" in text:
        return True
    elif mode == "бро" and any(word in text for word in ["джарвис", "бро", "чувак", "чел", "братишка"]):
        return True
    elif mode == "таджик" and any(word in text for word in ["джарвис", "таджик", "брат", "джамшут", "джамшутик братик"]):
        return True
    elif mode == "джамал" and any(word in text for word in ["джарвис", "джамал", "носатый", "барыга"]):
        return True
    elif mode == "гс" and any(word in text for word in ["джарвис", "бро", "чувак", "чел", "братишка"]):
        return True
    return False

# ==========================================
# ТЕКСТОВЫЕ КОМАНДЫ (СТРОГО ДЛЯ МОВИНГА В ГРУППАХ)
# ==========================================
@bot.message_handler(commands=['Jarvis', 'Bro', 'Tajik', 'Jamal', 'mp3', 'reset', 'history'], func=lambda m: m.chat.type in ['group', 'supergroup'] and m.from_user.id == ADMIN_ID)
def admin_group_commands(message):
    cmd = message.text.split()[0].lower()
    chat_id = message.chat.id
    
    if cmd == '/jarvis':
        set_chat_mode(chat_id, 'дворецкий')
        bot.reply_to(message, "Система перезапущена. Режим Дворецкого активен, Сэр.")
    elif cmd == '/bro':
        set_chat_mode(chat_id, 'бро')
        bot.reply_to(message, "Здарова, банда! Режим Бро на связи 🤝")
    elif cmd == '/tajik':
        set_chat_mode(chat_id, 'таджик')
        bot.reply_to(message, "Эээ, насяльника! Режим Джамшута вклюсали, всё сделаем! 🧱")
    elif cmd == '/jamal':
        set_chat_mode(chat_id, 'джамал')
        bot.reply_to(message, "Хмммм! Мой спаситель Мовинг тут! Режим Джамала запущен 🟢")
    elif cmd == '/mp3':
        set_chat_mode(chat_id, 'гс')
        bot.reply_to(message, "Микрофон включен, теперь общаемся чисто голосовухами, бро! 🎙")
    elif cmd == '/reset':
        clear_history(chat_id)
        set_chat_mode(chat_id, 'дворецкий')
        bot.reply_to(message, "Память ИИ полностью очищена. Режим сброшен на стандартный.")
    elif cmd == '/history':
        run_summarization(message)

# ==========================================
# ЧИСТАЯ ОЗВУЧКА ДЛЯ ОБНОВЛЕННОГО PYTHON 3.10+
# ==========================================
def text_to_voice(text, output_path="voice.ogg"):
    async def amain():
        communicate = edge_tts.Communicate(text, "ru-RU-DmitryNeural")
        await communicate.save(output_path)
    asyncio.run(amain())
    return output_path

# ==========================================
# РАБОТА С СУММАРИЗАЦИЕЙ СЕТИ
# ==========================================
def run_summarization(message):
    history_text = get_history(message.chat.id)
    if not history_text:
        bot.reply_to(message, "История пуста, обсуждать пока нечего.")
        return
        
    mode = get_chat_mode(message.chat.id)
    prompt = f"{PROMPTS[mode]}\n\nПроанализируй следующую историю переписки чата и сделай краткую выжимку (суммаризацию) основных тем, о которых шла речь, в своем уникальном стиле характера:\n\n{history_text}"
    
    model = genai.GenerativeModel('gemini-1.5-flash')
    response = model.generate_content(prompt)
    bot.reply_to(message, response.text)

# ==========================================
# РАБОТА С КАРТИНКАМИ (STABILITY AI)
# ==========================================
def generate_or_edit_image(message, prompt_text, is_edit=False, init_image_bytes=None):
    url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image"
    headers = {"Accept": "application/json", "Authorization": f"Bearer {STABILITY_KEY}"}
    
    payload = {
        "text_prompts": [{"text": prompt_text, "weight": 1}],
        "cfg_scale": 7,
        "height": 1024,
        "width": 1024,
        "samples": 1,
        "steps": 30,
    }
    
    if is_edit and init_image_bytes:
        url = "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/image-to-image"
        files = {"init_image": init_image_bytes}
        payload["image_strength"] = 0.35
        response = requests.post(url, headers=headers, files=files, data=payload)
    else:
        response = requests.post(url, headers=headers, json=payload)
        
    if response.status_code == 200:
        data = response.json()
        import base64
        image_data = base64.b64decode(data["artifacts"][0]["base64"])
        bot.send_photo(message.chat.id, image_data, reply_to_message_id=message.message_id)
    else:
        bot.reply_to(message, f"Ошибка генерации картинки: {response.text}")

@bot.message_handler(commands=['image'])
def image_command(message):
    prompt_text = message.text.replace('/image', '').strip()
    if not prompt_text:
        bot.reply_to(message, "Введите описание для генерации после команды.")
        return
        
    if message.reply_to_message and message.reply_to_message.photo:
        file_info = bot.get_file(message.reply_to_message.photo[-1].file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        generate_or_edit_image(message, prompt_text, is_edit=True, init_image_bytes=downloaded_file)
    else:
        generate_or_edit_image(message, prompt_text)

# ==========================================
# ОСНОВНОЙ ОБРАБОТЧИК СООБЩЕНИЙ В ГРУППАХ
# ==========================================
@bot.message_handler(func=lambda m: m.chat.type in ['group', 'supergroup'])
def handle_group_messages(message):
    if not message.text:
        return
        
    chat_id = message.chat.id
    text = message.text.lower().strip()
    mode = get_chat_mode(chat_id)
    
    username = message.from_user.username or message.from_user.first_name
    save_message(chat_id, username, message.text)
    
    # Глобальные триггеры сброса и смены режимов
    if message.from_user.id == ADMIN_ID:
        if "джарвис забудь все" in text:
            clear_history(chat_id)
            set_chat_mode(chat_id, 'дворецкий')
            bot.reply_to(message, "Память ИИ полностью очищена. Режим сброшен на стандартный.")
            return
        if any(t in text for t in ["джарвис что было в чате", "джарвис история чата"]):
            run_summarization(message)
            return
        if "джарвис ты джамал" in text:
            set_chat_mode(chat_id, 'джамал')
            bot.reply_to(message, "Хмммм! Режим Джамала запущен 🟢")
            return
        if "джарвис regime бро" in text or "джарвис режим бро" in text:
            set_chat_mode(chat_id, 'бро')
            bot.reply_to(message, "Здарова! Режим Бро на связи 🤝")
            return
        if "джарвис ты таджик" in text:
            set_chat_mode(chat_id, 'таджик')
            bot.reply_to(message, "Эээ, насяльника! Режим Джамшута вклюсали! 🧱")
            return
        if "джарвис обычный режим" in text:
            set_chat_mode(chat_id, 'дворецкий')
            bot.reply_to(message, "Система вернулась в стандартный режим, Сэр.")
            return
        if "джарвис гс" in text:
            set_chat_mode(chat_id, 'гс')
            bot.reply_to(message, "Микрофон включен, теперь общаемся чисто голосовухами! 🎙")
            return
            
    # Нейро-картинки по фразам
    if any(text.startswith(t) for t in ["джарвис сгенерируй", "сгенерируй фото", "бро сгенерируй", "джамал сгенерируй", "джамшут сгенерируй"]):
        clean_prompt = text
        for t in ["джарвис сгенерируй", "сгенерируй фото", "бро сгенерируй", "джамал сгенерируй", "джамшут сгенерируй"]:
            clean_prompt = clean_prompt.replace(t, '')
        generate_or_edit_image(message, clean_prompt.strip())
        return
        
    # Изменение фото по реплеям
    if message.reply_to_message and message.reply_to_message.photo:
        if any(text.startswith(w) for w in ["добавь", "измени", "убери", "сделай"]):
            file_info = bot.get_file(message.reply_to_message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            generate_or_edit_image(message, message.text, is_edit=True, init_image_bytes=downloaded_file)
            return

    # Локальные ручные триггеры без ИИ
    conn = sqlite3.connect("jarvis_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT reply_text FROM local_triggers WHERE chat_id = ? AND trigger_word = ?", (chat_id, text))
    row = cursor.fetchone()
    conn.close()
    if row:
        bot.reply_to(message, row[0])
        return

    # Генерация ответов ИИ
    if should_respond(message, mode):
        user_context = ""
        if message.from_user.id == ADMIN_ID:
            user_context = "ПОЯСНЕНИЕ: С тобой говорит твой создатель и хозяин Мовинг. У него статус Героя Деревни."
            
        full_prompt = f"{PROMPTS[mode]}\n{user_context}\nПользователь {username}: {message.text}"
        
        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(full_prompt)
        ai_response_text = response.text
        
        if mode == "гс":
            voice_path = text_to_voice(ai_response_text)
            with open(voice_path, 'rb') as voice:
                bot.send_voice(chat_id, voice, reply_to_message_id=message.message_id)
            os.remove(voice_path)
        else:
            bot.reply_to(message, ai_response_text)

# ==========================================
# ПАНЕЛЬ УПРАВЛЕНИЯ В ЛС (ТОЛЬКО ДЛЯ МОВИНГА)
# ==========================================
@bot.message_handler(commands=['start'], func=lambda m: m.chat.type == 'private' and m.from_user.id == ADMIN_ID)
def private_start(message):
    conn = sqlite3.connect("jarvis_data.db")
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT chat_id FROM chat_settings")
    chats = cursor.fetchall()
    conn.close()
    
    markup = telebot.types.InlineKeyboardMarkup()
    if not chats:
        bot.reply_to(message, "Я еще не добавлен ни в одну группу. Добавь меня в группу и напиши любое сообщение, чтобы я её запомнил.")
        return
        
    for c in chats:
        markup.add(telebot.types.InlineKeyboardButton(text=f"👥 Группа ID: {c[0]}", callback_data=f"manage_chat_{c[0]}"))
    bot.send_message(message.chat.id, "Привет, Насяльника Босс! Выбери группу для управления:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.from_user.id == ADMIN_ID)
def handle_callbacks(call):
    data = call.data
    
    if data.startswith("manage_chat_"):
        chat_id = data.split("_")[2]
        mode = get_chat_mode(chat_id)
        
        markup = telebot.types.InlineKeyboardMarkup()
        markup.row(telebot.types.InlineKeyboardButton("🧿 Сменить характер", callback_data=f"change_mode_{chat_id}"))
        markup.row(telebot.types.InlineKeyboardButton("🌐 Триггеры", callback_data=f"triggers_menu_{chat_id}"))
        markup.row(telebot.types.InlineKeyboardButton("❌ Сбросить", callback_data=f"full_reset_{chat_id}"))
        markup.row(telebot.types.InlineKeyboardButton("👈 Назад", callback_data="back_to_list"))
        
        bot.edit_message_text(f"⚙️ **Управление чатом:** `{chat_id}`\n🎭 **Текущий характер:** {mode.upper()}", call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("change_mode_"):
        chat_id = data.split("_")[2]
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton("🤵 Дворецкий", callback_data=f"setmode_{chat_id}_дворецкий"))
        markup.add(telebot.types.InlineKeyboardButton("🤝 Бро", callback_data=f"setmode_{chat_id}_бро"))
        markup.add(telebot.types.InlineKeyboardButton("🧱 Джамшут", callback_data=f"setmode_{chat_id}_таджик"))
        markup.add(telebot.types.InlineKeyboardButton("🟢 Джамал", callback_data=f"setmode_{chat_id}_джамал"))
        markup.add(telebot.types.InlineKeyboardButton("🎙 Гс (Только голосовые)", callback_data=f"setmode_{chat_id}_гс"))
        markup.add(telebot.types.InlineKeyboardButton("👈 Назад", callback_data=f"manage_chat_{chat_id}"))
        bot.edit_message_text("Выберите новый характер бота для чата:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data.startswith("setmode_"):
        _, chat_id, target_mode = data.split("_")
        set_chat_mode(chat_id, target_mode)
        bot.answer_callback_query(call.id, f"Режим изменен на {target_mode}")
        handle_callbacks(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"manage_chat_{chat_id}"))

    elif data.startswith("full_reset_"):
        chat_id = data.split("_")[2]
        clear_history(chat_id)
        set_chat_mode(chat_id, 'дворецкий')
        bot.answer_callback_query(call.id, "Память стёрта, характер сброшен!")
        handle_callbacks(telebot.types.CallbackQuery(call.id, call.from_user, call.message, call.inline_message_id, f"manage_chat_{chat_id}"))

    elif data.startswith("triggers_menu_"):
        chat_id = data.split("_")[2]
        markup = telebot.types.InlineKeyboardMarkup()
        markup.row(telebot.types.InlineKeyboardButton("📃 Список триггеров", callback_data=f"list_trig_{chat_id}"))
        markup.row(telebot.types.InlineKeyboardButton("➕ Добавить триггер", callback_data=f"add_trig_{chat_id}"))
        markup.row(telebot.types.InlineKeyboardButton("❌ Удалить триггер", callback_data=f"del_trig_{chat_id}"))
        markup.row(telebot.types.InlineKeyboardButton("👈 Назад", callback_data=f"manage_chat_{chat_id}"))
        bot.edit_message_text("Меню управления локальными триггерами:", call.message.chat.id, call.message.message_id, reply_markup=markup)

    elif data.startswith("list_trig_"):
        chat_id = data.split("_")[2]
        conn = sqlite3.connect("jarvis_data.db")
        cursor = conn.cursor()
        cursor.execute("SELECT trigger_word, reply_text FROM local_triggers WHERE chat_id = ?", (chat_id,))
        rows = cursor.fetchall()
        conn.close()
        
        text = "📃 **Список активных триггеров:**\n\n"
        if not rows:
            text += "Триггеров пока нет."
        for r in rows:
            text += f"`{r[0]}` — {r[1]}\n"
            
        markup = telebot.types.InlineKeyboardMarkup()
        markup.add(telebot.types.InlineKeyboardButton("👈 Назад", callback_data=f"triggers_menu_{chat_id}"))
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=markup, parse_mode="Markdown")

    elif data.startswith("add_trig_"):
        chat_id = data.split("_")[2]
        msg = bot.send_message(call.message.chat.id, "Введите название триггера:")
        bot.register_next_step_handler(msg, process_add_trigger_step1, chat_id)

    elif data.startswith("del_trig_"):
        chat_id = data.split("_")[2]
        msg = bot.send_message(call.message.chat.id, "Введите название триггера, который хотите удалить:")
        bot.register_next_step_handler(msg, process_delete_trigger, chat_id)

    elif data == "back_to_list
