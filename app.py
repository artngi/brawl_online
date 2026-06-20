import os
import uuid
from flask import Flask, request
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
# すべてのオリジンからのCORS接続を許可し、リアルタイムSocket.IOを確立
socketio = SocketIO(app, cors_allowed_origins="*")

# プレイヤー情報管理: { sid: { "id": sid, "name": name, "lobby_id": lobby_id, "slot": slot, "brawler": brawler, "gadget": gadget, "sp": sp, "status": "idle"|"lobby"|"playing" } }
players = {}
# ロビー情報管理: { lobby_id: { "id": lobby_id, "host_id": host, "members": [sid1, sid2], "mode": "gemgrab" } }
lobbies = {}

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
        "slot": 0,
        "brawler": "pius",
        "gadget": "shield_absorb",
        "sp": "auto_aim",
        "status": "idle" # idle, lobby, playing
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

def broadcast_online_players():
    # 接続中の全員に現在のオンラインプレイヤー一覧を送信
    socketio.emit('online_players', list(players.values()))

@socketio.on('join_server')
def handle_join_server(data):
    sid = request.sid
    if sid in players:
        players[sid]["name"] = data.get("name", "ブロワラー")
        players[sid]["brawler"] = data.get("brawler", "pius")
        players[sid]["gadget"] = data.get("gadget", "shield_absorb")
        players[sid]["sp"] = data.get("sp", "auto_aim")
    broadcast_online_players()

@socketio.on('send_invite')
def handle_send_invite(data):
    # 特定のプレイヤーに招待を送信
    target_sid = data.get("target_id")
    sender_sid = request.sid
    if target_sid in players and sender_sid in players:
        emit('receive_invite', {
            "sender_id": sender_sid,
            "sender_name": players[sender_sid]["name"]
        }, room=target_sid)

@socketio.on('respond_invite')
def handle_respond_invite(data):
    sender_id = data.get("sender_id") # 招待を送った側
    response = data.get("response") # "accept" or "decline"
    guest_id = request.sid # 招待を受けた側

    if response == "accept":
        if sender_id in players and guest_id in players:
            # 送信側のロビーを取得、なければ新規作成
            lobby_id = players[sender_id]["lobby_id"]
            if not lobby_id:
                lobby_id = str(uuid.uuid4())
                lobbies[lobby_id] = {
                    "id": lobby_id,
                    "host_id": sender_id,
                    "members": [sender_id],
                    "mode": "gemgrab"
                }
                players[sender_id]["lobby_id"] = lobby_id
                players[sender_id]["slot"] = 0
                players[sender_id]["status"] = "lobby"
                join_room(lobby_id, sid=sender_id)

            # 受信側をロビーに追加
            lobby = lobbies[lobby_id]
            if guest_id not in lobby["members"]:
                lobby["members"].append(guest_id)
                players[guest_id]["lobby_id"] = lobby_id
                players[guest_id]["status"] = "lobby"
                # 空いているスロットを割り当て
                assigned_slot = find_empty_slot(lobby_id)
                players[guest_id]["slot"] = assigned_slot
                join_room(lobby_id, sid=guest_id)
            
            # ロビー状態を全員に同期
            sync_lobby_state(lobby_id)
            broadcast_online_players()
    else:
        # 拒否されたことを送信側に通知
        if sender_id in players:
            emit('invite_declined', {"guest_name": players[guest_id]["name"]}, room=sender_id)

def find_empty_slot(lobby_id):
    lobby = lobbies.get(lobby_id)
    if not lobby:
        return 1
    occupied_slots = [players[m]["slot"] for m in lobby["members"] if m in players]
    for s in range(0, 17): # 0~16のスロット
        if s not in occupied_slots:
            return s
    return 1

def leave_lobby_logic(sid, lobby_id):
    if lobby_id in lobbies:
        lobby = lobbies[lobby_id]
        if sid in lobby["members"]:
            lobby["members"].remove(sid)
            leave_room(lobby_id, sid=sid)
        
        if sid in players:
            players[sid]["lobby_id"] = None
            players[sid]["status"] = "idle"
            players[sid]["slot"] = 0

        # ホストが抜けたら次の人をホストにするか、ロビーを解散
        if lobby["host_id"] == sid:
            if len(lobby["members"]) > 0:
                lobby["host_id"] = lobby["members"][0]
                sync_lobby_state(lobby_id)
            else:
                lobbies.pop(lobby_id, None)
        else:
            sync_lobby_state(lobby_id)

@socketio.on('leave_lobby')
def handle_leave_lobby():
    sid = request.sid
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id:
            leave_lobby_logic(sid, lobby_id)
    broadcast_online_players()

@socketio.on('update_lobby_settings')
def handle_update_lobby_settings(data):
    sid = request.sid
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id and lobbies[lobby_id]["host_id"] == sid:
            lobbies[lobby_id]["mode"] = data.get("mode", "gemgrab")
            sync_lobby_state(lobby_id)

@socketio.on('select_slot')
def handle_select_slot(data):
    sid = request.sid
    target_slot = data.get("slot")
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id and lobby_id in lobbies:
            lobby = lobbies[lobby_id]
            # 既に他のプレイヤーがそのスロットを使っていないかチェック
            slot_occupied = False
            for m in lobby["members"]:
                if m != sid and players[m]["slot"] == target_slot:
                    slot_occupied = True
                    break
            
            if not slot_occupied:
                players[sid]["slot"] = target_slot
                sync_lobby_state(lobby_id)

@socketio.on('sync_character_custom')
def handle_sync_character_custom(data):
    sid = request.sid
    if sid in players:
        players[sid]["brawler"] = data.get("brawler")
        players[sid]["gadget"] = data.get("gadget")
        players[sid]["sp"] = data.get("sp")
        lobby_id = players[sid]["lobby_id"]
        if lobby_id:
            sync_lobby_state(lobby_id)

def sync_lobby_state(lobby_id):
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
            "members": member_data
        }, room=lobby_id)

# 試合の開始
@socketio.on('start_match')
def handle_start_match():
    sid = request.sid
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id and lobbies[lobby_id]["host_id"] == sid:
            for m in lobbies[lobby_id]["members"]:
                if m in players:
                    players[m]["status"] = "playing"
            emit('match_started', room=lobby_id)
            broadcast_online_players()

# プレイ中の同期パケット
@socketio.on('game_sync_state')
def handle_game_sync_state(data):
    sid = request.sid
    if sid in players:
        lobby_id = players[sid]["lobby_id"]
        if lobby_id:
            data["sender_sid"] = sid
            # 自分以外の同じロビーのメンバーに座標などの状態を送信
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
