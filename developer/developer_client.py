import socket
import sys
import os
import json

# --- 路徑設定 ---
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
if project_root not in sys.path:
    sys.path.append(project_root)

from common.protocol import send_json, recv_json, send_file

try:
    from common.utils import zip_dir
except ImportError:
    print("[!] 警告: 找不到 common.utils.zip_dir，請確保該檔案存在。")
    def zip_dir(src, dst):
        return False

SERVER_IP = '140.113.17.12'
SERVER_PORT = 18000


class DeveloperClient:
    def __init__(self):
        self.sock = None
        self.is_connected = False
        self.username = None

    def connect(self):
        if self.is_connected:
            return True
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((SERVER_IP, SERVER_PORT))
            self.is_connected = True
            print(f"[*] 已連線至 Server {SERVER_IP}:{SERVER_PORT}")
            return True
        except ConnectionRefusedError:
            print("[!] 無法連線至 Server，請確認 Server 是否已啟動。")
            return False
        except Exception as e:
            print(f"[!] 連線錯誤: {e}")
            return False

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.sock = None
        self.is_connected = False

    def send_request(self, data):
        if not self.sock or not self.is_connected:
            if not self.connect():
                return None
        try:
            ok = send_json(self.sock, data)
            if not ok:
                print("[!] 發送請求失敗")
                self.close()
                return None

            response = recv_json(self.sock)
            if response is None:
                print("[!] 等待回應超時或連線中斷")
                self.close()
                return None
            return response
        except Exception as e:
            print(f"[!] 通訊錯誤: {e}")
            self.close()
            return None

    def ping_server(self):
        res = self.send_request({'cmd': 'PING'})
        return bool(res and res.get('status') == 'OK')

    def start(self):
        self.connect()
        try:
            while True:
                print("\n=== Developer Client (未登入) ===")
                status = "已連線" if self.is_connected else "未連線"
                print(f"狀態: {status}")
                print("1. 登入 (Login)")
                print("2. 註冊 (Register)")
                print("3. 離開 (Exit)")

                choice = input("請選擇功能 (1-3): ").strip()

                if choice in ['1', '2'] and not self.is_connected:
                    if not self.connect():
                        continue

                if choice == '1':
                    self.handle_login()
                elif choice == '2':
                    self.handle_register()
                elif choice == '3':
                    print("Bye!")
                    break
                else:
                    print("輸入錯誤，請重新輸入。")
        except KeyboardInterrupt:
            print("\n強制結束程式")
        finally:
            self.close()

    def handle_register(self):
        print("\n--- 註冊開發者帳號 ---")
        user = input("帳號: ").strip()
        pwd = input("密碼: ").strip()
        req = {'cmd': 'REGISTER', 'user': user, 'pwd': pwd, 'role': 'developer'}
        res = self.send_request(req)
        if res:
            print(f"[{'成功' if res.get('status')=='OK' else '失敗'}] {res.get('msg')}")

    def handle_login(self):
        print("\n--- 登入 ---")
        user = input("帳號: ").strip()
        pwd = input("密碼: ").strip()

        # ✅ 必帶 role
        req = {'cmd': 'LOGIN', 'user': user, 'pwd': pwd, 'role': 'developer'}
        res = self.send_request(req)

        if res:
            if res.get('status') == 'OK':
                print(f"[成功] {res.get('msg')}")
                self.username = user
                self.dashboard()
            else:
                print(f"[失敗] {res.get('msg')}")

    def dashboard(self):
        while True:
            print(f"\n=== 開發者後台: {self.username} ===")
            print("1. 上架新遊戲 (Upload New Game)")
            print("2. 更新遊戲 (Update Existing Game)")
            print("3. 列出我的遊戲 (List Games)")
            print("4. 下架/重新上架/刪除 (Unpublish / Publish / Delete)")
            print("5. 登出 (Logout)")

            choice = input("請選擇功能 (1-5): ").strip()

            if not self.ping_server():
                print("[!] 連線已中斷，嘗試重連...")
                if not self.connect():
                    print("[!] 重連失敗，返回主選單")
                    break

            if choice == '1':
                self.handle_upload()
            elif choice == '2':
                self.handle_update()
            elif choice == '3':
                self.list_my_games()
            elif choice == '4':
                self.handle_unpublish_menu()
            elif choice == '5':
                self.send_request({'cmd': 'LOGOUT'})
                self.username = None
                print("已登出")
                break
            else:
                print("輸入錯誤")

    def validate_game_config(self, game_dir):
        config_path = os.path.join(game_dir, 'game_config.json')
        if not os.path.exists(config_path):
            return False, "找不到 game_config.json"
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            if 'exe_cmd' not in config and ('exe_cmd_host' not in config or 'exe_cmd_join' not in config):
                return False, "設定檔缺少必要欄位: exe_cmd（或 exe_cmd_host + exe_cmd_join）"
            return True, "驗證通過"
        except json.JSONDecodeError:
            return False, "設定檔格式錯誤 (非有效 JSON)"
        except Exception as e:
            return False, f"讀取錯誤: {e}"

    def list_my_games(self):
        req = {'cmd': 'LIST_GAMES', 'user': self.username}
        res = self.send_request(req)

        if res is None:
            print("[!] 無法取得遊戲列表: 連線失敗或無回應")
            return None

        if res.get('status') != 'OK':
            print(f"[!] 無法取得遊戲列表: Server 回應錯誤 ({res.get('msg', '未知原因')})")
            return None

        games = res.get('games')
        if games is None:
            print("[!] 資料異常: Server 回應缺少 'games' 欄位")
            return None

        if not games:
            print("\n[提示] 您目前沒有上架任何遊戲。")
            return None

        print(f"\n--- {self.username} 的遊戲列表 ---")
        print(f"{'No.':<5} {'Game Name':<20} {'Version':<10} {'Published':<10} {'Description'}")
        print("-" * 80)
        for i, g in enumerate(games):
            pub = "YES" if g.get('published', True) else "NO"
            print(f"{i+1:<5} {g.get('name',''):<20} {g.get('version',''):<10} {pub:<10} {g.get('description','')}")
        print("-" * 80)

        return games

    def _check_game_name_available(self, game_name: str) -> bool:
        res = self.send_request({'cmd': 'CHECK_GAME_NAME', 'game_name': game_name})
        if res is None or res.get('status') != 'OK':
            print("[!] 無法檢查遊戲名稱（連線或伺服器錯誤），請重試")
            return False
        return bool(res.get('available', False))

    def handle_upload(self):
        print("\n--- 上架新遊戲 ---")
        self._upload_flow(is_update=False)

    def handle_update(self):
        print("\n--- 更新已上架遊戲 ---")
        games = self.list_my_games()
        if not games:
            return

        selected_game = None
        while True:
            choice = input("請輸入要更新的遊戲編號 (輸入 0 取消): ").strip()
            if choice == '0':
                return
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(games):
                    selected_game = games[idx]
                    break
            print("[!] 輸入錯誤。")

        self._upload_flow(is_update=True, game_data=selected_game)

    def handle_unpublish_menu(self):
        print("\n--- 下架/重新上架/刪除 ---")
        games = self.list_my_games()
        if not games:
            return

        selected = None
        while True:
            choice = input("請輸入要操作的遊戲編號 (輸入 0 取消): ").strip()
            if choice == '0':
                return
            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(games):
                    selected = games[idx]
                    break
            print("[!] 輸入錯誤。")

        game_name = selected['name']

        print("\n你要做什麼？")
        print("1) 軟下架（玩家看不到、不能新下載/新房間）")
        print("2) 重新上架（恢復可見）")
        print("3) 永久刪除（DB + storage zip 都刪）")

        act = input("請選擇 (1-3, 其他取消): ").strip()
        if act not in {'1', '2', '3'}:
            return

        if act == '1':
            print("\n[提示] 下架後：一般玩家列表將不顯示此遊戲；不可新下載/新開房。")
            if input("確認下架？(y/n): ").strip().lower() != 'y':
                return
            res = self.send_request({
                'cmd': 'UNPUBLISH_GAME',
                'user': self.username,
                'game_name': game_name,
                'published': False
            })
            if res and res.get('status') == 'OK':
                print("[成功] 已下架")
            else:
                print(f"[失敗] {None if res is None else res.get('msg')}")

        elif act == '2':
            if input("確認重新上架？(y/n): ").strip().lower() != 'y':
                return
            res = self.send_request({
                'cmd': 'UNPUBLISH_GAME',
                'user': self.username,
                'game_name': game_name,
                'published': True
            })
            if res and res.get('status') == 'OK':
                print("[成功] 已重新上架")
            else:
                print(f"[失敗] {None if res is None else res.get('msg')}")

        else:
            print("\n[警告] 永久刪除後：玩家端未來一定找不到；storage 裡的 zip 也會刪除。")
            if input("真的要永久刪除？(type DELETE to confirm): ").strip() != 'DELETE':
                print("[取消] 未刪除")
                return
            res = self.send_request({
                'cmd': 'DELETE_GAME',
                'user': self.username,
                'game_name': game_name
            })
            if res and res.get('status') == 'OK':
                print("[成功] 已永久刪除")
            else:
                print(f"[失敗] {None if res is None else res.get('msg')}")

    def _upload_flow(self, is_update=False, game_data=None):
        if is_update:
            game_name = game_data['name']
            print(f"[*] 更新遊戲: {game_name}")
        else:
            while True:
                game_name = input("遊戲名稱 (ID) [必填]: ").strip()
                if not game_name:
                    print("[!] 錯誤：遊戲名稱不能為空。")
                    continue
                if not self._check_game_name_available(game_name):
                    print("[!] 遊戲名已被使用，請換一個名字。")
                    continue
                break

        while True:
            prompt = f"新版本號 (目前的: {game_data['version']})" if is_update else "版本號 (e.g. 1.0)"
            version = input(f"{prompt} [必填]: ").strip()
            if version:
                break
            print("[!] 錯誤：版本號不能為空。")

        while True:
            desc_prompt = "遊戲簡介 [Enter 沿用]" if is_update else "遊戲簡介 [必填]"
            description = input(f"{desc_prompt}: ").strip()
            if is_update and not description:
                description = game_data.get('description', '')
            if description:
                break
            print("[!] 錯誤：簡介不能為空。")

        while True:
            default_path = os.path.join(current_dir, "games", game_name)
            user_path = input(f"遊戲資料夾路徑 [預設: {default_path}]: ").strip()
            game_dir = user_path if user_path else default_path

            if not os.path.exists(game_dir):
                print(f"[!] 錯誤：路徑不存在 '{game_dir}'")
                continue
            if not os.path.isdir(game_dir):
                print("[!] 錯誤：非資料夾")
                continue

            valid, msg = self.validate_game_config(game_dir)
            if not valid:
                print(f"[!] 驗證失敗: {msg}")
                if input("是否修正後重試? (y/n): ").lower() != 'y':
                    return
                continue

            print("[ok] 驗證通過")
            break

        print("[*] 正在壓縮...")
        temp_zip = "temp_upload.zip"
        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except:
                pass

        z = zip_dir(game_dir, temp_zip)
        if not z and not os.path.exists(temp_zip):
            print("[!] 壓縮失敗")
            return

        file_size = os.path.getsize(temp_zip)
        req = {
            'cmd': 'UPLOAD_REQUEST',
            'user': self.username,
            'game_name': game_name,
            'version': version,
            'description': description,
            'file_size': file_size
        }

        print(f"[*] 發送請求 (Size: {file_size} bytes)...")
        res = self.send_request(req)

        if res is None:
            print("[!] 連線失敗")
        elif res.get('status') == 'READY':
            print("[*] 傳輸檔案...")
            ok = send_file(self.sock, temp_zip)
            if ok:
                final = recv_json(self.sock)
                if final and final.get('status') == 'OK':
                    print(f"[成功] {final.get('msg')}")
                else:
                    print(f"[失敗] Server: {final}")
            else:
                print("[!] 傳輸中斷")
        else:
            print(f"[失敗] {res.get('msg', 'Unknown error')}")

        if os.path.exists(temp_zip):
            try:
                os.remove(temp_zip)
            except:
                pass


if __name__ == "__main__":
    client = DeveloperClient()
    client.start()
