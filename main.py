import os
import shutil
import whisper
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Pozwalamy przeglądarce łączyć się z serwerem (ważne dla frontendu!)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Załadowanie modelu do RAM-u (na procesorze - CPU)
print("Ładowanie modelu Whisper (to może potrwać chwilę)...")
model = whisper.load_model("base", device="cpu")
print("Model gotowy!")

@app.post("/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    # 1. Zapisujemy wysłany plik tymczasowo
    temp_filename = f"temp_{file.filename}"
    with open(temp_filename, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    try:
        # 2. Przetwarzamy dźwięk na tekst
        print(f"Przetwarzam plik: {temp_filename}")
        result = model.transcribe(temp_filename)
        
        # 3. Zwracamy tekst do użytkownika
        return {"text": result["text"]}
    
    except Exception as e:
        return {"error": str(e)}
    
    finally:
        # 4. Sprzątamy - usuwamy plik tymczasowy
        if os.path.exists(temp_filename):
            os.remove(temp_filename)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)