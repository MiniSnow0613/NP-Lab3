import socket
import json
import argparse
import threading
import queue
import time
import tkinter as tk
from tkinter import messagebox
import subprocess
import sys
import os

ENC = "utf-8"

# ---------------- Newline-delimited JSON helpers ----------------

def send_msg(sock: socket.socket, obj: dict) -> None:
    data = (json.dumps(obj, ensure_ascii=False) + "\n").encode(ENC)
    sock.sendall(data)

def recv_line(sock: socket.socket) -> bytes:
    buf = b""
    while b"\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("peer disconnected")
        buf += chunk
        if len(buf) > 1024 * 1024:
            raise ConnectionError("message too large")
    line, _rest = buf.split(b"\n", 1)
    return line

def recv_msg(sock: socket.socket) -> dict:
    line = recv_line(sock)
    return json.loads(line.decode(ENC))


# ---------------- Quiz data ----------------

QUESTIONS = [
    {"q": "TCP 三向交握（3-way handshake）第一個封包是？", "choices": ["SYN", "ACK", "FIN"], "ans": 0},
    {"q": "HTTP 預設 port 是？", "choices": ["21", "80", "443"], "ans": 1},
    {"q": "DNS 的主要用途是？", "choices": ["把網域名轉 IP", "加密封包", "壓縮檔案"], "ans": 0},
    {"q": "下列哪個是私有 IP 範圍的一部分？", "choices": ["8.8.8.8", "192.168.1.10", "1.1.1.1"], "ans": 1},
    {"q": "TCP 提供什麼特性？", "choices": ["不可靠傳輸", "可靠且有序傳輸", "只能廣播"], "ans": 1},
]

# ---------------- Host (Game Server + GUI) ----------------

class HostApp:
    def __init__(self, port: int, expected_players: int, round_timeout: int):
        self.port = port
        self.expected_players = max(3, expected_players)
        self.round_timeout = max(5, round_timeout)

        self.server_sock = None
        self.clients = []  # list of (sock, addr, name)
        self.clients_lock = threading.Lock()

        self.inbox = queue.Queue()
        self.running = True

        self.scores = {}          # name -> int
        self.current_round = -1
        self.current_answers = {} # name -> choice_idx
        self.round_deadline = None

        # ✅ 用來告訴外面「已經 listen 成功」(讓 host 自動 join)
        self.listening_event = threading.Event()

        # GUI
        self.root = tk.Tk()
        self.root.title("Host - Multiplayer Quiz")

        self.status_var = tk.StringVar(value="等待玩家加入...")
        tk.Label(self.root, textvariable=self.status_var, font=("Arial", 14)).pack(pady=8)

        self.players_var = tk.StringVar(value="Players: (0)")
        tk.Label(self.root, textvariable=self.players_var, font=("Arial", 12)).pack()

        self.question_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.question_var, wraplength=520, font=("Arial", 12)).pack(pady=8)

        self.choice_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.choice_var, wraplength=520, font=("Arial", 11)).pack(pady=4)

        self.score_text = tk.Text(self.root, width=60, height=8)
        self.score_text.pack(pady=6)
        self.score_text.config(state=tk.DISABLED)

        btns = tk.Frame(self.root)
        btns.pack(pady=6)

        self.start_btn = tk.Button(btns, text="Start Game", command=self.start_game, state=tk.DISABLED)
        self.start_btn.grid(row=0, column=0, padx=6)

        self.kick_btn = tk.Button(btns, text="Stop Host", command=self.stop)
        self.kick_btn.grid(row=0, column=1, padx=6)

        self.root.protocol("WM_DELETE_WINDOW", self.stop)

        # start accept thread
        self.accept_thread = threading.Thread(target=self.accept_loop, daemon=True)
        self.accept_thread.start()

        # poll inbox / timers
        self.root.after(100, self.process_inbox)
        self.root.after(200, self.tick)

    def log_scores(self):
        self.score_text.config(state=tk.NORMAL)
        self.score_text.delete("1.0", tk.END)
        items = sorted(self.scores.items(), key=lambda x: (-x[1], x[0]))
        for name, sc in items:
            self.score_text.insert(tk.END, f"{name}: {sc}\n")
        self.score_text.config(state=tk.DISABLED)

    def update_player_list(self):
        with self.clients_lock:
            names = [n for _, _, n in self.clients]
        self.players_var.set(f"Players: ({len(names)}) " + ", ".join(names))
        if len(names) >= self.expected_players and self.current_round == -1:
            self.start_btn.config(state=tk.NORMAL)
        else:
            self.start_btn.config(state=tk.DISABLED)

    def accept_loop(self):
        try:
            self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_sock.bind(("0.0.0.0", self.port))
            self.server_sock.listen(10)

            self.status_var.set(f"Host listening on port {self.port} (need {self.expected_players}+ players)")
            self.listening_event.set()  # ✅ 告訴外界：可以 join 了
        except Exception as e:
            self.inbox.put({"type": "HOST_ERROR", "msg": str(e)})
            return

        while self.running:
            try:
                self.server_sock.settimeout(1.0)
                conn, addr = self.server_sock.accept()
                conn.settimeout(None)

                msg = recv_msg(conn)
                if msg.get("type") != "HELLO":
                    conn.close()
                    continue
                name = (msg.get("name") or "").strip()
                if not name:
                    conn.close()
                    continue

                with self.clients_lock:
                    existing = {n for _, _, n in self.clients}
                    if name in existing:
                        send_msg(conn, {"type": "HELLO_FAIL", "msg": "Name already used"})
                        conn.close()
                        continue

                    self.clients.append((conn, addr, name))
                    self.scores.setdefault(name, 0)

                send_msg(conn, {"type": "HELLO_OK", "msg": "Welcome"})
                self.inbox.put({"type": "PLAYER_JOIN", "name": name})

                t = threading.Thread(target=self.client_recv_loop, args=(conn, name), daemon=True)
                t.start()

            except socket.timeout:
                continue
            except Exception:
                continue

    def client_recv_loop(self, conn: socket.socket, name: str):
        try:
            while self.running:
                msg = recv_msg(conn)
                self.inbox.put({"type": "FROM_PLAYER", "name": name, "msg": msg})
        except Exception:
            self.inbox.put({"type": "PLAYER_LEAVE", "name": name})

    def broadcast(self, obj: dict):
        dead = []
        with self.clients_lock:
            for (sock, _addr, name) in self.clients:
                try:
                    send_msg(sock, obj)
                except Exception:
                    dead.append(name)
        for n in dead:
            self.remove_player(n)

    def remove_player(self, name: str):
        with self.clients_lock:
            new_clients = []
            for (sock, addr, n) in self.clients:
                if n == name:
                    try:
                        sock.close()
                    except:
                        pass
                else:
                    new_clients.append((sock, addr, n))
            self.clients = new_clients
        self.inbox.put({"type": "PLAYER_LEFT_CONFIRMED", "name": name})

    def start_game(self):
        if self.current_round != -1:
            return
        with self.clients_lock:
            if len(self.clients) < self.expected_players:
                messagebox.showwarning("Not enough players", f"Need {self.expected_players}+ players")
                return

        self.status_var.set("遊戲開始！")
        self.current_round = 0
        self.next_round()

    def next_round(self):
        if self.current_round >= len(QUESTIONS):
            self.finish_game()
            return

        q = QUESTIONS[self.current_round]
        self.current_answers = {}
        self.round_deadline = time.time() + self.round_timeout

        self.question_var.set(f"Round {self.current_round+1}/{len(QUESTIONS)}\n{q['q']}")
        self.choice_var.set("A) " + q["choices"][0] + "\nB) " + q["choices"][1] + "\nC) " + q["choices"][2])
        self.log_scores()

        self.broadcast({
            "type": "ROUND",
            "round": self.current_round,
            "q": q["q"],
            "choices": q["choices"],
            "timeout": self.round_timeout
        })

    def finish_round(self):
        q = QUESTIONS[self.current_round]
        ans = q["ans"]

        for name, choice in self.current_answers.items():
            if choice == ans:
                self.scores[name] = self.scores.get(name, 0) + 1

        scoreboard = sorted(self.scores.items(), key=lambda x: (-x[1], x[0]))
        self.broadcast({
            "type": "RESULT",
            "round": self.current_round,
            "correct": ans,
            "answers": self.current_answers,
            "scores": scoreboard
        })

        self.log_scores()
        self.current_round += 1
        self.round_deadline = None
        self.root.after(1200, self.next_round)

    def finish_game(self):
        scoreboard = sorted(self.scores.items(), key=lambda x: (-x[1], x[0]))
        self.broadcast({"type": "GAME_OVER", "scores": scoreboard})
        self.status_var.set("Game Over")
        self.question_var.set("遊戲結束（Game Over）")
        self.choice_var.set("")
        self.log_scores()
        messagebox.showinfo("Game Over", "遊戲結束！請看分數排行。")

    def process_inbox(self):
        try:
            while True:
                evt = self.inbox.get_nowait()
                t = evt.get("type")

                if t == "HOST_ERROR":
                    messagebox.showerror("Host Error", evt.get("msg", "error"))
                    self.stop()
                    return

                if t == "PLAYER_JOIN":
                    self.status_var.set(f"{evt['name']} joined")
                    self.update_player_list()
                    self.log_scores()

                if t in ("PLAYER_LEAVE", "PLAYER_LEFT_CONFIRMED"):
                    self.status_var.set(f"{evt['name']} left")
                    self.remove_player(evt["name"]) if t == "PLAYER_LEAVE" else None
                    self.update_player_list()
                    self.log_scores()

                if t == "FROM_PLAYER":
                    name = evt["name"]
                    msg = evt["msg"]
                    if msg.get("type") == "ANSWER":
                        if self.current_round == -1 or self.round_deadline is None:
                            continue
                        if name in self.current_answers:
                            continue
                        choice = msg.get("choice")
                        if isinstance(choice, int) and 0 <= choice <= 2:
                            self.current_answers[name] = choice
                            with self.clients_lock:
                                active_names = [n for _, _, n in self.clients]
                            if all(n in self.current_answers for n in active_names):
                                self.finish_round()

        except queue.Empty:
            pass

        self.root.after(100, self.process_inbox)

    def tick(self):
        if self.current_round != -1 and self.round_deadline is not None and self.current_round < len(QUESTIONS):
            if time.time() >= self.round_deadline:
                self.finish_round()
        self.root.after(200, self.tick)

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            self.broadcast({"type": "HOST_STOP"})
        except:
            pass
        try:
            if self.server_sock:
                self.server_sock.close()
        except:
            pass
        with self.clients_lock:
            for sock, _, _ in self.clients:
                try:
                    sock.close()
                except:
                    pass
            self.clients = []
        try:
            self.root.destroy()
        except:
            pass

    def run(self):
        self.update_player_list()
        self.log_scores()
        self.root.mainloop()


# ---------------- Client (Player GUI) ----------------

class PlayerApp:
    def __init__(self, host: str, port: int, name: str):
        self.host = host
        self.port = port
        self.name = name

        self.sock = None
        self.inbox = queue.Queue()
        self.running = True

        self.round_active = False
        self.current_choices = []
        self.my_answer_sent = False
        self.timer_deadline = None

        self.root = tk.Tk()
        self.root.title(f"Player - {name}")

        self.status_var = tk.StringVar(value="Connecting...")
        tk.Label(self.root, textvariable=self.status_var, font=("Arial", 14)).pack(pady=8)

        self.q_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.q_var, wraplength=520, font=("Arial", 12)).pack(pady=8)

        self.timer_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.timer_var, font=("Arial", 11)).pack()

        self.btn_frame = tk.Frame(self.root)
        self.btn_frame.pack(pady=10)

        self.btns = []
        for i, label in enumerate(["A", "B", "C"]):
            b = tk.Button(self.btn_frame, text=f"{label})", width=18, height=2,
                          command=lambda idx=i: self.choose(idx), state=tk.DISABLED)
            b.grid(row=0, column=i, padx=6, pady=6)
            self.btns.append(b)

        self.score_text = tk.Text(self.root, width=60, height=8)
        self.score_text.pack(pady=6)
        self.score_text.config(state=tk.DISABLED)

        self.root.protocol("WM_DELETE_WINDOW", self.stop)

        self.connect()

        self.recv_thread = threading.Thread(target=self.recv_loop, daemon=True)
        self.recv_thread.start()

        self.root.after(100, self.process_inbox)
        self.root.after(200, self.tick)

    def connect(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            send_msg(self.sock, {"type": "HELLO", "name": self.name})
            res = recv_msg(self.sock)
            if res.get("type") != "HELLO_OK":
                raise ConnectionError(res.get("msg", "HELLO failed"))
            self.status_var.set("Connected. Waiting for host to start...")
        except Exception as e:
            messagebox.showerror("Connect failed", str(e))
            self.running = False
            try:
                self.root.destroy()
            except:
                pass

    def recv_loop(self):
        try:
            while self.running:
                msg = recv_msg(self.sock)
                self.inbox.put(msg)
        except Exception:
            self.inbox.put({"type": "DISCONNECT"})

    def set_scoreboard(self, scoreboard):
        self.score_text.config(state=tk.NORMAL)
        self.score_text.delete("1.0", tk.END)
        for name, sc in scoreboard:
            self.score_text.insert(tk.END, f"{name}: {sc}\n")
        self.score_text.config(state=tk.DISABLED)

    def enable_choices(self, enabled: bool):
        st = tk.NORMAL if enabled else tk.DISABLED
        for b in self.btns:
            b.config(state=st)

    def choose(self, idx: int):
        if not self.round_active or self.my_answer_sent:
            return
        self.my_answer_sent = True
        self.enable_choices(False)
        self.status_var.set("Answer sent. Waiting result...")
        try:
            send_msg(self.sock, {"type": "ANSWER", "choice": idx})
        except Exception:
            self.status_var.set("Send failed (disconnected).")

    def process_inbox(self):
        try:
            while True:
                msg = self.inbox.get_nowait()
                t = msg.get("type")

                if t == "ROUND":
                    self.round_active = True
                    self.my_answer_sent = False
                    self.current_choices = msg.get("choices", [])
                    timeout = int(msg.get("timeout", 10))
                    self.timer_deadline = time.time() + timeout

                    self.q_var.set(msg.get("q", ""))
                    for i in range(3):
                        text = self.current_choices[i] if i < len(self.current_choices) else ""
                        self.btns[i].config(text=f"{['A','B','C'][i]}) {text}")
                    self.status_var.set("Choose your answer!")
                    self.enable_choices(True)

                elif t == "RESULT":
                    self.round_active = False
                    self.enable_choices(False)
                    self.timer_deadline = None
                    correct = msg.get("correct", 0)
                    answers = msg.get("answers", {})
                    scoreboard = msg.get("scores", [])

                    my_choice = answers.get(self.name, None)
                    if my_choice is None:
                        self.status_var.set("你沒作答（timeout）")
                    elif my_choice == correct:
                        self.status_var.set("答對！")
                    else:
                        self.status_var.set("答錯。")

                    self.set_scoreboard(scoreboard)

                elif t == "GAME_OVER":
                    self.round_active = False
                    self.enable_choices(False)
                    self.timer_deadline = None
                    self.status_var.set("Game Over")
                    self.q_var.set("遊戲結束！")
                    self.set_scoreboard(msg.get("scores", []))
                    messagebox.showinfo("Game Over", "遊戲結束！請看分數排行。")

                elif t == "HOST_STOP":
                    messagebox.showwarning("Host stopped", "Host 已停止，遊戲結束。")
                    self.stop()
                    return

                elif t == "DISCONNECT":
                    messagebox.showwarning("Disconnected", "與 Host 連線中斷。")
                    self.stop()
                    return

        except queue.Empty:
            pass

        self.root.after(100, self.process_inbox)

    def tick(self):
        if self.timer_deadline is not None and self.round_active:
            left = int(self.timer_deadline - time.time())
            if left < 0:
                left = 0
            self.timer_var.set(f"Time left: {left}s")
            if left == 0 and not self.my_answer_sent:
                self.enable_choices(False)
                self.status_var.set("Time up. Waiting result...")
        else:
            self.timer_var.set("")
        self.root.after(200, self.tick)

    def stop(self):
        if not self.running:
            return
        self.running = False
        try:
            if self.sock:
                self.sock.close()
        except:
            pass
        try:
            self.root.destroy()
        except:
            pass

    def run(self):
        self.root.mainloop()


# ---------------- Entry ----------------

def spawn_self_join(name: str, port: int):
    """
    ✅ 讓 host 自己也當玩家：開一個新 process join 127.0.0.1
    """
    py = sys.executable
    script = os.path.abspath(__file__)
    cmd = [py, script, "join", "--host", "127.0.0.1", "--port", str(port), "--name", name]
    subprocess.Popen(cmd, cwd=os.path.dirname(script))

def main():
    p = argparse.ArgumentParser(description="Multiplayer GUI Quiz (3+ players)")
    sub = p.add_subparsers(dest="mode", required=True)

    ph = sub.add_parser("host", help="Host a game")
    ph.add_argument("--port", type=int, default=5001)
    ph.add_argument("--players", type=int, default=3, help="minimum players to start (>=3)")
    ph.add_argument("--timeout", type=int, default=12, help="seconds per round")
    ph.add_argument("--name", type=str, required=True, help="host also joins as a player (name)")

    pj = sub.add_parser("join", help="Join a hosted game")
    pj.add_argument("--host", type=str, required=True)
    pj.add_argument("--port", type=int, default=5001)
    pj.add_argument("--name", type=str, required=True)

    args = p.parse_args()

    if args.mode == "host":
        app = HostApp(port=args.port, expected_players=args.players, round_timeout=args.timeout)

        # ✅ 等到 host listen 成功再自動 join，避免 join 先連線導致 10061
        def auto_join_when_ready():
            app.listening_event.wait(timeout=5.0)
            if app.listening_event.is_set():
                spawn_self_join(args.name, args.port)

        threading.Thread(target=auto_join_when_ready, daemon=True).start()
        app.run()
    else:
        PlayerApp(host=args.host, port=args.port, name=args.name).run()

if __name__ == "__main__":
    main()
