# Instrukcja implementacji API STT na innej stronie

## Cel

Zaimplementuj na stronie formularz do transkrypcji filmu YouTube przez istniejące API aplikacji STT.

Użytkownik powinien mieć możliwość wyboru:

- URL YouTube
- modelu transkrypcji
- języka
- zadania do wykonania: transkrypcja albo tłumaczenie

## Endpointy

### 1. Pobranie dostępnych modeli

```http
GET /api/models?type=transcription
```

Ten endpoint jest publiczny i zwraca modele lokalne oraz chmurowe.

W odpowiedzi używaj tablicy:

```js
data.models
```

Każdy model ma pole:

```js
model.request_settings
```

To pole zawiera gotowe ustawienia wymagane przez endpoint transkrypcji.

Przykład dla modelu lokalnego:

```json
{
  "processing_mode": "offline",
  "model_name": "base"
}
```

Przykład dla modelu chmurowego:

```json
{
  "processing_mode": "online",
  "cloud_model_id": 1
}
```

### 2. Uruchomienie transkrypcji YouTube

```http
POST /api/youtube/transcribe
```

Ten endpoint wymaga aktywnej sesji użytkownika.

Przy `fetch` użyj:

```js
credentials: "include"
```

## Request

Po wyborze modelu z listy połącz `request_settings` modelu z ustawieniami użytkownika.

### Przykład dla modelu lokalnego

```json
{
  "yt_url": "https://www.youtube.com/watch?v=...",
  "settings": {
    "processing_mode": "offline",
    "model_name": "base",
    "language": "pl",
    "task": "transcribe",
    "save_to_history": false
  }
}
```

### Przykład dla modelu chmurowego

```json
{
  "yt_url": "https://www.youtube.com/watch?v=...",
  "settings": {
    "processing_mode": "online",
    "cloud_model_id": 1,
    "language": "auto",
    "task": "transcribe",
    "save_to_history": false
  }
}
```

## Pola formularza

### Model

- Pobierz modele z `/api/models?type=transcription`
- Wyświetl `display_name`
- Opcjonalnie dopisz źródło: `source`, np. `local` albo `cloud`
- Jako wartość w `<select>` możesz trzymać indeks albo zserializowane `request_settings`

### Język

```html
<select name="language">
  <option value="auto">Auto</option>
  <option value="pl">Polski</option>
  <option value="en">Angielski</option>
  <option value="de">Niemiecki</option>
  <option value="es">Hiszpański</option>
</select>
```

### Zadanie

```html
<select name="task">
  <option value="transcribe">Transkrypcja</option>
  <option value="translate">Tłumaczenie na angielski</option>
</select>
```

## Minimalny JavaScript

```js
let transcriptionModels = [];

async function loadModels() {
  const res = await fetch("/api/models?type=transcription");
  const data = await res.json();

  transcriptionModels = data.models;

  const select = document.querySelector("#model");
  select.innerHTML = "";

  transcriptionModels.forEach((model, index) => {
    const option = document.createElement("option");
    option.value = index;
    option.textContent = `${model.display_name} (${model.source})`;
    select.appendChild(option);
  });
}

async function submitTranscription(event) {
  event.preventDefault();

  const ytUrl = document.querySelector("#yt_url").value.trim();
  const modelIndex = document.querySelector("#model").value;
  const language = document.querySelector("#language").value;
  const task = document.querySelector("#task").value;

  const selectedModel = transcriptionModels[modelIndex];

  const payload = {
    yt_url: ytUrl,
    settings: {
      ...selectedModel.request_settings,
      language,
      task,
      save_to_history: false
    }
  };

  const res = await fetch("/api/youtube/transcribe", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    credentials: "include",
    body: JSON.stringify(payload)
  });

  const data = await res.json();

  if (!res.ok) {
    throw new Error(data.error || "Błąd transkrypcji");
  }

  document.querySelector("#transcription").textContent = data.text;
  document.querySelector("#summary").textContent = data.summary;
}

document.addEventListener("DOMContentLoaded", loadModels);
document.querySelector("#transcription-form").addEventListener("submit", submitTranscription);
```

## Response transkrypcji

Endpoint zwraca m.in.:

```json
{
  "text": "pełna transkrypcja",
  "summary": "podsumowanie / notatki AI",
  "language": "pl",
  "model_used": "Lokalny Whisper (base)",
  "task": "transcribe",
  "youtube": {
    "id": "...",
    "title": "...",
    "duration": 123,
    "url": "..."
  }
}
```

Na stronie pokaż przynajmniej:

- `youtube.title`
- `model_used`
- `language`
- `text`
- `summary`

## Ważne

Jeśli strona jest w tej samej aplikacji Flask, używaj ścieżek względnych:

```text
/api/models
/api/youtube/transcribe
```

Jeśli to osobna domena, trzeba dodać CORS albo zrobić backend proxy, ponieważ endpoint transkrypcji wymaga sesji użytkownika.
