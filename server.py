import os
import sqlite3
import json
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, template_folder='.')
app.config['SECRET_KEY'] = 'super_secret_pigeon_key'
socketio = SocketIO(app, cors_allowed_origins="*")

# --- БАЗА ДАННЫХ ---
def init_db():
    conn = sqlite3.connect('pigeon.db')
    c = conn.cursor()
    # Таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, avatar TEXT)''')
    # Таблица сообщений
    c.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  sender TEXT, receiver TEXT, text TEXT, time TEXT)''')
    conn.commit()
    conn.close()

init_db()

# --- ФУНКЦИИ ---

@app.route('/')
def index():
    return render_template('messenger.html')

@socketio.on('register')
def handle_register(data):
    username = data['username']
    password = data['password']
    # Случайный эмодзи если не выбран
    avatar = data.get('avatar', '🕊️') 
    
    conn = sqlite3.connect('pigeon.db')
    c = conn.cursor()
    
    try:
        # Хешируем пароль для безопасности
        hashed_pw = generate_password_hash(password)
        c.execute("INSERT INTO users VALUES (?, ?, ?)", (username, hashed_pw, avatar))
        conn.commit()
        emit('auth_response', {'success': True, 'username': username, 'avatar': avatar})
    except sqlite3.IntegrityError:
        emit('auth_response', {'success': False, 'message': 'Такой голубь уже существует!'})
    finally:
        conn.close()

@socketio.on('login')
def handle_login(data):
    username = data['username']
    password = data['password']
    
    conn = sqlite3.connect('pigeon.db')
    c = conn.cursor()
    c.execute("SELECT password, avatar FROM users WHERE username=?", (username,))
    row = c.fetchone()
    conn.close()
    
    if row and check_password_hash(row[0], password):
        # Успешный вход
        join_room(username) # Подключаем к личной комнате для уведомлений
        emit('auth_response', {'success': True, 'username': username, 'avatar': row[1]})
    else:
        emit('auth_response', {'success': False, 'message': 'Неверное имя или пароль'})

@socketio.on('get_history')
def handle_history(data):
    chat_with = data.get('chat_with') # 'global' или имя пользователя
    user = data.get('user')
    
    conn = sqlite3.connect('pigeon.db')
    c = conn.cursor()
    
    if chat_with == 'global':
        c.execute("SELECT sender, text, time, receiver FROM messages WHERE receiver='global' ORDER BY id DESC LIMIT 50")
    else:
        # История ЛС (сообщения от меня к нему И от него ко мне)
        c.execute("""SELECT sender, text, time, receiver FROM messages 
                     WHERE (sender=? AND receiver=?) OR (sender=? AND receiver=?) 
                     ORDER BY id DESC LIMIT 50""", (user, chat_with, chat_with, user))
    
    rows = c.fetchall()
    conn.close()
    
    # Отправляем историю (переворачиваем, чтобы старые были сверху)
    history = [{'from': r[0], 'text': r[1], 'time': r[2]} for r in rows][::-1]
    emit('history_data', {'messages': history, 'chat': chat_with})

@socketio.on('send_message')
def handle_msg(data):
    sender = data['sender']
    receiver = data['receiver'] # 'global' или имя пользователя
    text = data['text']
    time = datetime.now().strftime("%H:%M")
    
    # Сохраняем в БД
    conn = sqlite3.connect('pigeon.db')
    c = conn.cursor()
    c.execute("INSERT INTO messages (sender, receiver, text, time) VALUES (?, ?, ?, ?)", 
              (sender, receiver, text, time))
    conn.commit()
    conn.close()
    
    msg_data = {'from': sender, 'text': text, 'time': time, 'receiver': receiver}
    
    if receiver == 'global':
        emit('new_message', msg_data, broadcast=True)
    else:
        # Отправляем отправителю (чтобы он видел свое смс)
        emit('new_message', msg_data) 
        # Отправляем получателю в его личную комнату
        socketio.emit('new_message', msg_data, room=receiver) 

@socketio.on('join_dm')
def join_dm_room(data):
    join_room(data['username'])

# Поиск пользователей
@socketio.on('search_user')
def search_user(data):
    query = data['query']
    conn = sqlite3.connect('pigeon.db')
    c = conn.cursor()
    c.execute("SELECT username, avatar FROM users WHERE username LIKE ? LIMIT 5", (f'%{query}%',))
    results = [{'username': r[0], 'avatar': r[1]} for r in c.fetchall()]
    conn.close()
    emit('search_results', results)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
