# app.py (Versión 2.9 - Final Robusta)
from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, leave_room, emit
import random
import uuid
import os
import redis
import json
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'la-version-final-del-todo'
socketio = SocketIO(app)

try:
    redis_client = redis.from_url(os.environ.get("REDIS_URL"))
    print("Conectado a Redis con éxito.")
except Exception as e:
    print(f"No se pudo conectar a Redis: {e}")
    redis_client = None

def get_room(room_id):
    if not redis_client: return None
    room_data_json = redis_client.get(room_id)
    return json.loads(room_data_json) if room_data_json else None
def save_room(room_id, room_data):
    if not redis_client: return
    redis_client.set(room_id, json.dumps(room_data))
def delete_room(room_id):
    if not redis_client: return
    redis_client.delete(room_id)
def get_all_rooms():
    if not redis_client: return {}
    room_keys = redis_client.keys('*')
    if not room_keys: return {}
    return {key.decode('utf-8'): json.loads(redis_client.get(key)) for key in room_keys}
def add_log(room_id, message, type='normal'):
    room = get_room(room_id)
    if not room: return
    timestamp = datetime.now().strftime('%H:%M:%S')
    log_entry = {'message': message, 'type': type, 'timestamp': timestamp}
    if 'log' not in room: room['log'] = []
    room['log'].append(log_entry)
    save_room(room_id, room)

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('reconnect_player')
def handle_reconnect(data):
    player_id = data.get('playerId')
    if not player_id: return
    for room_id, room_data in get_all_rooms().items():
        if player_id in room_data['players']:
            player = room_data['players'][player_id]
            player['sid'] = request.sid
            join_room(room_id)
            save_room(room_id, room_data)
            emit('session_restored', {'room_id': room_id, 'is_host': room_data.get('host') == player_id, 'role': player.get('role'), 'status': player.get('status')})
            update_room_state(room_id)
            return

@socketio.on('create_room')
def handle_create_room(data):
    player_name = data.get('name', 'Anónimo')
    player_id = str(uuid.uuid4())
    room_id = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ', k=5))
    new_room = {'players': {player_id: {'name': player_name, 'role': None, 'status': 'alive', 'sid': request.sid}}, 'game_state': 'lobby', 'host': player_id, 'log': []}
    save_room(room_id, new_room)
    add_log(room_id, f"{player_name} ha creado la sala.")
    join_room(room_id)
    emit('room_created', {'room_id': room_id, 'playerId': player_id, 'is_host': True})
    update_room_state(room_id)

@socketio.on('join_room')
def handle_join_room(data):
    player_name = data.get('name', 'Anónimo')
    room_id = data.get('room_id', '').upper()
    player_id = str(uuid.uuid4())
    room = get_room(room_id)
    if room and room['game_state'] == 'lobby':
        room['players'][player_id] = {'name': player_name, 'role': None, 'status': 'alive', 'sid': request.sid}
        add_log(room_id, f"{player_name} se ha unido a la sala.")
        save_room(room_id, room)
        join_room(room_id)
        emit('room_joined', {'room_id': room_id, 'playerId': player_id, 'is_host': False})
        update_room_state(room_id)

@socketio.on('disconnect')
def handle_disconnect():
    for room_id, room_data in get_all_rooms().items():
        for player_id, player in room_data['players'].items():
            if player.get('sid') == request.sid:
                add_log(room_id, f"{player['name']} se ha desconectado.")
                player['sid'] = None
                save_room(room_id, room_data)
                update_room_state(room_id)
                return

@socketio.on('leave_room')
def handle_leave_room(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room: return
    player_id_to_remove = next((pid for pid, p in room['players'].items() if p.get('sid') == request.sid), None)
    if player_id_to_remove:
        player_name = room['players'][player_id_to_remove]['name']
        add_log(room_id, f"{player_name} ha salido de la sala.")
        del room['players'][player_id_to_remove]
        leave_room(room_id)
        if not room['players']:
            delete_room(room_id)
        else:
            # Si el anfitrión se va, asigna a otro como anfitrión
            if room.get('host') == player_id_to_remove:
                room['host'] = next(iter(room['players']))
                add_log(room_id, f"{room['players'][room['host']]['name']} es el nuevo anfitrión.")
            save_room(room_id, room)
            update_room_state(room_id)

@socketio.on('start_game')
def handle_start_game(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room or room.get('game_state') == 'in_game': return
    host_id = next((pid for pid, p in room['players'].items() if p.get('sid') == request.sid), None)
    if not host_id or host_id != room.get('host'): return
    players_ids = list(room['players'].keys())
    role_list = [role for role, count in data.get('roles', {}).items() for _ in range(int(count))]
    if len(role_list) != len(players_ids): return
    random.shuffle(role_list)
    for player_id, role in zip(players_ids, role_list):
        room['players'][player_id]['role'] = role
    room['game_state'] = 'in_game'
    add_log(room_id, "¡La partida ha comenzado!")
    save_room(room_id, room)
    for player_id in players_ids:
        player = room['players'][player_id]
        if player.get('sid'):
            emit('role_assigned', {'role': player['role']}, to=player['sid'])
    update_room_state(room_id)

@socketio.on('kill_player')
def handle_kill_player(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room: return
    killer_id = next((pid for pid, p in room['players'].items() if p.get('sid') == request.sid), None)
    if not killer_id: return
    killer_info = room['players'].get(killer_id)
    victim_info = room['players'].get(data.get('target_id'))
    if not (killer_info and victim_info and victim_info['status'] == 'alive' and killer_info['status'] == 'alive'): return
    victim_info['status'] = 'dead'
    add_log(room_id, f"{killer_info['name']} ha matado a {victim_info['name']} (Rol: {victim_info['role']}).", 'info')
    if victim_info['role'] == killer_info['role']:
        killer_info['status'] = 'dead'
        add_log(room_id, f"¡FUEGO AMIGO! {killer_info['name']} ha muerto por traición.", 'error')
    save_room(room_id, room)
    update_room_state(room_id)

# --- FUNCIÓN CORREGIDA ---
@socketio.on('end_game')
def handle_end_game(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room: return
    host_id_request = next((pid for pid, p in room['players'].items() if p.get('sid') == request.sid), None)
    if not host_id_request or host_id_request != room.get('host'): return
    
    for player in room['players'].values():
        player['role'] = None
        player['status'] = 'alive'
    
    room['game_state'] = 'lobby'
    add_log(room_id, "El anfitrión ha finalizado la partida. Volviendo al lobby.")
    save_room(room_id, room)
    
    # Simplemente actualizamos el estado. El cliente se encargará de resetear su UI.
    update_room_state(room_id)
    print(f"Partida finalizada por el anfitrión en la sala {room_id}.")

def update_room_state(room_id):
    room = get_room(room_id)
    if room:
        player_data = [{'id': pid, 'name': p['name'], 'status': p['status']} for pid, p in room['players'].items()]
        # Notificamos si el jugador actual es el nuevo anfitrión
        for pid, player in room['players'].items():
            if player.get('sid'):
                is_host = room.get('host') == pid
                emit('update_room_state', {'players': player_data, 'game_state': room['game_state'], 'log': room.get('log', []), 'is_host': is_host}, to=player['sid'])

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
