import os
import sqlite3
import json
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, template_folder='.')
app.config['SECRET_KEY'] = 'super_secret_pigeon_key'
# Увеличиваем лимит сообщения для передачи картинок (до 10Мб)
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=10000000)

DB_NAME = 'pigeon_v2.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # Пользователи: добавили display_name (имя для показа) и bio (описание)
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, avatar TEXT, display_name TEXT, bio TEXT)''')
    
    # Сообщения: добавили type (text/image) и is_deleted
    c.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  sender TEXT, receiver TEXT, text TEXT, time TEXT, is_deleted INTEGER DEFAULT 0)''')
    
    # Группы и Каналы
    # type: 'group' или 'channel'
    c.execute('''CREATE TABLE IF NOT EXISTS communities 
                 (name TEXT PRIMARY KEY, owner TEXT, type TEXT, description TEXT, avatar TEXT)''')
    
    # Участники групп/каналов
    c.execute('''CREATE TABLE IF NOT EXISTS members 
                 (community TEXT, username TEXT, is_banned INTEGER DEFAULT 0)''')
                 
    conn.commit()
    conn.close()

init_db()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_user_data(username):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username, avatar, display_name, bio FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    if row:
        return {'username': row[0], 'avatar': row[1], 'display_name': row[2] or row[0], 'bio': row[3] or ''}
    return None

@app.route('/')
def index():
    return render_template('messenger.html')

# --- АВТОРИЗАЦИЯ И ПРОФИЛЬ ---

@socketio.on('register')
def handle_register(data):
    username = data['username']
    password = data['password']
    # По умолчанию аватарка - заглушка
    default_avatar = "https://cdn-icons-png.flaticon.com/512/149/149071.png"
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        hashed_pw = generate_password_hash(password)
        c.execute("INSERT INTO users (username, password, avatar, display_name, bio) VALUES (?, ?, ?, ?, ?)", 
                  (username, hashed_pw, default_avatar, username, "Привет, я использую Почтового Голубя!"))
        conn.commit()
        emit('auth_response', {'success': True, 'user': get_user_data(username)})
    except sqlite3.IntegrityError:
        emit('auth_response', {'success': False, 'message': 'Такой голубь уже занят!'})
    finally:
        conn.close()

@socketio.on('login')
def handle_login(data):
    username = data['username']
    password = data['password']
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT password FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    
    if row and check_password_hash(row[0], password):
        join_room(username) 
        emit('auth_response', {'success': True, 'user': get_user_data(username)})
    else:
        emit('auth_response', {'success': False, 'message': 'Неверный логин или пароль'})

@socketio.on('update_profile')
def update_profile(data):
    username = data['username']
    new_display_name = data.get('display_name')
    new_bio = data.get('bio')
    new_avatar = data.get('avatar') # Это будет длинная строка Base64
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    if new_display_name:
        c.execute("UPDATE users SET display_name=? WHERE username=?", (new_display_name, username))
    if new_bio:
        c.execute("UPDATE users SET bio=? WHERE username=?", (new_bio, username))
    if new_avatar:
        c.execute("UPDATE users SET avatar=? WHERE username=?", (new_avatar, username))
        
    conn.commit()
    conn.close()
    
    # Возвращаем обновленные данные
    emit('profile_updated', get_user_data(username))

# --- СООБЩЕНИЯ И ИСТОРИЯ ---

@socketio.on('get_history')
def handle_history(data):
    target = data.get('chat_with') 
    user = data.get('user')
    
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # Проверяем, это ЛС или Группа
    c.execute("SELECT type, owner FROM communities WHERE name=?", (target,))
    comm_info = c.fetchone()
    
    is_community = comm_info is not None
    community_owner = comm_info['owner'] if is_community else None
    community_type = comm_info['type'] if is_community else None

    # Получаем сообщения
    if is_community:
        # Для групп и каналов
        c.execute("SELECT * FROM messages WHERE receiver=? AND is_deleted=0 ORDER BY id DESC LIMIT 50", (target,))
    else:
        # Для ЛС
        c.execute("""SELECT * FROM messages 
                     WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?)) 
                     AND is_deleted=0
                     ORDER BY id DESC LIMIT 50""", (user, target, target, user))
    
    rows = c.fetchall()
    
    history = []
    for r in rows:
        # Подгружаем аватарку и display_name отправителя
        sender_data = get_user_data(r['sender'])
        history.append({
            'id': r['id'],
            'from': r['sender'],
            'sender_display': sender_data['display_name'],
            'sender_avatar': sender_data['avatar'],
            'text': r['text'],
            'time': r['time']
        })
    
    conn.close()
    
    # Отправляем права доступа (может ли писать?)
    can_write = True
    if community_type == 'channel' and user != community_owner:
        can_write = False
        
    emit('history_data', {
        'messages': history[::-1], 
        'chat': target,
        'is_owner': (user == community_owner) if is_community else False,
        'can_write': can_write
    })

@socketio.on('send_message')
def handle_msg(data):
    sender = data['sender']
    receiver = data['receiver'] 
    text = data['text']
    time = datetime.now().strftime("%H:%M")
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Проверка прав для канала (писать может только владелец)
    c.execute("SELECT owner, type FROM communities WHERE name=?", (receiver,))
    comm = c.fetchone()
    if comm and comm[1] == 'channel' and comm[0] != sender:
        return # Не владелец не может писать в канал
    
    # Сохраняем
    c.execute("INSERT INTO messages (sender, receiver, text, time) VALUES (?, ?, ?, ?)", 
              (sender, receiver, text, time))
    msg_id = c.lastrowid
    conn.commit()
    conn.close()
    
    sender_info = get_user_data(sender)
    
    msg_data = {
        'id': msg_id,
        'from': sender, 
        'sender_display': sender_info['display_name'],
        'sender_avatar': sender_info['avatar'],
        'text': text, 
        'time': time, 
        'receiver': receiver
    }
    
    # Рассылка
    if comm: # Это группа или канал
        socketio.emit('new_message', msg_data, room=receiver)
    else: # ЛС
        emit('new_message', msg_data) # Себе
        socketio.emit('new_message', msg_data, room=receiver) # Ему
        # Отправляем уведомление
        socketio.emit('notification', {'from': sender, 'text': 'Новое сообщение!'}, room=receiver)

@socketio.on('delete_message')
def delete_message(data):
    msg_id = data['id']
    username = data['user']
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Получаем инфо о сообщении и чате
    c.execute("SELECT sender, receiver FROM messages WHERE id=?", (msg_id,))
    msg = c.fetchone()
    
    if not msg:
        conn.close(); return

    sender, receiver = msg
    
    # Проверяем, владелец ли это группы
    c.execute("SELECT owner FROM communities WHERE name=?", (receiver,))
    comm = c.fetchone()
    is_group_owner = (comm and comm[0] == username)
    
    # Удаляем, если это мое сообщение ИЛИ я владелец группы
    if sender == username or is_group_owner:
        c.execute("UPDATE messages SET is_deleted=1 WHERE id=?", (msg_id,))
        conn.commit()
        # Уведомляем всех об удалении
        socketio.emit('message_deleted', {'id': msg_id, 'chat': receiver}, room=receiver if comm else None)
        if not comm: # Для ЛС также уведомляем второго участника
            socketio.emit('message_deleted', {'id': msg_id, 'chat': receiver}, room=sender)
            socketio.emit('message_deleted', {'id': msg_id, 'chat': receiver}, room=receiver)

    conn.close()

# --- ГРУППЫ И КАНАЛЫ ---

@socketio.on('create_community')
def create_community(data):
    name = data['name']
    comm_type = data['type'] # group или channel
    owner = data['owner']
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO communities (name, owner, type, description, avatar) VALUES (?, ?, ?, ?, ?)", 
                  (name, owner, comm_type, "Описание...", "https://cdn-icons-png.flaticon.com/512/1256/1256650.png"))
        # Владелец сразу участник
        c.execute("INSERT INTO members (community, username) VALUES (?, ?)", (name, owner))
        conn.commit()
        emit('community_created', {'success': True, 'name': name, 'type': comm_type})
        join_room(name) # Подключаем сокет создателя к комнате группы
    except sqlite3.IntegrityError:
        emit('community_created', {'success': False, 'message': 'Имя занято!'})
    finally:
        conn.close()

@socketio.on('join_community')
def join_community(data):
    name = data['name'] # Имя канала/группы
    user = data['user']
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # Проверка существования
    c.execute("SELECT type, owner FROM communities WHERE name=?", (name,))
    comm = c.fetchone()
    
    if not comm:
        emit('join_response', {'success': False, 'message': 'Не найдено'})
        conn.close(); return

    comm_type, owner = comm
    
    # Логика входа
    # Если группа - нужен инвайт (пока упростим: зайти можно, если не забанен)
    # Если канал - зайти может любой
    
    # Проверка на бан
    c.execute("SELECT is_banned FROM members WHERE community=? AND username=?", (name, user))
    member = c.fetchone()
    if member and member[0] == 1:
        emit('join_response', {'success': False, 'message': 'Вы забанены!'})
        conn.close(); return

    if not member:
        c.execute("INSERT INTO members (community, username) VALUES (?, ?)", (name, user))
        conn.commit()
    
    join_room(name)
    emit('join_response', {'success': True, 'name': name, 'type': comm_type})
    conn.close()

@socketio.on('ban_user')
def ban_user(data):
    # data = {community: '...', admin: '...', target: '...'}
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT owner FROM communities WHERE name=?", (data['community'],))
    res = c.fetchone()
    if res and res[0] == data['admin']:
        # Ставим бан
        c.execute("UPDATE members SET is_banned=1 WHERE community=? AND username=?", (data['community'], data['target']))
        conn.commit()
        # Выкидываем пользователя (технически сложно через сокет, но писать он больше не сможет)
    conn.close()

# --- ПОИСК ---
@socketio.on('search')
def search(data):
    query = data['query']
    user = data['user'] # Кто ищет
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    results = []
    
    # 1. Поиск людей
    c.execute("SELECT username, display_name, avatar FROM users WHERE username LIKE ? LIMIT 5", (f'%{query}%',))
    for r in c.fetchall():
        if r[0] != user:
            results.append({'type': 'user', 'id': r[0], 'name': r[1], 'avatar': r[2]})
            
    # 2. Поиск каналов (группы скрыты, если не знаешь название)
    c.execute("SELECT name, description, avatar FROM communities WHERE type='channel' AND name LIKE ? LIMIT 5", (f'%{query}%',))
    for r in c.fetchall():
        results.append({'type': 'channel', 'id': r[0], 'name': r[0], 'avatar': r[2]})
        
    conn.close()
    emit('search_results', results)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
