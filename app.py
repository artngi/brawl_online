import os
import uuid
from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
# すべてのオリジンからのCORS接続を許可し、リアルタイムSocket.IOを確立
socketio = SocketIO(app, cors_allowed_origins="*")

# プレイヤー情報管理: { sid: { "id": sid, "name": name, "lobby_id": lobby_id, "status": status } }
players = {}
# ロビー情報管理: { lobby_id: { "id": lobby_id, "host_id": host, "members": [sid1, sid2], "mode": "gemgrab", "slots_data": [...] } }
lobbies = {}

def broadcast_online_players():
    """全オンラインプレイヤーの一覧を全員にブロードキャスト"""
    online_list = [{
        "id": p["id"],
        "name": p["name"],
        "status": p["status"],
        "lobby_id": p["lobby_id"]
    } for p in players.values()]
    socketio.emit('online_players_update', online_list)

def broadcast_lobby_update(lobby_id):
    """ロビー内の全メンバーに最新のロビー状態とスロットデータを同期"""
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        member_data = []
        for m in lobby["members"]:
            if m in players:
                member_data.append(players[m])
        emit('lobby_update', {
            "lobby_id": lobby_id,
            "host_id": lobby["host_id"],
            "mode": lobby["mode"],
            "members": member_data,
            "slots_data": lobby["slots_data"]
        }, room=lobby_id)

def leave_lobby_logic(sid, lobby_id):
    """ロビーからの退出処理ロジック"""
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        if sid in lobby["members"]:
            lobby["members"].remove(sid)
            leave_room(lobby_id, sid=sid)
        
        if sid in players:
            players[sid]["lobby_id"] = None
            players[sid]["status"] = "idle"
            
        if not lobby["members"]:
            lobbies.pop(lobby_id, None)
        else:
            if lobby["host_id"] == sid:
                lobby["host_id"] = lobby["members"][0]
            broadcast_lobby_update(lobby_id)

@app.route('/')
def index():
    return "Brawl Clone Matchmaking Server is Running!"

@socketio.on('connect')
def handle_connect():
    sid = request.sid
    players[sid] = {
        "id": sid,
        "name": "ブロワラー",
        "lobby_id": None,
        "status": "idle"  # idle, lobby, playing
    }
    broadcast_online_players()

@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    if sid in players:
        player = players[sid]
        lobby_id = player["lobby_id"]
        if lobby_id:
            leave_lobby_logic(sid, lobby_id)
        players.pop(sid, None)
    broadcast_online_players()

@socketio.on('update_profile')
def handle_update_profile(data):
    sid = request.sid
    if sid in players:
        players[sid]["name"] = data.get("name", "ブロワラー")
        broadcast_online_players()
        lobby_id = players[sid]["lobby_id"]
        if lobby_id:
            broadcast_lobby_update(lobby_id)

@socketio.on('invite_player')
def handle_invite_player(data):
    """他のプレイヤーにロビーへの招待を送る"""
    sid = request.sid
    target_id = data.get("target_id")
    if sid in players and target_id in players:
        player = players[sid]
        lobby_id = player["lobby_id"]
        
        # ロビーがまだなければ新規作成
        if not lobby_id:
            lobby_id = str(uuid.uuid4())
            lobbies[lobby_id] = {
                "id": lobby_id,
                "host_id": sid,
                "members": [sid],
                "mode": "gemgrab",
                "slots_data": None
            }
            player["lobby_id"] = lobby_id
            player["status"] = "lobby"
            join_room(lobby_id, sid=sid)
            broadcast_lobby_update(lobby_id)
            
        # ターゲットに招待イベントを直接送信
        emit('receive_invite', {
            "from_id": sid,
            "from_name": player["name"],
            "lobby_id": lobby_id
        }, room=target_id)

@socketio.on('respond_invite')
def handle_respond_invite(data):
    """招待に対する許可・拒否の応答"""
    sid = request.sid
    lobby_id = data.get("lobby_id")
    accept = data.get("accept")
    from_id = data.get("from_id")
    
    if not accept:
        if from_id in players:
            emit('invite_declined', {"by_name": players[sid]["name"]}, room=from_id)
        return
        
    if lobby_id in lobbies and sid in players:
        # 既存のロビーがあれば抜ける
        old_lobby_id = players[sid]["lobby_id"]
        if old_lobby_id:
            leave_lobby_logic(sid, old_lobby_id)
            
        lobby = lobbies[lobby_id]
        if len(lobby["members"]) < 6:
            lobby["members"].append(sid)
            players[sid]["lobby_id"] = lobby_id
            players[sid]["status"] = "lobby"
            join_room(lobby_id, sid=sid)
            broadcast_lobby_update(lobby_id)
            broadcast_online_players()
        else:
            emit('error_message', {"message": "ロビーが満員です"}, room=sid)

@socketio.on('sync_lobby_slots')
def handle_sync_lobby_slots(data):
    """ロビー内のスロットやモード変更を全メンバーに同期"""
    sid = request.sid
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id in lobbies:
            lobbies[lobby_id]["slots_data"] = data.get("slots")
            lobbies[lobby_id]["mode"] = data.get("mode")
            emit('lobby_slots_updated', {
                "slots": lobbies[lobby_id]["slots_data"],
                "mode": lobbies[lobby_id]["mode"],
                "host_id": lobbies[lobby_id]["host_id"]
            }, room=lobby_id, include_self=False)

@socketio.on('start_match')
def handle_start_match(data):
    """ホストが試合を開始した合図を全員に送信"""
    sid = request.sid
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id and lobbies[lobby_id]["host_id"] == sid:
            lobbies[lobby_id]["slots_data"] = data.get("slots")
            for m in lobbies[lobby_id]["members"]:
                if m in players:
                    players[m]["status"] = "playing"
            emit('match_started', {
                "slots": lobbies[lobby_id]["slots_data"],
                "mode": lobbies[lobby_id]["mode"]
            }, room=lobby_id)
            broadcast_online_players()

# プレイ中の座標・ステータスのリアルタイム同期パケット
@socketio.on('game_sync_state')
def handle_game_sync_state(data):
    sid = request.sid
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id:
            data["sender_sid"] = sid
            emit('game_sync_receive', data, room=lobby_id, include_self=False)

# プレイ中の攻撃トリガー同期
@socketio.on('game_sync_action')
def handle_game_sync_action(data):
    sid = request.sid
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id:
            data["sender_sid"] = sid
            emit('game_sync_action_receive', data, room=lobby_id, include_self=False)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port)
