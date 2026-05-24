from flask import Flask, request, jsonify, send_file, Response
from flask_cors import CORS
import yt_dlp
import os
import uuid
import threading
import time

app = Flask(__name__)
CORS(app, origins=["https://baixaclip.online", "https://www.baixaclip.online"])
DOWNLOAD_DIR = "/tmp/snapload"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

# Apaga arquivos antigos a cada 10 minutos (limpeza automática)
def cleanup_old_files():
    while True:
        time.sleep(600)
        now = time.time()
        for f in os.listdir(DOWNLOAD_DIR):
            fpath = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fpath) and now - os.path.getmtime(fpath) > 600:
                os.remove(fpath)

threading.Thread(target=cleanup_old_files, daemon=True).start()


@app.route("/api/info", methods=["POST"])
def get_info():
    """Recebe a URL e retorna informações do vídeo (título, thumbnail, formatos)"""
    data = request.get_json()
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "URL não informada"}), 400

    # Valida se é TikTok ou Instagram
    if "tiktok.com" not in url and "instagram.com" not in url:
        return jsonify({"error": "Apenas links do TikTok e Instagram são suportados"}), 400

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": False,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)

        # Monta lista de formatos disponíveis
        formats = []
        seen = set()
        for f in info.get("formats", []):
            ext = f.get("ext", "")
            height = f.get("height")
            if ext == "mp4" and height and height not in seen:
                seen.add(height)
                formats.append({
                    "format_id": f["format_id"],
                    "label": f"MP4 {height}p",
                    "ext": "mp4",
                    "quality": height,
                })

        # Ordena do maior pro menor
        formats.sort(key=lambda x: x["quality"], reverse=True)

        # Adiciona opção de MP3
        formats.append({"format_id": "audio", "label": "MP3 (só áudio)", "ext": "mp3", "quality": 0})

        return jsonify({
            "title": info.get("title", "Vídeo"),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration", 0),
            "uploader": info.get("uploader", ""),
            "formats": formats,
        })

    except Exception as e:
        return jsonify({"error": f"Não foi possível processar o link: {str(e)}"}), 500


@app.route("/api/download", methods=["POST"])
def download_video():
    """Baixa o vídeo e envia pro usuário"""
    data = request.get_json()
    url = data.get("url", "").strip()
    format_id = data.get("format_id", "")
    ext = data.get("ext", "mp4")

    if not url or not format_id:
        return jsonify({"error": "Parâmetros inválidos"}), 400

    file_id = str(uuid.uuid4())
    output_path = os.path.join(DOWNLOAD_DIR, f"{file_id}.%(ext)s")

    # Configuração do yt-dlp por formato
    if format_id == "audio":
        ydl_opts = {
            "quiet": True,
            "format": "bestaudio/best",
            "outtmpl": output_path,
            "postprocessors": [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }],
        }
        final_ext = "mp3"
    else:
        ydl_opts = {
            "quiet": True,
            "format": f"{format_id}+bestaudio/best[ext=mp4]/{format_id}",
            "outtmpl": output_path,
            "merge_output_format": "mp4",
        }
        final_ext = "mp4"

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            title = info.get("title", "video")

        # Encontra o arquivo gerado
        final_file = os.path.join(DOWNLOAD_DIR, f"{file_id}.{final_ext}")

        if not os.path.exists(final_file):
            # Tenta encontrar qualquer arquivo com esse ID
            for f in os.listdir(DOWNLOAD_DIR):
                if f.startswith(file_id):
                    final_file = os.path.join(DOWNLOAD_DIR, f)
                    break

        # Nome amigável para o download
        safe_title = "".join(c for c in title if c.isalnum() or c in " _-")[:50]
        download_name = f"{safe_title}.{final_ext}"

        return send_file(
            final_file,
            as_attachment=True,
            download_name=download_name,
            mimetype="video/mp4" if final_ext == "mp4" else "audio/mpeg",
        )

    except Exception as e:
        return jsonify({"error": f"Erro ao baixar: {str(e)}"}), 500


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
