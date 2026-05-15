import os
import shutil
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import whisper

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

models = {
    "tiny": whisper.load_model("tiny"),
    "base": whisper.load_model("base"),
    "small": whisper.load_model("small")
}

@app.post("/transcribe")
async def transcribe_audio(
    file: UploadFile = File(...), 
    model_name: str = Form("base"),
    language: str = Form("auto"),
    task: str = Form("transcribe")
):
    try:
        if model_name not in models:
            return JSONResponse(status_code=400, content={"error": "Model not supported"})
            
        upload_dir = "temp_uploads"
        os.makedirs(upload_dir, exist_ok=True)
        
        file_path = os.path.join(upload_dir, file.filename)
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
            
        model = models[model_name]
        
        options = {"task": task, "fp16": False}
        if language != "auto":
            options["language"] = language
            
        result = model.transcribe(file_path, **options)
        
        os.remove(file_path)
        
        return {
            "text": result["text"], 
            "model_used": model_name,
            "language": result.get("language", language),
            "task": task
        }
        
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)