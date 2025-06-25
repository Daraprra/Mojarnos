# app.py (Versión 2.7 - Lógica de guardado a prueba de bugs)
from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, leave_room, emit
import random
import uuid
import os
import redis
import json

app = Flask(__name__)
app.config['SECRET_KEY'] = 'la-version-mas-robusta-hasta-la-fecha'
socketio = SocketIO(app)

# --- CONEXIÓN A REDIS Y FUNCIONES DE AYUDA (Sin cambios) ---
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

@app.route('/')
def index():
    return render_template('index.html')

# --- El resto del código hasta start_game no cambia ---
@socketio.on('reconnect_player')
def handle_reconnect(data):
    player_id = data.get('playerId')
    if not player_id: return
    for room_id, room_data in get_all_rooms().items():
        if player_id in room_data['players']:
            player = room_data['players'][player_id]
            player['sid'] = request.sid
            player['status'] = player.get('status', 'alive')
            join_room(room_id)
            save_room(room_id, room_data)
            emit('session_restored', {'room_id': room_id, 'is_host': room_data['host'] == player_id, 'role': player['role'], 'status': player['status']})
            update_player_list(room_id)
            print(f"Jugador {player['name']} ({player_id}) reconectado a la sala {room_id}.")
            return

@socketio.on('create_room')
def handle_create_room(data):
    player_name = data.get('name', 'Anónimo')
    player_sid = request.sid
    player_id = str(uuid.uuid4())
    room_id = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ', k=5))
    new_room = {'players': {player_id: {'name': player_name, 'role': None, 'status': 'alive', 'sid': player_sid}}, 'game_state': 'lobby', 'host': player_id}
    save_room(room_id, new_room)
    join_room(room_id)
    print(f"Sala {room_id} creada por {player_name} ({player_id}).")
    emit('room_created', {'room_id': room_id, 'playerId': player_id, 'is_host': True})
    update_player_list(room_id)

@socketio.on('join_room')
def handle_join_room(data):
    player_name = data.get('name', 'Anónimo')
    room_id = data.get('room_id', '').upper()
    player_sid = request.sid
    player_id = str(uuid.uuid4())
    room = get_room(room_id)
    if room:
        if room['game_state'] == 'lobby':
            room['players'][player_id] = {'name': player_name, 'role': None, 'status': 'alive', 'sid': player_sid}
            save_room(room_id, room)
            join_room(room_id)
            print(f"{player_name} se unió a la sala {room_id}.")
            emit('room_joined', {'room_id': room_id, 'playerId': player_id, 'is_host': False})
            update_player_list(room_id)
        else:
            emit('error', {'message': 'El juego ya ha comenzado en esta sala.'})
    else:
        emit('error', {'message': 'La sala no existe.'})

@socketio.on('disconnect')
def handle_disconnect():
    player_sid = request.sid
    for room_id, room_data in get_all_rooms().items():
        for player_id, player in room_data['players'].items():
            if player.get('sid') == player_sid:
                print(f"Jugador {player['name']} desconectado temporalmente.")
                player['sid'] = None
                save_room(room_id, room_data)
                return

@socketio.on('leave_room')
def handle_leave_room(data):
    room_id = data.get('room_id')
    player_sid = request.sid
    room = get_room(room_id)
    if not room: return
    player_id_to_remove = next((pid for pid, p in room['players'].items() if p.get('sid') == player_sid), None)
    if player_id_to_remove:
        player_name = room['players'][player_id_to_remove]['name']
        del room['players'][player_id_to_remove]
        print(f"Jugador {player_name} ha salido de la sala {room_id}.")
        leave_room(room_id)
        if not room['players']:
            delete_room(room_id)
            print(f"Sala {room_id} vacía. Eliminada.")
        else:
            save_room(room_id, room)
            update_player_list(room_id)

@socketio.on('start_game')
def handle_start_game(data):
    room_id = data.get('room_id')
    custom_roles = data.get('roles', {})
    room = get_room(room_id)
    if not room or room.get('game_state') == 'in_game': return
    host_id = next((pid for pid, p in room['players'].items() if p.get('sid') == request.sid), None)
    if not host_id or host_id != room.get('host'): return
    players_ids = list(room['players'].keys())
    role_list = [role for role, count in custom_roles.items() for _ in range(int(count))]
    if len(role_list) != len(players_ids):
        emit('error', {'message': 'El número de roles no coincide con el de jugadores.'})
        return
    
    # Asigna los roles en la variable local
    random.shuffle(role_list)
    for player_id, role in zip(players_ids, role_list):
        room['players'][player_id]['role'] = role
    
    # Actualiza el estado del juego
    room['game_state'] = 'in_game'
    
    # --- ¡LA CORRECCIÓN CLAVE! ---
    # Primero guardamos todo el estado nuevo en la base de datos permanente.
    save_room(room_id, room)
    # --- FIN DE LA CORRECCIÓN ---

    # Solo DESPUÉS de guardar, notificamos a los jugadores.
    for player_id in players_ids:
        player = room['players'][player_id]
        emit('role_assigned', {'role': player['role']}, to=player.get('sid'))
    
    emit('game_started', to=room_id)
    update_player_list(room_id)

@socketio.on('kill_player')
def handle_kill_player(data):
    room_id = data.get('room_id')
    target_id = data.get('target_id')
    killer_sid = request.sid
    room = get_room(room_id)
    if not room: return
    killer_id = next((pid for pid, p in room['players'].items() if p.get('sid') == killer_sid), None)
    if not killer_id: return
    killer_info = room['players'].get(killer_id)
    victim_info = room['players'].get(target_id)
    if not (killer_info and victim_info and victim_info['status'] == 'alive' and killer_info['status'] == 'alive'): return
    victim_info['status'] = 'dead'
    emit('you_died', {'killer_name': killer_info['name']}, to=victim_info.get('sid'))
    emit('player_died', {'victim_name': victim_info['name'], 'victim_role': victim_info['role']}, to=room_id)
    if victim_info['role'] == killer_info['role']:
        killer_info['status'] = 'dead'
        emit('you_died', {'killer_name': 'a ti mismo por traición'}, to=killer_info.get('sid'))
        socketio.sleep(0.1) 
        emit('player_died', {'victim_name': killer_info['name'], 'victim_role': killer_info['role']}, to=room_id)
    save_room(room_id, room)
    update_player_list(room_id)

@socketio.on('end_game')
def handle_end_game(data):
    room_id = data.get('room_id')
    room = get_room(room_id)
    if not room: return
    host_id_request = next((pid for pid, p in room['players'].items() if p.get('sid') == request.sid), None)
    if not host_id_request or host_id_request != room.get('host'): return
    for player_id, player in room['players'].items():
        player['role'] = None
        player['status'] = 'alive'
    room['game_state'] = 'lobby'
    save_room(room_id, room)
    emit('game_ended', to=room_id)
    update_player_list(room_id)
    print(f"Partida finalizada por el anfitrión en la sala {room_id}.")

def update_player_list(room_id):
    room = get_room(room_id)
    if room:
        player_data = [{'id': pid, 'name': p['name'], 'status': p['status']} for pid, p in room['players'].items()]
        emit('update_players', {'players': player_data, 'game_state': room['game_state']}, to=room_id)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
