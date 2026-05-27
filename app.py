import os
import sqlite3
import re
import io
import platform
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_file
from werkzeug.security import generate_password_hash, check_password_hash
import whisper
import ollama
from groq import Groq
import yt_dlp

from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

app = Flask(__name__)
app.secret_key = 'super-tajny-klucz-do-sesji-praktyki'

UPLOAD_FOLDER = 'temp_uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DB_FILE = 'users.db'
GROQ_API_KEY = "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"

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            filename TEXT NOT NULL,
            raw_text TEXT NOT NULL,
            ai_notes TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_email) REFERENCES users(email)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

print("Ładowanie modeli Whisper...")
models = {
    "tiny": whisper.load_model("tiny"),
    "base": whisper.load_model("base"),
    "small": whisper.load_model("small")
}

@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user_email' in session:
        return redirect(url_for('test_page'))
        
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT email, first_name, last_name, password_hash FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        conn.close()
        
        if user and check_password_hash(user[3], password):
            session['user_email'] = user[0]
            flash('Zalogowano pomyślnie!', 'success')
            return redirect(url_for('test_page'))
        else:
            flash('Błędny email lub hasło.', 'danger')
        
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if 'user_email' in session:
        return redirect(url_for('test_page'))

    if request.method == 'POST':
        email = request.form.get('email')
        first_name = request.form.get('first_name')
        last_name = request.form.get('last_name')
        password = request.form.get('password')
        
        email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_regex, email):
            flash('Podaj poprawny adres e-mail (np. nazwa@domena.pl).', 'danger')
            return render_template('rejestracja.html')
            
        if len(password) < 8:
            flash('Hasło musi mieć co najmniej 8 znaków.', 'danger')
            return render_template('rejestracja.html')
        if not any(char.isupper() for char in password):
            flash('Hasło musi zawierać co najmniej jedną wielką literę.', 'danger')
            return render_template('rejestracja.html')
        if not any(char.isdigit() for char in password):
            flash('Hasło musi zawierać co najmniej jedną cyfrę.', 'danger')
            return render_template('rejestracja.html')
        if not any(char in '!@#$%^&*(),.?":{}|<>' for char in password):
            flash('Hasło musi zawierać co najmniej jeden znak specjalny.', 'danger')
            return render_template('rejestracja.html')
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT email FROM users WHERE email = ?", (email,))
        existing_user = cursor.fetchone()
        
        if existing_user:
            flash('Ten adres email jest już zarejestrowany!', 'danger')
            conn.close()
        else:
            hashed_password = generate_password_hash(password)
            cursor.execute(
                "INSERT INTO users (email, first_name, last_name, password_hash) VALUES (?, ?, ?, ?)",
                (email, first_name, last_name, hashed_password)
            )
            conn.commit()
            conn.close()
            flash('Rejestracja zakończona sukcesem! Możesz się zalogować.', 'success')
            return redirect(url_for('login'))
        
    return render_template('rejestracja.html')

@app.route('/test-page')
def test_page():
    if 'user_email' not in session:
        flash('Brak dostępu. Musisz się najpierw zalogować!', 'danger')
        return redirect(url_for('login'))
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT email, first_name, last_name FROM users WHERE email = ?", (session['user_email'],))
    user = cursor.fetchone()
    conn.close()
    
    user_data = {
        'email': user[0],
        'first_name': user[1],
        'last_name': user[2]
    }
    return render_template('test-page.html', user=user_data)

@app.route('/transcribe', methods=['POST'])
def transcribe():
    if 'user_email' not in session:
        return jsonify({"error": "Brak autoryzacji"}), 401
        
    youtube_url = request.form.get('youtube_url', '').strip()
    processing_mode = request.form.get('processing_mode', 'offline')
    model_name = request.form.get('model_name', 'base')
    language = request.form.get('language', 'auto')
    task = request.form.get('task', 'transcribe')
    custom_name = request.form.get('custom_name', '').strip()
    
    file_path = None
    display_title = ""

    try:
        if youtube_url:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(UPLOAD_FOLDER, '%(id)s.%(ext)s'),
                'postprocessors': [{
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '192',
                }],
                'quiet': True
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(youtube_url, download=True)
                video_id = info.get('id')
                video_title = info.get('title', 'YouTube Video')
                file_path = os.path.join(UPLOAD_FOLDER, f"{video_id}.mp3")
                display_title = custom_name if custom_name else f"YT: {video_title}"
        else:
            if 'file' not in request.files:
                return jsonify({"error": "Brak pliku lub linku YouTube"}), 400
            file = request.files['file']
            if file.filename == '':
                return jsonify({"error": "Nie wybrano pliku"}), 400
                
            file_path = os.path.join(UPLOAD_FOLDER, file.filename)
            file.save(file_path)
            display_title = custom_name if custom_name else file.filename

        # Jeśli przesłano gotowy plik tekstowy .txt
        if file_path.endswith('.txt') and not youtube_url:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                surowy_tekst = f.read()
            if os.path.exists(file_path):
                os.remove(file_path)
                
            detected_lang = "Plik tekstowy"
            model_used_info = "Czysty tekst (Brak STT)"
            
            # Generowanie notatki AI dla tekstu przez Groq/Ollama
            prompt = f"Jesteś profesjonalnym asystentem biurowym. Przeczytaj uważnie poniższy tekst i przygotuj z niego czytelną, ustrukturyzowaną notatkę w języku polskim.\nNotatka MUSI składać się z trzech wyraźnych sekcji:\n1. KRÓTKIE PODSUMOWANIE (2-4 zdania wyjaśniające esencję).\n2. NAJWAŻNIEJSZE PUNKTY (kluczowe informacje od myślników).\n3. LISTA ZADAŃ DO WYKONANIA (akcje do podjęcia).\n\nOto tekst:\n{surowy_tekst}"
            if processing_mode == 'online':
                client = Groq(api_key=GROQ_API_KEY)
                completion = client.chat.completions.create(
                    model="llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": prompt}]
                )
                notatki_ai = completion.choices[0].message.content
            else:
                try:
                    response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
                    notatki_ai = response['message']['content']
                except:
                    notatki_ai = "Nie udało się wygenerować notatek AI lokalnie. Upewnij się, że Ollama działa w tle."
        else:
            # Klasyczne przetwarzanie audio STT
            if processing_mode == 'online':
                client = Groq(api_key=GROQ_API_KEY)
                with open(file_path, "rb") as audio_file:
                    transcription_options = {
                        "file": (audio_file.name, audio_file.read()),
                        "model": "whisper-large-v3"
                    }
                    if language != "auto":
                        transcription_options["language"] = language
                    
                    transcription = client.audio.transcriptions.create(**transcription_options)
                    surowy_tekst = transcription.text
                    
                if os.path.exists(file_path):
                    os.remove(file_path)
                
                notatki_ai = ""
                if surowy_tekst.strip():
                    prompt = f"Jesteś profesjonalnym asystentem biurowym. Przeczytaj uważnie poniższy tekst pochodzący z nagrania audio i przygotuj z niego czytelną, ustrukturyzowaną notatkę w języku polskim.\nNotatka MUSI składać się z trzech wyraźnych sekcji:\n1. KRÓTKIE PODSUMOWANIE (2-4 zdania wyjaśniające esencję nagrania).\n2. NAJWAŻNIEJSZE PUNKTY (kluczowe informacje i wątki wypisane od myślników).\n3. LISTA ZADAŃ DO WYKONANIA (zadania i akcje do podjęcia, jeśli o nich wspomniano).\n\nOto tekst do przeanalizowania:\n{surowy_tekst}"
                    completion = client.chat.completions.create(
                        model="llama-3.3-70b-versatile",
                        messages=[{"role": "user", "content": prompt}]
                    )
                    notatki_ai = completion.choices[0].message.content
                    
                detected_lang = language if language != "auto" else "pl"
                model_used_info = "Groq Cloud (Whisper Large V3)"
                
            else:
                if model_name not in models:
                    return jsonify({"error": "Model not supported"}), 400
                    
                model = models[model_name]
                options = {"task": task, "fp16": False}
                if language != "auto":
                    options["language"] = language
                    
                result = model.transcribe(file_path, **options)
                surowy_tekst = result["text"]
                
                if os.path.exists(file_path):
                    os.remove(file_path)
                
                notatki_ai = ""
                if surowy_tekst.strip():
                    prompt = f"Jesteś profesjonalnym asystentem biurowym. Przeczytaj uważnie poniższy tekst pochodzący z nagrania audio i przygotuj z niego czytelną, ustrukturyzowaną notatkę w języku polskim.\nNotatka MUSI składać się z trzech wyraźnych sekcji:\n1. KRÓTKIE PODSUMOWANIE (2-4 zdania wyjaśniające esencję nagrania).\n2. NAJWAŻNIEJSZE PUNKTY (kluczowe informacje i wątki wypisane od myślników).\n3. LISTA ZADAŃ DO WYKONANIA (zadania i akcje do podjęcia, jeśli o nich wspomniano).\n\nOto tekst do przeanalizowania:\n{surowy_tekst}"
                    try:
                        response = ollama.chat(model='llama3', messages=[{'role': 'user', 'content': prompt}])
                        notatki_ai = response['message']['content']
                    except Exception as ollama_err:
                        notatki_ai = "Nie udało się wygenerować notatek AI. Upewnij się, że Ollama działa w tle."
                        
                detected_lang = result.get("language", language)
                model_used_info = f"Lokalny Whisper ({model_name})"

        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO history (user_email, filename, raw_text, ai_notes) VALUES (?, ?, ?, ?)",
            (session['user_email'], display_title, surowy_tekst.strip(), notatki_ai.strip())
        )
        new_id = cursor.lastrowid
        conn.commit()
        conn.close()

        return jsonify({
            "text": surowy_tekst, 
            "notes": notatki_ai,
            "model_used": model_used_info,
            "language": detected_lang,
            "task": task,
            "saved_name": display_title,
            "record_id": new_id
        })
        
    except Exception as e:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
        return jsonify({"error": str(e)}), 500

@app.route('/ask-question', methods=['POST'])
def ask_question():
    if 'user_email' not in session:
        return jsonify({"error": "Brak autoryzacji"}), 401
        
    data = request.get_json()
    record_id = data.get('id')
    question = data.get('question', '').strip()
    
    if not record_id or not question:
        return jsonify({"error": "Brak ID nagrania lub pytania"}), 400
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT raw_text FROM history WHERE id = ? AND user_email = ?", (record_id, session['user_email']))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "Nie znaleziono nagrania w historii"}), 404
        
    transkrypcja = row[0]
    
    prompt = f"""Jesteś inteligentnym asystentem. Odpowiedz krótko i konkretnie na pytanie użytkownika, opierając się wyłącznie na podanym poniżej tekście transkrypcji z nagrania audio.

Tekst transkrypcji:
{transkrypcja}

Pytanie użytkownika:
{question}"""

    try:
        client = Groq(api_key=GROQ_API_KEY)
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}]
        )
        odpowiedz_ai = completion.choices[0].message.content
        return jsonify({"answer": odpowiedz_ai})
    except Exception as e:
        return jsonify({"error": f"Błąd AI: {str(e)}"}), 500

@app.route('/get-history', methods=['GET'])
def get_history():
    if 'user_email' not in session:
        return jsonify({"error": "Brak autoryzacji"}), 401
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, filename, raw_text, ai_notes, datetime(created_at, 'localtime') FROM history WHERE user_email = ? ORDER BY created_at DESC", 
        (session['user_email'],)
    )
    rows = cursor.fetchall()
    conn.close()
    
    history_list = []
    for r in rows:
        history_list.append({
            "id": r[0],
            "filename": r[1],
            "raw_text": r[2],
            "ai_notes": r[3],
            "created_at": r[4]
        })
    return jsonify(history_list)

@app.route('/delete-history/<int:item_id>', methods=['DELETE'])
def delete_history(item_id):
    if 'user_email' not in session:
        return jsonify({"error": "Brak autoryzacji"}), 401
        
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM history WHERE id = ? AND user_email = ?", (item_id, session['user_email']))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route('/export/docx', methods=['POST'])
def export_docx():
    if 'user_email' not in session:
        return "Brak autoryzacji", 401
    
    content = request.form.get('content', '')
    title = request.form.get('title', 'Dokument')
    
    doc = Document()
    doc.add_heading(title, 0)
    
    for line in content.split('\n'):
        doc.add_paragraph(line)
        
    file_stream = io.BytesIO()
    doc.save(file_stream)
    file_stream.seek(0)
    
    return send_file(
        file_stream,
        mimetype='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        as_attachment=True,
        download_name=f'{title}.docx'
    )

@app.route('/export/pdf', methods=['POST'])
def export_pdf():
    if 'user_email' not in session:
        return "Brak autoryzacji", 401
        
    content = request.form.get('content', '')
    title = request.form.get('title', 'Dokument')
    
    file_stream = io.BytesIO()
    pdf = canvas.Canvas(file_stream, pagesize=letter)
    
    try:
        sys_os = platform.system()
        if sys_os == "Windows":
            font_path = "C:\\Windows\\Fonts\\arial.ttf"
            font_path_bold = "C:\\Windows\\Fonts\\arialbd.ttf"
        elif sys_os == "Darwin":
            font_path = "/Library/Fonts/Arial.ttf"
            font_path_bold = "/Library/Fonts/Arial Bold.ttf"
        else:
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            font_path_bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

        pdfmetrics.registerFont(TTFont('PolishArial', font_path))
        pdfmetrics.registerFont(TTFont('PolishArial-Bold', font_path_bold))
        font_regular = 'PolishArial'
        font_bold = 'PolishArial-Bold'
    except:
        font_regular = 'Helvetica'
        font_bold = 'Helvetica-Bold'

    pdf.setTitle(title)
    
    pdf.setFont(font_bold, 16)
    pdf.drawString(50, 750, title)
    pdf.setStrokeColorRGB(0.2, 0.2, 0.2)
    pdf.line(50, 740, 550, 740)
    
    pdf.setFont(font_regular, 10)
    y = 710
    for line in content.split('\n'):
        if y < 50:
            pdf.showPage()
            y = 750
            pdf.setFont(font_regular, 10)
        clean_line = line.encode('utf-8', errors='ignore').decode('utf-8')
        pdf.drawString(50, y, clean_line)
        y -= 15
        
    pdf.save()
    file_stream.seek(0)
    
    return send_file(
        file_stream,
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f'{title}.pdf'
    )

@app.route('/change-password', methods=['GET', 'POST'])
def change_password():
    if 'user_email' not in session:
        flash('Musisz się zalogować, aby zmienić hasło.', 'danger')
        return redirect(url_for('login'))
        
    if request.method == 'POST':
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_new_password = request.form.get('confirm_new_password')
        
        email = session['user_email']
        
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute("SELECT password_hash FROM users WHERE email = ?", (email,))
        user = cursor.fetchone()
        
        if not user or not check_password_hash(user[0], current_password):
            flash('Aktualne hasło jest niepoprawne.', 'danger')
            conn.close()
        elif new_password != confirm_new_password:
            flash('Nowe hasła nie są identyczne.', 'danger')
            conn.close()
        else:
            new_hashed = generate_password_hash(new_password)
            cursor.execute("UPDATE users SET password_hash = ? WHERE email = ?", (new_hashed, email))
            conn.commit()
            conn.close()
            flash('Hasło zostało pomyślnie zmienione!', 'success')
            return redirect(url_for('test_page'))
            
    return render_template('zmiana-hasla.html')

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    flash('Wylogowano pomyślnie.', 'success')
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=8000)