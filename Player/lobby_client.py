import os
import sys
import json
import socket
import zipfile
import subprocess
import threading
import time

# --- 路徑設定 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from common.protocol import send_json, recv_json, recv_file

SERVER_IP = '140.113.17.12'
SERVER_PORT = 18000

DOWNLOADS_DIR = os.path.join(current_dir, "downloads")


def make_readonly_recursive(path: str):
    for root, dirs, files in os.walk(path):
        for d in dirs:
            p = os.path.join(root, d)
            try:
                os.chmod(p, 0o555)
            except:
                pass
        for f in files:
            p = os.path.join(root, f)
            try:
                os.chmod(p, 0o444)
            except:
                pass


def get_local_ip_guess() -> str:
    """
    取得本機「可能」對外的 IP（多網卡/校網/VPN 環境不保證 100% 正確）
    但作為 host 主動回報（reported ip）是合理的。
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return ""


class LobbyClient:
    def __init__(self):
        self.sock = None
        self.connected = False
        self.username = None
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    # ---------- network ----------
    def connect(self):
        if self.connected:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((SERVER_IP, SERVER_PORT))
            self.connected = True
            return True
        except Exception as e:
            print(f"[!] 無法連線到 Server: {e}")
            self.connected = False
            return False

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except:
            pass
        self.sock = None
        self.connected = False

    def request(self, obj: dict):
        if not self.connected and not self.connect():
            return None
        try:
            if not send_json(self.sock, obj):
                self.close()
                return None
            res = recv_json(self.sock)
            if res is None:
                self.close()
                return None
            return res
        except Exception:
            self.close()
            return None

    # ---------- utils ----------
    def _probe_tcp(self, ip: str, port: int, timeout: float = 1.0) -> bool:
        ip = (ip or "").strip()
        if not ip:
            return False
        try:
            with socket.create_connection((ip, int(port)), timeout=timeout):
                return True
        except:
            return False

    def _choose_connect_ip(self, room: dict, port: int) -> str:
        """
        依序嘗試:
        1) reported_host_ip (通常是 LAN / private)
        2) observed_host_ip (通常是 public)
        3) host_ip (server 目前對外欄位，可能=reported 或 observed)
        找到能連通的就用它。
        """
        reported = (room.get("reported_host_ip") or "").strip()
        observed = (room.get("observed_host_ip") or "").strip()
        host_ip = (room.get("host_ip") or "").strip()

        candidates = []
        if reported:
            candidates.append(reported)
        if observed and observed not in candidates:
            candidates.append(observed)
        if host_ip and host_ip not in candidates:
            candidates.append(host_ip)

        for ip in candidates:
            if self._probe_tcp(ip, port, timeout=1.0):
                return ip

        print(f"[!] 無法連線到 host（port={port}），已嘗試: {candidates}")
        return ""

    # ---------- auth ----------
    def register(self):
        print("\n--- 玩家註冊 ---")
        u = input("帳號: ").strip()
        p = input("密碼: ").strip()
        res = self.request({'cmd': 'REGISTER', 'user': u, 'pwd': p, 'role': 'player'})
        if res:
            print(f"[{res.get('status')}] {res.get('msg')}")
        else:
            print("[!] 連線失敗")

    def login(self):
        print("\n--- 玩家登入 ---")
        u = input("帳號: ").strip()
        p = input("密碼: ").strip()
        res = self.request({'cmd': 'LOGIN', 'user': u, 'pwd': p, 'role': 'player'})
        if not res:
            print("[!] 連線失敗")
            return False
        if res.get('status') != 'OK':
            print(f"[FAIL] {res.get('msg')}")
            return False

        self.username = u
        os.makedirs(os.path.join(DOWNLOADS_DIR, self.username), exist_ok=True)
        print("[OK] 登入成功")
        return True

    def logout(self):
        self.request({'cmd': 'LOGOUT'})
        self.username = None
        print("[*] 已登出")

    # ---------- store ----------
    def list_store(self):
        res = self.request({'cmd': 'LIST_PUBLIC_GAMES'})
        if res is None:
            print("[!] 列表載入失敗（連線失敗）")
            return None
        if res.get('status') != 'OK':
            print(f"[!] 列表載入失敗: {res.get('msg')}")
            return None

        games = res.get('games', [])
        if not games:
            print("\n[提示] 目前沒有可遊玩的遊戲。")
            return None

        print("\n--- 遊戲商城（可下載/可遊玩）---")
        print(f"{'No.':<5} {'Game Name':<18} {'Author':<12} {'Latest':<8} Description")
        print("-" * 90)
        for i, g in enumerate(games):
            desc = g.get('description') or "尚未提供簡介"
            print(f"{i+1:<5} {g.get('name',''):<18} {g.get('uploader',''):<12} {g.get('latest_version',''):<8} {desc}")
        print("-" * 90)
        return games

    def game_detail(self, game_name: str):
        res = self.request({'cmd': 'GET_GAME_DETAIL', 'game_name': game_name})
        if res is None:
            print("[!] 詳細資訊載入失敗（連線失敗）")
            return None
        if res.get('status') != 'OK':
            print(f"[!] 無法取得詳細資訊: {res.get('msg')}")
            return None
        return res.get('detail')

    # ---------- local paths ----------
    def local_paths(self, game_name: str, version: str):
        base = os.path.join(DOWNLOADS_DIR, self.username, game_name, version)
        zip_path = os.path.join(base, f"{game_name}_{version}.zip")
        extracted = os.path.join(base, "extracted")
        return base, zip_path, extracted

    def is_installed(self, game_name: str, version: str) -> bool:
        _, _, extracted = self.local_paths(game_name, version)
        return os.path.exists(extracted) and os.path.isdir(extracted)

    # ---------- download/install ----------
    def download_game(self, game_name: str, version: str):
        res = self.request({'cmd': 'DOWNLOAD_REQUEST', 'game_name': game_name, 'version': version})
        if res is None:
            print("[!] 下載失敗（連線失敗）")
            return False
        if res.get('status') != 'READY':
            print(f"[!] 下載失敗: {res.get('msg')}")
            return False

        file_size = int(res.get('file_size', 0))
        if file_size <= 0:
            print("[!] Server 回傳檔案大小異常")
            return False

        base, zip_path, _ = self.local_paths(game_name, version)
        os.makedirs(base, exist_ok=True)

        tmp_path = zip_path + ".part"
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except:
                pass

        print(f"[*] 下載中... ({file_size} bytes)")
        ok = recv_file(self.sock, tmp_path, file_size)
        if not ok:
            print("[!] 下載中斷（不會把半套檔案當成功）")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except:
                pass
            return False

        final = recv_json(self.sock)
        if not (final and final.get('status') == 'OK'):
            print(f"[!] 下載完成但回應異常: {final}")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except:
                pass
            return False

        os.replace(tmp_path, zip_path)
        print(f"[成功] 已下載到: {zip_path}")
        return True

    def install_game(self, game_name: str, version: str):
        base, zip_path, extracted = self.local_paths(game_name, version)
        if not os.path.exists(zip_path):
            print("[!] 找不到 zip，請先下載")
            return False

        if os.path.exists(extracted):
            print("[OK] 已安裝過（同版本），略過解壓")
            return True

        os.makedirs(extracted, exist_ok=True)
        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(extracted)
        except Exception as e:
            print(f"[!] 解壓失敗: {e}")
            return False

        make_readonly_recursive(extracted)
        print(f"[成功] 已安裝（解壓）到: {extracted}")
        return True

    def download_and_install(self, game_name: str, version: str):
        if self.is_installed(game_name, version):
            print(f"[OK] 最新版本 v{version} 已就緒")
            return True

        print("[*] 開始下載並安裝...")
        if not self.download_game(game_name, version):
            return False
        if not self.install_game(game_name, version):
            return False
        print("[成功] 下載並安裝完成")
        return True

    # ---------- rooms ----------
    def create_room(self, game_name: str, version: str, max_players: int = 3):
        my_ip = get_local_ip_guess()
        if my_ip:
            print(f"[*] Host 回報 IP: {my_ip}（server 仍會保留 observed IP）")
        else:
            print("[*] Host 無法推測本機 IP，改用 server observed IP")

        res = self.request({
            'cmd': 'CREATE_ROOM',
            'game_name': game_name,
            'version': version,
            'max_players': max_players,
            'host_ip': my_ip,  # ✅ host 主動回報
        })
        if not res:
            print("[!] 建房失敗（連線失敗）")
            return None
        if res.get('status') != 'OK':
            print(f"[!] 建房失敗: {res.get('msg')}")
            return None
        return res.get('room')

    def list_rooms(self):
        res = self.request({'cmd': 'LIST_ROOMS'})
        if not res:
            print("[!] 取得房間列表失敗（連線失敗）")
            return None
        if res.get('status') != 'OK':
            print(f"[!] 取得房間列表失敗: {res.get('msg')}")
            return None
        return res.get('rooms', [])

    def join_room(self, room_id: int):
        res = self.request({'cmd': 'JOIN_ROOM', 'room_id': room_id})
        if not res:
            print("[!] 加房失敗（連線失敗）")
            return None
        if res.get('status') != 'OK':
            print(f"[!] 加房失敗: {res.get('msg')}")
            return None
        return res.get('room')

    def close_room(self, room_id: int):
        self.request({'cmd': 'CLOSE_ROOM', 'room_id': room_id})

    def _start_room_guard(self, room_id: int, proc: subprocess.Popen):
        stop_flag = {'stop': False}

        def heartbeat_loop():
            while not stop_flag['stop']:
                res = self.request({'cmd': 'HEARTBEAT_ROOM', 'room_id': room_id})
                if not res or res.get('status') != 'OK':
                    break
                time.sleep(2)

        def watch_proc_loop():
            while True:
                if proc.poll() is not None:
                    self.close_room(room_id)
                    stop_flag['stop'] = True
                    break
                time.sleep(1)

        threading.Thread(target=heartbeat_loop, daemon=True).start()
        threading.Thread(target=watch_proc_loop, daemon=True).start()

    # ---------- run game ----------
    def run_game_host(self, game_name: str, version: str, room_port: int, players_min: int = 3):
        _, _, extracted = self.local_paths(game_name, version)
        if not os.path.exists(extracted):
            print("[!] 尚未安裝，不能啟動")
            return None

        cfg_path = os.path.join(extracted, "game_config.json")
        if not os.path.exists(cfg_path):
            print("[!] 安裝內容缺少 game_config.json，無法啟動")
            return None

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[!] 讀取 game_config.json 失敗: {e}")
            return None

        exe = (cfg.get("exe_cmd") or "").strip()
        if not exe:
            print("[!] game_config.json 的 exe_cmd 是空的")
            return None

        cmd = f"{exe} host --port {room_port} --players {players_min} --name {self.username}"
        print(f"[*] 啟動遊戲：{cmd}")

        try:
            p = subprocess.Popen(cmd, cwd=extracted, shell=True)
            print("[OK] 已啟動")
            return p
        except Exception as e:
            print(f"[!] 啟動失敗: {e}")
            return None

    def run_game_join(self, game_name: str, version: str, host_ip: str, room_port: int):
        _, _, extracted = self.local_paths(game_name, version)
        if not os.path.exists(extracted):
            print("[!] 尚未安裝，不能啟動")
            return False

        cfg_path = os.path.join(extracted, "game_config.json")
        if not os.path.exists(cfg_path):
            print("[!] 安裝內容缺少 game_config.json，無法啟動")
            return False

        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            print(f"[!] 讀取 game_config.json 失敗: {e}")
            return False

        exe = (cfg.get("exe_cmd") or "").strip()
        if not exe:
            print("[!] game_config.json 的 exe_cmd 是空的")
            return False

        cmd = f"{exe} join --host {host_ip} --port {room_port} --name {self.username}"
        print(f"[*] 啟動遊戲：{cmd}")

        try:
            subprocess.Popen(cmd, cwd=extracted, shell=True)
            print("[OK] 已啟動")
            return True
        except Exception as e:
            print(f"[!] 啟動失敗: {e}")
            return False

    # ---------- UI ----------
    def store_flow(self):
        games = self.list_store()
        if not games:
            return

        while True:
            s = input("\n輸入遊戲編號查看詳細（0 返回）: ").strip()
            if s == '0':
                return
            if not s.isdigit():
                print("[!] 請輸入數字")
                continue
            idx = int(s) - 1
            if not (0 <= idx < len(games)):
                print("[!] 編號錯誤")
                continue

            name = games[idx]['name']
            detail = self.game_detail(name)
            if not detail:
                continue

            latest = detail.get('latest_version')
            versions = detail.get('versions', [])

            print("\n=== 遊戲詳細資訊 ===")
            print(f"名稱: {detail.get('name')}")
            print(f"作者: {detail.get('uploader')}")
            print(f"簡介: {detail.get('description') or '尚未提供簡介'}")
            print(f"最新版本: v{latest}")

            print("可用版本:")
            for i, v in enumerate(versions):
                mark = "  [LATEST]" if str(v.get('version')) == str(latest) else ""
                print(f"  {i+1}. v{v.get('version')} - {(v.get('description') or '尚未提供簡介')}{mark}")

            while True:
                print("\n1) 一鍵下載/更新到最新（下載並安裝）  2) 啟動遊戲（永遠最新 + host建房/join加房）  3) 返回列表")
                c = input("選擇: ").strip()
                if c == '3':
                    break
                if c not in {'1', '2'}:
                    print("[!] 輸入錯誤")
                    continue

                ok = self.download_and_install(name, latest)
                if not ok:
                    continue
                if c == '1':
                    continue

                mode = input("啟動模式 host/join（預設host）: ").strip().lower()
                if mode not in {'host', 'join', ''}:
                    print("[!] 模式錯誤")
                    continue
                if mode == '':
                    mode = 'host'

                if mode == 'host':
                    room = self.create_room(name, latest, max_players=3)
                    if not room:
                        continue

                    rid = int(room.get('room_id'))
                    rport = int(room.get('port'))
                    players_now = len(room.get('players', []))
                    players_max = int(room.get('max_players', 3))

                    print(f"[OK] 已建立房間 RoomID={rid}  Ver={latest}  {players_now}/{players_max}")
                    print(f"     host_ip={room.get('host_ip')}  observed={room.get('observed_host_ip')}  reported={room.get('reported_host_ip')}")

                    proc = self.run_game_host(name, latest, room_port=rport, players_min=3)
                    if not proc:
                        self.close_room(rid)
                        continue
                    self._start_room_guard(rid, proc)

                else:
                    rooms = self.list_rooms()
                    if rooms is None:
                        continue

                    candidates = []
                    for r in rooms:
                        if r.get('game_name') != name:
                            continue
                        if str(r.get('version')) != str(latest):
                            continue
                        if r.get('status') != 'OPEN':
                            continue
                        if len(r.get('players', [])) >= int(r.get('max_players', 3)):
                            continue
                        candidates.append(r)

                    if not candidates:
                        print("[提示] 目前沒有可加入的房間（可能都滿了或 host 已 crash）")
                        continue

                    print("\n--- 房間列表（OPEN）---")
                    print(f"{'No.':<5} {'RoomID':<8} {'Game':<16} {'Ver':<8} {'Players':<10} {'Host':<10} {'ConnectIP'}")
                    print("-" * 110)
                    for i, r in enumerate(candidates):
                        pcnt = len(r.get('players', []))
                        m = int(r.get('max_players', 3))
                        reported = (r.get('reported_host_ip') or '').strip()
                        observed = (r.get('observed_host_ip') or '').strip()
                        connect_show = reported if reported else (r.get('host_ip') or '')
                        if observed and observed != connect_show:
                            connect_show = f"{connect_show} ({observed})"
                        print(f"{i+1:<5} {r.get('room_id', ''):<8} {r.get('game_name',''):<16} {r.get('version',''):<8} {str(pcnt)+'/'+str(m):<10} {r.get('host',''):<10} {connect_show}")
                    print("-" * 110)

                    pick = input("選擇要加入的房間編號 (0取消): ").strip()
                    if pick == '0':
                        continue
                    if not pick.isdigit():
                        print("[!] 請輸入數字")
                        continue
                    pidx = int(pick) - 1
                    if not (0 <= pidx < len(candidates)):
                        print("[!] 編號錯誤")
                        continue

                    target = candidates[pidx]
                    rid = int(target.get('room_id'))
                    joined_room = self.join_room(rid)
                    if not joined_room:
                        continue

                    print("[OK] 已加入房間")

                    rport = int(joined_room.get('port') or target.get('port') or 0)
                    if not rport:
                        print("[!] 房間資訊缺少 port，無法 join")
                        continue

                    # ✅ 關鍵：選出真的連得上的 IP
                    chosen_ip = self._choose_connect_ip(joined_room, rport)
                    if not chosen_ip:
                        continue

                    self.run_game_join(name, latest, host_ip=chosen_ip, room_port=rport)

            games = self.list_store()
            if not games:
                return

    def main_menu(self):
        self.connect()
        try:
            while True:
                print("\n=== Lobby Client (玩家端) ===")
                if not self.username:
                    print("1. 登入")
                    print("2. 註冊")
                    print("3. 離開")
                    c = input("選擇: ").strip()
                    if c == '1':
                        if self.login():
                            continue
                    elif c == '2':
                        self.register()
                    elif c == '3':
                        return
                    else:
                        print("[!] 輸入錯誤")
                else:
                    print(f"目前玩家：{self.username}")
                    print("1. 瀏覽商城 / 查看詳細 / 一鍵更新 / host建房 / join加房")
                    print("2. 登出")
                    c = input("選擇: ").strip()
                    if c == '1':
                        self.store_flow()
                    elif c == '2':
                        self.logout()
                    else:
                        print("[!] 輸入錯誤")
        finally:
            self.close()


if __name__ == "__main__":
    LobbyClient().main_menu()
