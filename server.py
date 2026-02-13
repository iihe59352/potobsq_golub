import os
from flask import Flask, request
from flask_socketio import SocketIO, emit
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'pigeon_secret_key'

# Разрешаем подключения со всех адресов (важно для Render)
socketio = SocketIO(app, cors_allowed_origins="*")

# Список активных пользователей { session_id: username }
users = {}

@app.route('/')
def index():
    return "Почтовый голубь в небе! Сервер работает."

@socketio.on('login')
def handle_login(data):
    username = data.get('name', 'Анонимный голубь')
    users[request.sid] = username
    print(f"--- {username} прилетел в голубятню ---")
    # Уведомляем всех, кто онлайн (опционально)
    emit('update_users', list(users.values()), broadcast=True)

@socketio.on('global')
def handle_message(data):
    username = users.get(request.sid, 'Аноним')
    current_time = datetime.now().strftime("%H:%M")
    
    # broadcast=True — сообщение улетает ВСЕМ пользователям
    emit('global_message', {
        'from': username,
        'text': data['text'],
        'time': current_time
    }, broadcast=True)

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in users:
        print(f"--- {users[request.sid]} улетел ---")
        del users[request.sid]
        emit('update_users', list(users.values()), broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
