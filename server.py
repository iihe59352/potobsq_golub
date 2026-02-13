from flask import Flask, send_file, request
from flask_socketio import SocketIO, emit
from datetime import datetime
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'golub-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*")

# Хранилище данных
users = {}  # {id_подключения: имя_пользователя}
messages = []  # история сообщений

@app.route('/')
def index():
    return send_file('messenger.html')

@socketio.on('connect')
def handle_connect():
    print('✅ Новый пользователь подключился')

@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in users:
        username = users[request.sid]
        del users[request.sid]
        # Уведомляем всех
        emit('user_left', {'username': username}, broadcast=True)
        emit('users_update', list(users.values()), broadcast=True)

@socketio.on('join')
def handle_join(data):
    username = data['username']
    users[request.sid] = username
    
    # Отправляем историю чата новому пользователю
    emit('history', messages[-50:])
    
    # Обновляем список пользователей у всех
    emit('users_update', list(users.values()), broadcast=True)
    
    # Сообщение о новом участнике
    system_msg = {
        'user': '📨 Почтовый голубь',
        'text': f'🐦 {username} присоединился к чату!',
        'time': datetime.now().strftime('%H:%M')
    }
    emit('message', system_msg, broadcast=True)

@socketio.on('message')
def handle_message(data):
    username = users.get(request.sid, 'Аноним')
    
    msg_data = {
        'user': username,
        'text': data['text'],
        'time': datetime.now().strftime('%H:%M')
    }
    
    messages.append(msg_data)
    if len(messages) > 100:
        messages.pop(0)
    
    emit('message', msg_data, broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    print(f"🚀 Сервер 'Почтовый голубь' запущен!")
    print(f"📱 Открой браузер и перейди на: http://localhost:{port}")
    socketio.run(app, host='0.0.0.0', port=port, debug=True)