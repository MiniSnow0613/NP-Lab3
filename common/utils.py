import os
import zipfile

def zip_dir(folder_path, output_path):
    """
    將指定資料夾 (folder_path) 壓縮成 zip 檔案 (output_path)
    """
    try:
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            # 走訪資料夾內所有檔案
            for root, dirs, files in os.walk(folder_path):
                for file in files:
                    # 取得檔案完整路徑
                    file_path = os.path.join(root, file)
                    # 決定在 Zip 內的相對路徑 (去除前面的絕對路徑部分)
                    arcname = os.path.relpath(file_path, folder_path)
                    zipf.write(file_path, arcname)
        return True
    except Exception as e:
        print(f"[Utils] Zipping failed: {e}")
        return False

def unzip_file(zip_path, extract_to):
    """
    將 zip 檔案解壓縮到指定目錄
    """
    try:
        with zipfile.ZipFile(zip_path, 'r') as zipf:
            zipf.extractall(extract_to)
        return True
    except Exception as e:
        print(f"[Utils] Unzipping failed: {e}")
        return False