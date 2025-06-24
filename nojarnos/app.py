# app.py (Versión 2.0)
from flask import Flask, render_template, request
from flask_socketio import SocketIO, join_room, leave_room, emit
import random

app = Flask(__name__)
app.config['SECRET_KEY'] = 'el-secreto-mejor-guardado-del-mundo-v2'
socketio = SocketIO(app)

game_rooms = {}

@app.route('/')
def index():
    return render_template('index.html')

@socketio.on('create_room')
def handle_create_room(data):
    player_name = data.get('name', 'Anónimo')
    player_sid = request.sid
    room_id = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ', k=5))
    
    game_rooms[room_id] = {
        'players': {player_sid: {'name': player_name, 'role': None, 'status': 'alive'}},
        'game_state': 'lobby',
        'host': player_sid
    }
    
    join_room(room_id)
    print(f"Sala {room_id} creada por {player_name} ({player_sid}).")
    emit('room_created', {'room_id': room_id, 'sid': player_sid, 'is_host': True})
    update_player_list(room_id)

@socketio.on('join_room')
def handle_join_room(data):
    player_name = data.get('name', 'Anónimo')
    room_id = data.get('room_id', '').upper()
    player_sid = request.sid

    if room_id in game_rooms:
        room = game_rooms[room_id]
        if room['game_state'] == 'lobby':
            room['players'][player_sid] = {'name': player_name, 'role': None, 'status': 'alive'}
            join_room(room_id)
            print(f"{player_name} se unió a la sala {room_id}.")
            emit('room_joined', {'room_id': room_id, 'sid': player_sid, 'is_host': False})
            update_player_list(room_id)
        else:
            emit('error', {'message': 'El juego ya ha comenzado en esta sala.'})
    else:
        emit('error', {'message': 'La sala no existe.'})

@socketio.on('start_game')
def handle_start_game(data):
    room_id = data.get('room_id')
    custom_roles = data.get('roles', {})
    
    room = game_rooms.get(room_id)
    if not room or request.sid != room.get('host'): return

    players_sids = list(room['players'].keys())
    role_list = [role for role, count in custom_roles.items() for _ in range(int(count))]
    
    if len(role_list) != len(players_sids):
        emit('error', {'message': 'El número de roles no coincide con el de jugadores.'})
        return

    random.shuffle(role_list)
    
    for sid, role in zip(players_sids, role_list):
        room['players'][sid]['role'] = role
        emit('role_assigned', {'role': role}, to=sid)

    room['game_state'] = 'in_game'
    emit('game_started', to=room_id)
    update_player_list(room_id)

@socketio.on('kill_player')
def handle_kill_player(data):
    room_id = data.get('room_id')
    target_sid = data.get('target_sid')
    killer_sid = request.sid

    room = game_rooms.get(room_id)
    if not room: return

    killer_info = room['players'].get(killer_sid)
    victim_info = room['players'].get(target_sid)

    if not (killer_info and victim_info and victim_info['status'] == 'alive'): return
    
    # ¡MODIFICACIÓN CLAVE! Hemos eliminado la comprobación de rol. Ahora todos pueden matar.
    # if killer_info['role'] != 'Assassino': return 

    victim_info['status'] = 'dead'
    emit('you_died', {'killer_name': killer_info['name']}, to=target_sid)
    emit('player_died', {'victim_name': victim_info['name'], 'victim_role': victim_info['role']}, to=room_id)
    update_player_list(room_id)

    # La lógica de fuego amigo ahora se aplica a CUALQUIER rol que mate a alguien de su mismo rol.
    if victim_info['role'] == killer_info['role']:
        killer_info['status'] = 'dead'
        emit('you_died', {'killer_name': 'a ti mismo por traición'}, to=killer_sid)
        socketio.sleep(0.1) 
        emit('player_died', {'victim_name': killer_info['name'], 'victim_role': killer_info['role']}, to=room_id)
        update_player_list(room_id)

def update_player_list(room_id):
    room = game_rooms.get(room_id)
    if room:
        player_data = [{'sid': sid, 'name': p['name'], 'status': p['status']} for sid, p in room['players'].items()]
        emit('update_players', {'players': player_data, 'game_state': room['game_state']}, to=room_id)

if __name__ == '__main__':
    socketio.run(app, debug=True, host='0.0.0.0', port=5000)
