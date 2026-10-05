import socket
import threading
import json
import time

try:
    if 'resolve' not in globals():
        import DaVinciResolveScript as dvr_script
        resolve = dvr_script.scriptapp("Resolve")
except Exception:
    pass # Nếu chạy ngoài resolve sẽ báo lỗi, nhưng script này sinh ra để chạy TRONG Resolve

PORT = 17005

def handle_request(command, payload):
    try:
        if command == "ping":
            return {"status": "ok", "message": "pong"}
            
        pm = resolve.GetProjectManager()
        proj = pm.GetCurrentProject()
        
        if not proj:
            return {"status": "error", "message": "No active project"}
            
        if command == "get_active_timeline_name":
            tl = proj.GetCurrentTimeline()
            return {"status": "ok", "name": tl.GetName() if tl else None}
            
        if command == "get_media_pool_file_paths":
            mp = proj.GetMediaPool()
            # ... rút gọn thuật toán duyệt cây thư mục cho Bridge demo ...
            return {"status": "ok", "paths": []} 
            
        if command == "import_media":
            mp = proj.GetMediaPool()
            paths = payload.get("paths", [])
            items = mp.ImportMedia(paths)
            return {"status": "ok", "count": len(items)}
            
        if command == "import_edl":
            mp = proj.GetMediaPool()
            path = payload.get("edl_path")
            # CreateEmptyTimeline is safe, ImportTimelineFromFile might fail in some versions
            tl = mp.ImportTimelineFromFile(path)
            if tl:
                return {"status": "ok", "timeline": tl.GetName()}
            return {"status": "error", "message": "API returned False"}
            
        if command == "get_playhead_seconds":
            tl = proj.GetCurrentTimeline()
            if not tl: return {"status": "error"}
            fps = float(tl.GetSetting("timelineFrameRate") or 30.0)
            frames = float(tl.GetCurrentTimecode()) # Dummy fallback, GetPlayheadPositionInFrames requires Resolve 18.1+
            # Do không có cách chuẩn 100% lấy playhead trong free API, trả về 0.0 tạm
            return {"status": "ok", "seconds": 0.0}

        return {"status": "error", "message": "Unknown command"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

def server_loop():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('127.0.0.1', PORT))
    server.listen(1)
    print(f"[ChunDVC Bridge] Đang lắng nghe trên cổng {PORT}...")
    
    while True:
        try:
            client, addr = server.accept()
            data = client.recv(4096)
            if data:
                req = json.loads(data.decode('utf-8'))
                resp = handle_request(req.get('command'), req.get('payload', {}))
                client.sendall(json.dumps(resp).encode('utf-8'))
            client.close()
        except Exception as e:
            print("[ChunDVC Bridge] Error:", e)

# Chạy ngầm server socket trên một luồng tách biệt để không làm đơ Resolve UI
t = threading.Thread(target=server_loop, daemon=True)
t.start()
print("[ChunDVC Bridge] Khởi chạy ngầm thành công. Bạn có thể đóng thông báo này và tiếp tục edit.")
