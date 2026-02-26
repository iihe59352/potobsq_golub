import os
import sqlite3
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit, join_room
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, template_folder='.')
app.config['SECRET_KEY'] = 'super_secret_pigeon_key'
socketio = SocketIO(app, cors_allowed_origins="*", max_http_buffer_size=10000000)

DB_NAME = 'pigeon_v3.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (username TEXT PRIMARY KEY, password TEXT, avatar TEXT, display_name TEXT, bio TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS messages 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  sender TEXT, receiver TEXT, text TEXT, time TEXT, is_deleted INTEGER DEFAULT 0)''')
    conn.commit()
    conn.close()

init_db()

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

# --- АВТОРИЗАЦИЯ ---

@socketio.on('register')
def handle_register(data):
    username = data['username']
    password = data['password']
    default_avatar = "https://cdn-icons-png.flaticon.com/512/149/149071.png"
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        hashed_pw = generate_password_hash(password)
        c.execute("INSERT INTO users (username, password, avatar, display_name, bio) VALUES (?, ?, ?, ?, ?)", 
                  (username, hashed_pw, default_avatar, username, "Я почтовый голубь!"))
        conn.commit()
        emit('auth_response', {'success': True, 'user': get_user_data(username)})
    except sqlite3.IntegrityError:
        emit('auth_response', {'success': False, 'message': 'Такой логин уже занят!'})
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
    new_avatar = data.get('avatar')
    
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
    emit('profile_updated', get_user_data(username))

# --- СООБЩЕНИЯ ---

@socketio.on('get_history')
def handle_history(data):
    target = data.get('chat_with') 
    user = data.get('user')
    
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    if target == 'global':
        c.execute("SELECT * FROM messages WHERE receiver='global' AND is_deleted=0 ORDER BY id DESC LIMIT 50")
    else:
        c.execute("""SELECT * FROM messages 
                     WHERE ((sender=? AND receiver=?) OR (sender=? AND receiver=?)) 
                     AND is_deleted=0 ORDER BY id DESC LIMIT 50""", (user, target, target, user))
    
    rows = c.fetchall()
    history = []
    for r in rows:
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
    
    emit('history_data', {'messages': history[::-1], 'chat': target})

@socketio.on('send_message')
def handle_msg(data):
    sender = data['sender']
    receiver = data['receiver'] 
    text = data['text']
    time = datetime.now().strftime("%H:%M")
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("INSERT INTO messages (sender, receiver, text, time) VALUES (?, ?, ?, ?)", 
              (sender, receiver, text, time))
    msg_id = c.lastrowid
    conn.commit()
    conn.close()
    
    sender_info = get_user_data(sender)
    msg_data = {
        'id': msg_id, 'from': sender, 
        'sender_display': sender_info['display_name'],
        'sender_avatar': sender_info['avatar'],
        'text': text, 'time': time, 'receiver': receiver
    }
    
    if receiver == 'global':
        socketio.emit('new_message', msg_data, room='global')
    else:
        emit('new_message', msg_data) # Себе
        socketio.emit('new_message', msg_data, room=receiver) # Ему
        socketio.emit('notification', {'from': sender_info['display_name'], 'text': text}, room=receiver)

@socketio.on('delete_message')
def delete_message(data):
    msg_id = data['id']
    username = data['user']
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT sender, receiver FROM messages WHERE id=?", (msg_id,))
    msg = c.fetchone()
    
    if msg and msg[0] == username: # Только свои сообщения
        c.execute("UPDATE messages SET is_deleted=1 WHERE id=?", (msg_id,))
        conn.commit()
        receiver = msg[1]
        if receiver == 'global':
            socketio.emit('message_deleted', {'id': msg_id, 'chat': 'global'}, room='global')
        else:
            socketio.emit('message_deleted', {'id': msg_id, 'chat': receiver}, room=username)
            socketio.emit('message_deleted', {'id': msg_id, 'chat': receiver}, room=receiver)
    conn.close()

# --- ПОИСК (Только люди) ---
@socketio.on('search')
def search(data):
    query = data['query']
    user = data['user']
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT username, display_name, avatar FROM users WHERE username LIKE ? LIMIT 10", (f'%{query}%',))
    results = []
    for r in c.fetchall():
        if r[0] != user:
            results.append({'id': r[0], 'name': r[1], 'avatar': r[2]})
    conn.close()
    emit('search_results', results)

# Подключение к глобальной комнате
@socketio.on('join_global')
def join_global():
    join_room('global')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
