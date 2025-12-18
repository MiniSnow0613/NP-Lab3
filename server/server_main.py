import socket
import threading
import json
import os
import sys
import time

SERVER_BUILD = "SERVER_BUILD=2025-12-18 ipfix-v2"
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from common.protocol import send_json, recv_json, recv_file, send_file

HOST = '0.0.0.0'
PORT = 18000

DEV_DB_PATH = os.path.join(current_dir, 'developers.json')
PLAYER_DB_PATH = os.path.join(current_dir, 'players.json')
GAMES_DB_PATH = os.path.join(current_dir, 'games.json')
OLD_DB_PATH = os.path.join(current_dir, 'database.json')
STORAGE_DIR = os.path.join(current_dir, 'storage')


def load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, obj):
    tmp = path + ".tmp"
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)


def version_key(v: str):
    v = str(v).strip()
    parts = v.split(".")
    out = []
    ok = True
    for p in parts:
        if p.isdigit():
            out.append(int(p))
        else:
            ok = False
            break
    if ok:
        return (0, tuple(out), v)
    return (1, v)


class AccountDB:
    def __init__(self, path, role_name):
        self.path = path
        self.role_name = role_name
        self.lock = threading.RLock()
        self.data = load_json(self.path, {})
        if not isinstance(self.data, dict):
            self.data = {}
            save_json(self.path, self.data)

    def register(self, username, password):
        username = (username or "").strip()
        password = (password or "").strip()
        if not username or not password:
            return False, "Bad username/password."

        with self.lock:
            if username in self.data:
                return False, "Username already exists."
            self.data[username] = {'password': password, 'role': self.role_name, 'history': []}
            save_json(self.path, self.data)
        return True, "Registration successful."

    def login(self, username, password):
        username = (username or "").strip()
        password = (password or "").strip()
        user = self.data.get(username)
        if not user:
            return False, "User not found."
        if user.get('password') != password:
            return False, "Wrong password."
        return True, "Login successful."


class GameDB:
    def __init__(self, path):
        self.path = path
        self.lock = threading.RLock()
        self.games = load_json(self.path, {})
        if not isinstance(self.games, dict):
            self.games = {}
            save_json(self.path, self.games)

        self._migrate_legacy_format_if_needed()
        self._ensure_published_flag()
        self._ensure_latest_version()

    def _save(self):
        with self.lock:
            save_json(self.path, self.games)

    def _migrate_legacy_format_if_needed(self):
        with self.lock:
            changed = False
            for gname, ginfo in list(self.games.items()):
                if not isinstance(ginfo, dict):
                    continue
                if isinstance(ginfo.get('versions'), dict):
                    continue
                if 'version' in ginfo and 'file_path' in ginfo:
                    v = str(ginfo.get('version'))
                    self.games[gname] = {
                        'name': ginfo.get('name', gname),
                        'uploader': ginfo.get('uploader', ''),
                        'description': ginfo.get('description', ''),
                        'published': True,
                        'latest_version': v,
                        'versions': {
                            v: {'version': v, 'file_path': ginfo.get('file_path', ''), 'description': ginfo.get('description', '')}
                        }
                    }
                    changed = True
            if changed:
                self._save()

    def _ensure_published_flag(self):
        with self.lock:
            changed = False
            for _, ginfo in self.games.items():
                if isinstance(ginfo, dict) and 'published' not in ginfo:
                    ginfo['published'] = True
                    changed = True
            if changed:
                self._save()

    def _ensure_latest_version(self):
        with self.lock:
            changed = False
            for _, ginfo in self.games.items():
                if not isinstance(ginfo, dict):
                    continue
                versions = ginfo.get('versions', {}) or {}
                if not versions:
                    continue
                best = sorted(versions.keys(), key=version_key)[-1]
                if ginfo.get('latest_version') != best:
                    ginfo['latest_version'] = best
                    changed = True
            if changed:
                self._save()

    def is_game_name_taken(self, game_name: str) -> bool:
        return game_name in self.games

    def get_game_owner(self, game_name: str) -> str:
        g = self.games.get(game_name, {})
        return g.get('uploader', '') if isinstance(g, dict) else ''

    def add_game_version(self, game_name: str, uploader: str, version: str, description: str, file_path: str):
        game_name = (game_name or '').strip()
        uploader = (uploader or '').strip()
        version = str(version or '').strip()
        if not game_name or not uploader or not version:
            raise ValueError("bad game_name/uploader/version")

        with self.lock:
            if game_name not in self.games:
                self.games[game_name] = {
                    'name': game_name,
                    'uploader': uploader,
                    'description': description or '',
                    'published': True,
                    'latest_version': version,
                    'versions': {}
                }
            else:
                owner = self.games[game_name].get('uploader', '')
                if owner and owner != uploader:
                    raise PermissionError("Game name already owned by another developer")

            if 'versions' not in self.games[game_name] or not isinstance(self.games[game_name]['versions'], dict):
                self.games[game_name]['versions'] = {}

            if description:
                self.games[game_name]['description'] = description

            self.games[game_name]['versions'][version] = {
                'version': version,
                'file_path': file_path,
                'description': description or ''
            }

            cur_latest = self.games[game_name].get('latest_version')
            if (cur_latest is None) or (version_key(version) >= version_key(cur_latest)):
                self.games[game_name]['latest_version'] = version

            self._save()

    def set_game_published(self, game_name: str, uploader: str, published: bool):
        with self.lock:
            if game_name not in self.games:
                return False, "Game not found."
            ginfo = self.games[game_name]
            if ginfo.get('uploader') != uploader:
                return False, "No permission to unpublish this game."
            ginfo['published'] = bool(published)
            self._save()
            return True, "OK"

    def delete_game_permanently(self, game_name: str, uploader: str, *, delete_files: bool = True):
        with self.lock:
            if game_name not in self.games:
                return False, "Game not found."
            ginfo = self.games[game_name]
            if ginfo.get('uploader') != uploader:
                return False, "No permission to delete this game."

            file_paths = []
            versions = ginfo.get('versions', {})
            if isinstance(versions, dict):
                for _, vinfo in versions.items():
                    fp = (vinfo or {}).get('file_path')
                    if fp:
                        file_paths.append(fp)

            del self.games[game_name]
            self._save()

        if delete_files:
            for fp in file_paths:
                abs_path = os.path.abspath(os.path.join(STORAGE_DIR, fp))
                storage_abs = os.path.abspath(STORAGE_DIR)
                if abs_path.startswith(storage_abs) and os.path.exists(abs_path):
                    try:
                        os.remove(abs_path)
                    except:
                        pass

        return True, "OK"

    def list_games_by_uploader(self, uploader: str):
        result = []
        for gname, ginfo in self.games.items():
            if not isinstance(ginfo, dict):
                continue
            if ginfo.get('uploader') != uploader:
                continue

            published = bool(ginfo.get('published', True))
            versions = ginfo.get('versions', {}) or {}
            for v, vinfo in versions.items():
                result.append({
                    'name': ginfo.get('name', gname),
                    'version': str(v),
                    'description': (vinfo or {}).get('description', ginfo.get('description', '')),
                    'uploader': uploader,
                    'file_path': (vinfo or {}).get('file_path', ''),
                    'published': published,
                    'latest_version': ginfo.get('latest_version')
                })

        result.sort(key=lambda x: (x.get('name', ''), version_key(x.get('version', '0'))))
        return result

    def list_public_games(self):
        result = []
        for gname, ginfo in self.games.items():
            if not isinstance(ginfo, dict):
                continue
            if not bool(ginfo.get('published', True)):
                continue

            versions = ginfo.get('versions', {}) or {}
            if not versions:
                continue

            latest_version = ginfo.get('latest_version')
            if not latest_version:
                latest_version = sorted(versions.keys(), key=version_key)[-1]

            vinfo = versions.get(latest_version, {}) or {}

            result.append({
                'name': ginfo.get('name', gname),
                'uploader': ginfo.get('uploader', ''),
                'description': vinfo.get('description') or ginfo.get('description', '') or "尚未提供簡介",
                'latest_version': latest_version
            })

        result.sort(key=lambda x: x.get('name', ''))
        return result

    def get_game_detail(self, game_name: str):
        ginfo = self.games.get(game_name)
        if not isinstance(ginfo, dict):
            return None

        versions = ginfo.get('versions', {}) or {}
        version_list = []
        for v, vinfo in versions.items():
            version_list.append({
                'version': str(v),
                'description': (vinfo or {}).get('description', ginfo.get('description', '')) or "尚未提供簡介",
                'file_path': (vinfo or {}).get('file_path', '')
            })
        version_list.sort(key=lambda x: version_key(x['version']))

        latest_version = ginfo.get('latest_version')
        if not latest_version and version_list:
            latest_version = version_list[-1]['version']

        return {
            'name': ginfo.get('name', game_name),
            'uploader': ginfo.get('uploader', ''),
            'published': bool(ginfo.get('published', True)),
            'description': ginfo.get('description', '') or "尚未提供簡介",
            'latest_version': latest_version,
            'versions': version_list
        }

    def resolve_zip_path(self, game_name: str, version: str):
        ginfo = self.games.get(game_name)
        if not isinstance(ginfo, dict):
            return None
        if not bool(ginfo.get('published', True)):
            return None

        vinfo = (ginfo.get('versions', {}) or {}).get(version)
        if not isinstance(vinfo, dict):
            return None

        fp = vinfo.get('file_path')
        if not fp:
            return None

        abs_path = os.path.abspath(os.path.join(STORAGE_DIR, fp))
        storage_abs = os.path.abspath(STORAGE_DIR)
        if not abs_path.startswith(storage_abs):
            return None
        if not os.path.exists(abs_path):
            return None
        return abs_path

    def is_published(self, game_name: str) -> bool:
        g = self.games.get(game_name)
        if not isinstance(g, dict):
            return False
        return bool(g.get('published', True))

    def has_version(self, game_name: str, version: str) -> bool:
        g = self.games.get(game_name)
        if not isinstance(g, dict):
            return False
        vers = g.get('versions', {}) or {}
        return str(version) in vers


def migrate_old_database_if_exists():
    if not os.path.exists(OLD_DB_PATH):
        return

    old = load_json(OLD_DB_PATH, {})
    if not isinstance(old, dict):
        return

    devs = load_json(DEV_DB_PATH, {})
    players = load_json(PLAYER_DB_PATH, {})
    games = load_json(GAMES_DB_PATH, {})

    changed = False

    for k, v in old.items():
        if k == 'games':
            continue
        if not isinstance(v, dict):
            continue
        role = v.get('role', 'player')
        rec = {'password': v.get('password', ''), 'role': role, 'history': v.get('history', [])}
        if role == 'developer':
            if k not in devs:
                devs[k] = rec
                changed = True
        else:
            if k not in players:
                players[k] = rec
                changed = True

    old_games = old.get('games', {})
    if isinstance(old_games, dict):
        for gname, ginfo in old_games.items():
            if gname not in games and isinstance(ginfo, dict):
                games[gname] = ginfo
                changed = True

    if changed:
        save_json(DEV_DB_PATH, devs)
        save_json(PLAYER_DB_PATH, players)
        save_json(GAMES_DB_PATH, games)


class RoomManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.next_id = 1001
        self.rooms = {}

    def _alloc_port(self, rid: int) -> int:
        return 50000 + (rid % 1000)

    def create_room(self, host_user, reported_host_ip, observed_host_ip, game_name, version, max_players=3):
        with self.lock:
            rid = self.next_id
            self.next_id += 1
            port = self._alloc_port(rid)
            now = time.time()

            reported_host_ip = (reported_host_ip or "").strip()
            observed_host_ip = (observed_host_ip or "").strip()

            # 預設給舊 client 用的 host_ip：先用 reported（LAN），沒有才 observed（public）
            host_ip = reported_host_ip if reported_host_ip else observed_host_ip

            room = {
                'room_id': rid,
                'game_name': game_name,
                'version': version,
                'host': host_user,

                'host_ip': host_ip,                    # 相容欄位
                'reported_host_ip': reported_host_ip,  # ✅ 新欄位
                'observed_host_ip': observed_host_ip,  # ✅ 新欄位

                'port': port,
                'players': [host_user],
                'max_players': int(max_players),
                'status': 'OPEN',
                'created_at': int(now),
                'last_heartbeat': now
            }
            self.rooms[rid] = room
            return room


    def list_rooms(self):
        with self.lock:
            rooms = list(self.rooms.values())
        rooms.sort(key=lambda r: r['room_id'])
        return rooms

    def get_room(self, rid):
        with self.lock:
            return self.rooms.get(rid)

    def join_room(self, rid, user):
        with self.lock:
            room = self.rooms.get(rid)
            if not room:
                return False, "Room not found."
            if room['status'] != 'OPEN':
                return False, "Room is not open."
            if user in room['players']:
                return True, "Already in room."
            if len(room['players']) >= room['max_players']:
                return False, "Room is full."
            room['players'].append(user)
            return True, "Joined."

    def leave_room(self, rid, user):
        with self.lock:
            room = self.rooms.get(rid)
            if not room:
                return False, "Room not found."
            if user not in room['players']:
                return False, "Not in room."
            room['players'].remove(user)
            if user == room['host']:
                room['status'] = 'CLOSED'
            if len(room['players']) == 0:
                room['status'] = 'CLOSED'
            return True, "Left."

    def heartbeat(self, rid: int, user: str):
        with self.lock:
            room = self.rooms.get(rid)
            if not room:
                return False, "Room not found."
            if room.get('host') != user:
                return False, "Only host can heartbeat."
            if room.get('status') != 'OPEN':
                return False, "Room is not open."
            room['last_heartbeat'] = time.time()
            return True, "OK"

    def close_room(self, rid: int, user: str):
        with self.lock:
            room = self.rooms.get(rid)
            if not room:
                return False, "Room not found."
            if room.get('host') != user:
                return False, "Only host can close."
            room['status'] = 'CLOSED'
            return True, "OK"

    def cleanup_expired(self, ttl_sec: int = 10):
        now = time.time()
        with self.lock:
            to_delete = []
            for rid, room in self.rooms.items():
                if room.get('status') != 'OPEN':
                    to_delete.append(rid)
                    continue
                last = float(room.get('last_heartbeat', room.get('created_at', 0)))
                if now - last > ttl_sec:
                    to_delete.append(rid)
            for rid in to_delete:
                self.rooms.pop(rid, None)


class GameStoreServer:
    def __init__(self, host, port):
        migrate_old_database_if_exists()

        self.dev_manager = AccountDB(DEV_DB_PATH, "developer")
        self.player_manager = AccountDB(PLAYER_DB_PATH, "player")
        self.game_db = GameDB(GAMES_DB_PATH)
        self.room_mgr = RoomManager()

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((host, port))
        self.server_socket.listen(20)
        self.server_socket.settimeout(1.0)

        self.running = True

        self.online_users = set()
        self.online_users_lock = threading.Lock()

        if not os.path.exists(STORAGE_DIR):
            os.makedirs(STORAGE_DIR)

        self.room_cleanup_thread = threading.Thread(target=self._room_cleanup_loop, daemon=True)
        self.room_cleanup_thread.start()
        print("[*]", SERVER_BUILD)
        print(f"[*] Server listening on {host}:{port}")
        print("[*]", SERVER_BUILD)

    def _room_cleanup_loop(self):
        while self.running:
            try:
                self.room_mgr.cleanup_expired(ttl_sec=10)
            except:
                pass
            time.sleep(2)

    def start(self):
        try:
            while self.running:
                try:
                    client_sock, addr = self.server_socket.accept()
                    client_sock.settimeout(None)
                    t = threading.Thread(target=self.handle_client, args=(client_sock, addr), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
        except KeyboardInterrupt:
            print("\n[*] Server stopping...")
            self.running = False
        finally:
            try:
                self.server_socket.close()
            except:
                pass

    def handle_client(self, conn, addr):
        current_user = None
        current_role = None
        observed_ip = addr[0]  # ✅ 這個就是 server 看到的來源 IP（通常是 public/NAT 後）

        try:
            while True:
                request = recv_json(conn)
                if not request:
                    break

                cmd = request.get('cmd')
                response = {'status': 'ERROR', 'msg': 'Unknown command'}

                if cmd == 'PING':
                    response = {'status': 'OK', 'msg': 'Pong'}

                elif cmd == 'REGISTER':
                    username = request.get('user')
                    password = request.get('pwd')
                    role = request.get('role', 'player')

                    if role == 'developer':
                        ok, msg = self.dev_manager.register(username, password)
                    else:
                        ok, msg = self.player_manager.register(username, password)

                    response = {'status': 'OK' if ok else 'FAIL', 'msg': msg}

                elif cmd == 'LOGIN':
                    username = request.get('user')
                    password = request.get('pwd')
                    role = request.get('role', 'player')

                    mgr = self.dev_manager if role == 'developer' else self.player_manager
                    ok, msg = mgr.login(username, password)

                    if ok:
                        with self.online_users_lock:
                            key = (role, username)
                            if key in self.online_users:
                                ok = False
                                msg = "帳號已在其他裝置登入"
                            else:
                                self.online_users.add(key)
                                current_user = username
                                current_role = role

                    response = {'status': 'OK', 'msg': msg, 'role': role} if ok else {'status': 'FAIL', 'msg': msg}

                elif cmd == 'LOGOUT':
                    if current_user and current_role:
                        with self.online_users_lock:
                            self.online_users.discard((current_role, current_user))
                    current_user = None
                    current_role = None
                    response = {'status': 'OK', 'msg': 'Logged out'}

                # ---------- Player ----------
                elif cmd == 'LIST_PUBLIC_GAMES':
                    if not current_user or current_role != 'player':
                        response = {'status': 'FAIL', 'msg': 'Permission denied'}
                    else:
                        response = {'status': 'OK', 'games': self.game_db.list_public_games()}

                elif cmd == 'GET_GAME_DETAIL':
                    if not current_user or current_role != 'player':
                        response = {'status': 'FAIL', 'msg': 'Permission denied'}
                    else:
                        game_name = (request.get('game_name') or '').strip()
                        detail = self.game_db.get_game_detail(game_name)
                        if detail is None:
                            response = {'status': 'FAIL', 'msg': 'Game not found'}
                        elif not detail.get('published', True):
                            response = {'status': 'FAIL', 'msg': 'Game is unpublished'}
                        else:
                            response = {'status': 'OK', 'detail': detail}

                elif cmd == 'DOWNLOAD_REQUEST':
                    if not current_user or current_role != 'player':
                        send_json(conn, {'status': 'FAIL', 'msg': 'Permission denied'})
                        continue

                    game_name = (request.get('game_name') or '').strip()
                    version = str(request.get('version') or '').strip()
                    if not game_name or not version:
                        send_json(conn, {'status': 'FAIL', 'msg': 'Bad request'})
                        continue

                    abs_path = self.game_db.resolve_zip_path(game_name, version)
                    if abs_path is None:
                        send_json(conn, {'status': 'FAIL', 'msg': 'Game/version not available'})
                        continue

                    file_size = os.path.getsize(abs_path)
                    send_json(conn, {'status': 'READY', 'file_size': file_size})

                    ok = send_file(conn, abs_path)
                    if not ok:
                        continue
                    send_json(conn, {'status': 'OK', 'msg': 'Download complete'})
                    continue

                # ---------- Rooms ----------
                elif cmd == 'CREATE_ROOM':
                    print("[DBG] CREATE_ROOM request =", request, " observed_ip =", observed_ip)

                    if not current_user or current_role != 'player':
                        response = {'status': 'FAIL', 'msg': 'Permission denied'}
                    else:
                        game_name = (request.get('game_name') or '').strip()
                        version = str(request.get('version') or '').strip()
                        max_players = int(request.get('max_players', 3))

                        reported_ip = (request.get('host_ip') or '').strip()

                        if not self.game_db.is_published(game_name):
                            response = {'status': 'FAIL', 'msg': 'Game is unpublished. Cannot create room.'}
                        elif not self.game_db.has_version(game_name, version):
                            response = {'status': 'FAIL', 'msg': 'Version not available.'}
                        else:
                            room = self.room_mgr.create_room(
                                host_user=current_user,
                                reported_host_ip=reported_ip,
                                observed_host_ip=observed_ip,
                                game_name=game_name,
                                version=version,
                                max_players=max_players
                            )
                            response = {'status': 'OK', 'room': room}



                elif cmd == 'LIST_ROOMS':
                    if not current_user or current_role != 'player':
                        response = {'status': 'FAIL', 'msg': 'Permission denied'}
                    else:
                        rooms = [r for r in self.room_mgr.list_rooms() if r.get('status') == 'OPEN']
                        response = {'status': 'OK', 'rooms': rooms}

                elif cmd == 'JOIN_ROOM':
                    if not current_user or current_role != 'player':
                        response = {'status': 'FAIL', 'msg': 'Permission denied'}
                    else:
                        rid = int(request.get('room_id', 0))
                        ok, msg = self.room_mgr.join_room(rid, current_user)
                        if ok:
                            room = self.room_mgr.get_room(rid)
                            response = {'status': 'OK', 'msg': msg, 'room': room}
                        else:
                            response = {'status': 'FAIL', 'msg': msg}

                elif cmd == 'LEAVE_ROOM':
                    if not current_user or current_role != 'player':
                        response = {'status': 'FAIL', 'msg': 'Permission denied'}
                    else:
                        rid = int(request.get('room_id', 0))
                        ok, msg = self.room_mgr.leave_room(rid, current_user)
                        response = {'status': 'OK', 'msg': msg} if ok else {'status': 'FAIL', 'msg': msg}

                elif cmd == 'HEARTBEAT_ROOM':
                    if not current_user or current_role != 'player':
                        response = {'status': 'FAIL', 'msg': 'Permission denied'}
                    else:
                        rid = int(request.get('room_id', 0))
                        ok, msg = self.room_mgr.heartbeat(rid, current_user)
                        response = {'status': 'OK', 'msg': msg} if ok else {'status': 'FAIL', 'msg': msg}

                elif cmd == 'CLOSE_ROOM':
                    if not current_user or current_role != 'player':
                        response = {'status': 'FAIL', 'msg': 'Permission denied'}
                    else:
                        rid = int(request.get('room_id', 0))
                        ok, msg = self.room_mgr.close_room(rid, current_user)
                        response = {'status': 'OK', 'msg': msg} if ok else {'status': 'FAIL', 'msg': msg}

                send_json(conn, response)

        except Exception as e:
            print(f"[!] Error handling client {addr}: {e}")
        finally:
            if current_user and current_role:
                with self.online_users_lock:
                    self.online_users.discard((current_role, current_user))
            try:
                conn.close()
            except:
                pass


if __name__ == "__main__":
    if not os.path.exists(STORAGE_DIR):
        os.makedirs(STORAGE_DIR)
    server = GameStoreServer(HOST, PORT)
    server.start()
