import os
from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import uuid

app = Flask(__name__)
# すべてのオリジンからのアクセスを許可
socketio = SocketIO(app, cors_allowed_origins="*")

# プレイヤーデータベース
# 構造: { uid: { "uid": str, "name": str, "brawler": str, "gadget": str, "sp": str, "team_id": int, "online": bool, "sid": str, "room_id": str } }
players = {}
sid_to_uid = {}

# 招待状の追跡
# { invite_id: { "from_uid": str, "to_uid": str } }
invites = {}

@app.route('/')
def index():
    return "Brawl Clone Online Server is Running!"

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
            
            # 参加していたルームがある場合は退出通知
            room_id = players[uid].get('room_id')
            if room_id:
                leave_room(room_id, sid=sid)
                players[uid]['room_id'] = None
                emit('room_update', get_room_players(room_id), to=room_id)
                
        del sid_to_uid[sid]
    broadcast_players()

@socketio.on('register_player')
def handle_register(data):
    sid = request.sid
    uid = data.get('uid')
    name = data.get('name', 'ブロワラー')
    brawler = data.get('brawler', 'shelly')
    gadget = data.get('gadget', 'random')
    sp = data.get('sp', 'random')
    team_id = data.get('team_id', 1)  # 1: 青, 2: 赤

    if not uid:
        uid = str(uuid.uuid4())

    sid_to_uid[sid] = uid
    
    players[uid] = {
        'uid': uid,
        'sid': sid,
        'name': name,
        'brawler': brawler,
        'gadget': gadget,
        'sp': sp,
        'team_id': team_id,
        'online': True,
        'room_id': players.get(uid, {}).get('room_id', None) # 既存の部屋があれば引き継ぐ
    }

    emit('registration_success', {'uid': uid})
    broadcast_players()
    
    # すでに部屋にいるならデータを再送信
    room_id = players[uid]['room_id']
    if room_id:
        join_room(room_id, sid=sid)
        emit('room_update', get_room_players(room_id), to=room_id)

@socketio.on('update_player_data')
def handle_update(data):
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        if uid in players:
            players[uid]['name'] = data.get('name', players[uid]['name'])
            players[uid]['brawler'] = data.get('brawler', players[uid]['brawler'])
            players[uid]['gadget'] = data.get('gadget', players[uid]['gadget'])
            players[uid]['sp'] = data.get('sp', players[uid]['sp'])
            players[uid]['team_id'] = data.get('team_id', players[uid]['team_id'])
            
            broadcast_players()
            
            room_id = players[uid].get('room_id')
            if room_id:
                emit('room_update', get_room_players(room_id), to=room_id)

@socketio.on('send_challenge')
def handle_challenge(data):
    sid = request.sid
    target_uid = data.get('target_uid')
    if sid in sid_to_uid:
        challenger_uid = sid_to_uid[sid]
        challenger = players.get(challenger_uid)
        target = players.get(target_uid)

        if challenger and target and target['online'] and target['sid']:
            invite_id = f"invite_{uuid.uuid4().hex[:8]}"
            invites[invite_id] = {
                'from_uid': challenger_uid,
                'to_uid': target_uid
            }
            emit('challenge_received', {
                'invite_id': invite_id,
                'challenger_uid': challenger_uid,
                'challenger_name': challenger['name']
            }, to=target['sid'])

@socketio.on('respond_challenge')
def handle_response(data):
    sid = request.sid
    invite_id = data.get('invite_id')
    accepted = data.get('accepted')

    if invite_id in invites and sid in sid_to_uid:
        invite = invites[invite_id]
        target_uid = sid_to_uid[sid]
        challenger_uid = invite['from_uid']
        
        target = players.get(target_uid)
        challenger = players.get(challenger_uid)

        if challenger and target:
            if accepted:
                # すでに別の部屋に入っている場合は退出
                for uid in [challenger_uid, target_uid]:
                    old_room = players[uid].get('room_id')
                    if old_room:
                        leave_room(old_room, sid=players[uid]['sid'])
                
                # 新しい共通ルームを作成 (ホストのUIDを部屋IDとする)
                room_id = f"room_{challenger_uid}"
                
                players[challenger_uid]['room_id'] = room_id
                players[target_uid]['room_id'] = room_id
                
                # 自分(青チーム)と相手(赤チーム)に初期設定
                players[challenger_uid]['team_id'] = 1
                players[target_uid]['team_id'] = 2

                join_room(room_id, sid=challenger['sid'])
                join_room(room_id, sid=target['sid'])

                # メンバー間で部屋同期
                emit('room_joined', {'room_id': room_id, 'is_host': True}, to=challenger['sid'])
                emit('room_joined', {'room_id': room_id, 'is_host': False}, to=target['sid'])
                
                room_players = get_room_players(room_id)
                emit('room_update', room_players, to=room_id)
            else:
                if challenger['online'] and challenger['sid']:
                    emit('challenge_rejected', {'msg': f"{target['name']}さんに招待を断られました"}, to=challenger['sid'])
            
            # 招待を削除
            del invites[invite_id]

@socketio.on('leave_room')
def handle_leave_room():
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        if uid in players:
            room_id = players[uid].get('room_id')
            if room_id:
                leave_room(room_id, sid=sid)
                players[uid]['room_id'] = None
                # ロビー（シングルプレイヤー）に戻す
                emit('room_left')
                emit('room_update', get_room_players(room_id), to=room_id)

@socketio.on('trigger_game_start')
def handle_trigger_game_start(data):
    sid = request.sid
    if sid in sid_to_uid:
        uid = sid_to_uid[sid]
        if uid in players:
            room_id = players[uid].get('room_id')
            if room_id:
                # 部屋のホストのみが開始可能
                if room_id == f"room_{uid}":
                    emit('game_start_signal', {
                        'mode': data.get('mode', 'gemgrab')
                    }, to=room_id)

def get_room_players(room_id):
    # 特定の部屋に所属しているプレイヤーを抽出
    room_members = []
    for p in players.values():
        if p.get('room_id') == room_id and p['online']:
            room_members.append({
                'uid': p['uid'],
                'name': p['name'],
                'brawler': p['brawler'],
                'gadget': p['gadget'],
                'sp': p['sp'],
                'team_id': p['team_id']
            })
    return room_members

def broadcast_players():
    # 全プレイヤーのリスト（sidなどのプライベートな値は除外）
    serialized = []
    for p in players.values():
        if p['online']:
            serialized.append({
                'uid': p['uid'],
                'name': p['name'],
                'brawler': p['brawler'],
                'online': p['online']
            })
    emit('update_players', serialized, broadcast=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
