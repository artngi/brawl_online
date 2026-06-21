import os
from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import uuid

app = Flask(__name__)
# Enable CORS for all origins to support hosting on Render and itch.io/Canvas iframe
socketio = SocketIO(app, cors_allowed_origins="*")

# Player database
# Format: { uid: { "uid": str, "name": str, "online": bool, "sid": str, "roomId": str|None } }
players = {}
sid_to_uid = {}

# Rooms database
# Format: { room_id: { "host_uid": str, "players": { uid: { "name": str, "team": int, "brawler": str, "gadget": str, "sp": str, "ready": bool } }, "mode": str, "status": str } }
rooms = {}

@app.route('/')
def index():
    return "Brawl Clone Multiplayer Relay Server is Running!"

@socketio.on('connect')
def handle_connect():
    pass

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        if uid in players:
            players[uid]['online'] = False
            players[uid]['sid'] = None
            
            # If in a room, handle departure
            room_id = players[uid].get('roomId')
            if room_id:
                handle_leave_room_internal(uid, room_id, sid)
                
        del sid_to_uid[sid]
    broadcast_players()

@socketio.on('register_player')
def handle_register(data):
    sid = request.sid
    uid = data.get('uid')
    name = data.get('name', '名無しさん')

    if not uid:
        uid = str(uuid.uuid4())

    sid_to_uid[sid] = uid
    
    players[uid] = {
        'uid': uid,
        'sid': sid,
        'name': name,
        'online': True,
        'roomId': None
    }

    emit('registration_success', {'uid': uid})
    broadcast_players()

@socketio.on('update_player_data')
def handle_update(data):
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        if uid in players:
            players[uid]['name'] = data.get('name', players[uid]['name'])
            broadcast_players()
            
            # If player is currently in a room, update their info inside the room
            room_id = players[uid].get('roomId')
            if room_id and room_id in rooms:
                if uid in rooms[room_id]['players']:
                    rooms[room_id]['players'][uid]['name'] = players[uid]['name']
                    broadcast_room_update(room_id)

@socketio.on('send_challenge')
def handle_challenge(data):
    sid = request.sid
    target_uid = data.get('target_uid')
    if sid in sid_to_uid:
        challenger_uid = sid_to_uid[sid]
        challenger = players.get(challenger_uid)
        target = players.get(target_uid)

        if challenger and target and target['online'] and target['sid']:
            emit('challenge_received', {
                'challenger_uid': challenger_uid,
                'challenger_name': challenger['name']
            }, to=target['sid'])

@socketio.on('respond_challenge')
def handle_response(data):
    sid = request.sid
    challenger_uid = data.get('challenger_uid')
    accepted = data.get('accepted')

    if sid in sid_to_uid:
        target_uid = sid_to_uid[sid]
        target = players.get(target_uid)
        challenger = players.get(challenger_uid)

        if challenger and target:
            if accepted:
                # Create a room hosted by the challenger
                room_id = f"room_{challenger_uid}"
                rooms[room_id] = {
                    'host_uid': challenger_uid,
                    'players': {
                        challenger_uid: {
                            'name': challenger['name'],
                            'team': 1, # Default Alpha
                            'brawler': 'pius',
                            'gadget': 'random',
                            'sp': 'random',
                            'ready': True
                        },
                        target_uid: {
                            'name': target['name'],
                            'team': 2, # Default Omega
                            'brawler': 'pius',
                            'gadget': 'random',
                            'sp': 'random',
                            'ready': False
                        }
                    },
                    'mode': 'gemgrab',
                    'status': 'lobby'
                }
                
                # Assign players to this room ID
                players[challenger_uid]['roomId'] = room_id
                players[target_uid]['roomId'] = room_id

                join_room(room_id, sid=challenger['sid'])
                join_room(room_id, sid=target['sid'])

                emit('join_room_success', {
                    'room_id': room_id,
                    'is_host': True
                }, to=challenger['sid'])

                emit('join_room_success', {
                    'room_id': room_id,
                    'is_host': False
                }, to=target['sid'])
                
                broadcast_room_update(room_id)
            else:
                if challenger['online'] and challenger['sid']:
                    emit('challenge_rejected', {'msg': f"{target['name']}さんに断られました"}, to=challenger['sid'])

@socketio.on('update_lobby_settings')
def handle_lobby_settings(data):
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        room_id = data.get('room_id')
        if room_id in rooms:
            room = rooms[room_id]
            # Only host can change game mode
            if room['host_uid'] == uid:
                room['mode'] = data.get('mode', room['mode'])
                broadcast_room_update(room_id)

@socketio.on('update_lobby_player')
def handle_lobby_player(data):
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        room_id = data.get('room_id')
        if room_id in rooms and uid in rooms[room_id]['players']:
            p_data = rooms[room_id]['players'][uid]
            p_data['team'] = data.get('team', p_data['team'])
            p_data['brawler'] = data.get('brawler', p_data['brawler'])
            p_data['gadget'] = data.get('gadget', p_data['gadget'])
            p_data['sp'] = data.get('sp', p_data['sp'])
            broadcast_room_update(room_id)

@socketio.on('leave_room')
def handle_leave_room(data):
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        room_id = data.get('room_id')
        handle_leave_room_internal(uid, room_id, sid)

def handle_leave_room_internal(uid, room_id, sid):
    if room_id in rooms:
        room = rooms[room_id]
        if uid in room['players']:
            del room['players'][uid]
            
        if uid in players:
            players[uid]['roomId'] = None
            
        leave_room(room_id, sid=sid)
        emit('left_room_confirm', {}, to=sid)
        
        # If no players left, destroy the room. If host left, assign new host or destroy
        if len(room['players']) == 0:
            del rooms[room_id]
        else:
            if room['host_uid'] == uid:
                # Assign a new host
                new_host = list(room['players'].keys())[0]
                room['host_uid'] = new_host
                # Notify the new host
                new_host_sid = players[new_host]['sid']
                if new_host_sid:
                    emit('assigned_host', {}, to=new_host_sid)
            broadcast_room_update(room_id)
            
    broadcast_players()

@socketio.on('start_multiplayer_game')
def handle_start_game(data):
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        room_id = data.get('room_id')
        if room_id in rooms and rooms[room_id]['host_uid'] == uid:
            rooms[room_id]['status'] = 'playing'
            # Relay map seed or setup triggers to all in the room
            emit('game_started_trigger', {
                'mode': rooms[room_id]['mode'],
                'players': rooms[room_id]['players']
            }, to=room_id)

# --- Gameplay Sync Relays (In-Game Room Communication) ---

@socketio.on('sync_pos')
def handle_sync_pos(data):
    # Relays real-time position, direction, and character stats to others in the same room
    room_id = data.get('room_id')
    if room_id:
        emit('sync_pos_receive', data, to=room_id, include_self=False)

@socketio.on('sync_action')
def handle_sync_action(data):
    # Relays triggers (attack execution, gadget use, hypercharge activation, deaths)
    room_id = data.get('room_id')
    if room_id:
        emit('sync_action_receive', data, to=room_id, include_self=False)

@socketio.on('sync_bots')
def handle_sync_bots(data):
    # Relays bot AI states, actions, and positioning from the Host to clients
    room_id = data.get('room_id')
    if room_id:
        emit('sync_bots_receive', data, to=room_id, include_self=False)

@socketio.on('sync_map')
def handle_sync_map(data):
    # Relays map destructions (wall break), gem spawns, item locations
    room_id = data.get('room_id')
    if room_id:
        emit('sync_map_receive', data, to=room_id, include_self=False)

@socketio.on('sync_damage')
def handle_sync_damage(data):
    # Relays bullet hits and damages so health bars are synchronized perfectly
    room_id = data.get('room_id')
    if room_id:
        emit('sync_damage_receive', data, to=room_id, include_self=False)

@socketio.on('sync_game_over')
def handle_sync_game_over(data):
    # Triggered when game ends, updates room status back to lobby
    room_id = data.get('room_id')
    if room_id and room_id in rooms:
        rooms[room_id]['status'] = 'lobby'
        emit('sync_game_over_receive', data, to=room_id)

def broadcast_players():
    # Gather list of all online players excluding SIDs
    serialized = []
    for p in players.values():
        if p['online']:
            serialized.append({
                'uid': p['uid'],
                'name': p['name'],
                'roomId': p['roomId']
            })
    emit('update_players', serialized, broadcast=True)

def broadcast_room_update(room_id):
    if room_id in rooms:
        emit('room_lobby_update', rooms[room_id], to=room_id)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
