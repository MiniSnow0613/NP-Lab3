import os
import json

TEMPLATE_MAIN = """\
# 這是遊戲入口（範例）
# 你可以把 main.py 換成你自己的遊戲程式
print("Hello from game template!")
input("Press Enter to exit...")
"""

def main():
    game_name = input("Game name: ").strip()
    if not game_name:
        print("Empty name.")
        return

    base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "games", game_name)
    os.makedirs(base, exist_ok=True)

    main_py = os.path.join(base, "main.py")
    cfg = os.path.join(base, "game_config.json")

    if not os.path.exists(main_py):
        with open(main_py, "w", encoding="utf-8") as f:
            f.write(TEMPLATE_MAIN)

    if not os.path.exists(cfg):
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump({"exe_cmd": "python main.py"}, f, ensure_ascii=False, indent=2)

    print(f"[OK] Created: {base}")

if __name__ == "__main__":
    main()
