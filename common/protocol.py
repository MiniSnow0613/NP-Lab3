import socket
import struct
import json

# 定義一個通用的 Header 格式：4 bytes 的 unsigned int (代表後續資料的長度)
# '!' 代表 network (big-endian), 'I' 代表 unsigned int (4 bytes)
HEADER_STRUCT = struct.Struct('!I')

def send_json(sock: socket.socket, data: dict):
    """
    將 Python 字典轉為 JSON 並發送。
    格式: [4 bytes Length] + [JSON Bytes]
    """
    try:
        # 1. 將字典轉為 JSON 字串，再轉為 bytes (UTF-8)
        json_bytes = json.dumps(data).encode('utf-8')
        
        # 2. 計算長度
        length = len(json_bytes)
        
        # 3. 打包 Header (長度資訊)
        header = HEADER_STRUCT.pack(length)
        
        # 4. 發送 (Header + Body)
        # sendall 確保資料全部送出，不會只送一半
        sock.sendall(header + json_bytes)
        return True
        
    except (socket.error, TypeError, ValueError) as e:
        print(f"[Protocol] Send Error: {e}")
        return False

def recv_json(sock: socket.socket) -> dict:
    """
    從 socket 接收完整的 JSON 封包。
    這是一個 blocking call，會等到收到完整封包或連線中斷。
    """
    try:
        # 1. 先接收 4 bytes 的 Header (長度)
        header_bytes = _recv_all(sock, HEADER_STRUCT.size)
        if not header_bytes:
            return None # 連線關閉或接收失敗
            
        # 2. 解開 Header，得知接下來要收多少資料
        length = HEADER_STRUCT.unpack(header_bytes)[0]
        
        # 3. 根據長度接收 JSON Body
        body_bytes = _recv_all(sock, length)
        if not body_bytes:
            return None
            
        # 4. 解碼 JSON
        return json.loads(body_bytes.decode('utf-8'))
        
    except (socket.error, json.JSONDecodeError, struct.error) as e:
        print(f"[Protocol] Recv Error: {e}")
        return None

def _recv_all(sock: socket.socket, n: int) -> bytes:
    """
    輔助函式：確保剛好接收 n bytes 資料。
    解決 TCP 封包破碎 (fragmentation) 的問題。
    """
    data = b''
    while len(data) < n:
        try:
            # 每次嘗試接收剩餘所需的量
            packet = sock.recv(n - len(data))
            if not packet:
                # 如果 recv 回傳空 bytes，代表對方關閉連線 (EOF)
                return None
            data += packet
        except socket.error:
            return None
            
    return data

# --- 加分題：檔案傳輸輔助函式 (預留給 Level 2) ---

def send_file(sock: socket.socket, file_path: str):
    """
    傳送二進位檔案 (不經過 JSON 封裝，直接送 Raw Bytes)。
    通常在 send_json 發送完 metadata (檔名、大小) 後呼叫。
    """
    try:
        with open(file_path, 'rb') as f:
            while True:
                # 分塊讀取，避免一次讀入大檔案吃光記憶體
                chunk = f.read(4096)
                if not chunk:
                    break
                sock.sendall(chunk)
        return True
    except IOError as e:
        print(f"[Protocol] File Send Error: {e}")
        return False

def recv_file(sock: socket.socket, save_path: str, file_size: int):
    """
    接收指定大小的二進位檔案。
    """
    try:
        received = 0
        with open(save_path, 'wb') as f:
            while received < file_size:
                # 計算這次最多能收多少 (不能超過剩餘大小，也不超過 buffer)
                chunk_size = min(4096, file_size - received)
                chunk = sock.recv(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
        return received == file_size
    except IOError as e:
        print(f"[Protocol] File Recv Error: {e}")
        return False