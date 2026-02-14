import os
from flask import Flask, request, render_template
from flask_socketio import SocketIO, emit
from datetime import datetime

# ВАЖНО: template_folder='.' говорит серверу искать HTML прямо в текущей папке
app = Flask(__name__, template_folder='.')
app.config['SECRET_KEY'] = 'pigeon_secret_key'

# Разрешаем подключение с любого адреса
socketio = SocketIO(app, cors_allowed_origins="*")

users = {}

@app.route('/')
def index():
    # Вот здесь была ошибка. Теперь мы загружаем именно HTML файл
    return render_template('messenger.html')

@socketio.on('login')
def handle_login(data):
    username = data.get('name', 'Аноним')
    users[request.sid] = username
    print(f"--- {username} залетел в чат ---")
    emit('update_users', list(users.values()), broadcast=True)

@socketio.on('global')
def handle_message(data):
    username = users.get(request.sid, 'Аноним')
    current_time = datetime.now().strftime("%H:%M")
    
    emit('global_message', {
        'from': username,
        'text': data['text'],
        'time': current_time
    }, broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
