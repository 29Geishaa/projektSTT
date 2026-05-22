# **Projekt STT (Speech-to-Text) z Notatkami AI** 🚀

Aplikacja webowa typu **SaaS** zbudowana we **Flasku**, która pozwala na **rejestrację użytkowników**, **nagrywanie lub przesyłanie plików audio**, a następnie **automatyczne generowanie transkrypcji** oraz **inteligentnych notatek i list zadań**.

---

## **🛠️ Główne Funkcje (Tech Stack)**

* **Logowanie i Baza Danych:** System **rejestracji i autoryzacji** użytkowników oparty na **Flask** oraz bazie **SQLite**.
* **Transkrypcja (STT):** Zamiana **mowy na tekst** w czasie rzeczywistym przy użyciu modelu **OpenAI Whisper**.
* **Analiza AI (LLM):** Automatyczne **strukturyzowanie tekstu**, wyciąganie **wniosków** i zadań (**To-Do**) przy użyciu modelu **Llama 3** (uruchamianego lokalnie przez **Ollama**).
* **Dwu-kolumnowy Interfejs:** Czytelny frontend (**HTML/CSS/JS**) prezentujący **surowy tekst** po prawej stronie oraz **gotową notatkę AI** po lewej.

---

## **📦 Wymagania i Instalacja**

### **1. Klonowanie repozytorium i środowisko**
Klonujemy projekt, tworzymy wirtualne środowisko Pythona (**`venv`**) i je aktywujemy:


```
git clone [https://github.com/29Geishaa/projektSTT.git](https://github.com/29Geishaa/projektSTT.git)
cd projektSTT
python3 -m venv venv
source venv/bin/activate
```


2. Instalacja zależności
Instalujemy wszystkie wymagane biblioteki Pythona zapisane w pliku konfiguracyjnym:

```
pip install -r requirements.txt
```

3. Konfiguracja modeli AI (Ollama)
Upewnij się, że masz zainstalowaną aplikację Ollama oraz pobrany odpowiedni model językowy:

```
ollama run llama3
```

🚀 Uruchomienie Projektu
Odpal serwer deweloperski Flaska:

```
python app.py
Aplikacja domyślnie zacznie działać lokalnie na porcie 8000 (http://127.0.0.1:8000).
```


📝 Przykładowy Scenariusz Testowy
Aby przetestować pełne możliwości systemu, zaloguj się, kliknij przycisk nagrywania i przeczytaj na głos poniższy tekst:

"Dobra, słuchajcie, musimy szybko ogarnąć plan na ten tydzień, bo gonią nas terminy. Przede wszystkim, Kasia musi do czwartku skończyć ten raport finansowy dla zarządu, bo bez tego nie ruszymy z budżetem. Janek, Ty miałeś pogadać z klientem i ustalić, czy odpowiada im ten nowy projekt graficzny – daj mi znać, jak tylko dostaniesz maila, najlepiej do jutra do piętnastej. No i ja zajmę się rezerwacją sali na piątkowe spotkanie podsumowujące. Ogólnie najważniejsze jest to, żebyśmy do końca miesiąca zamknęli ten etap projektu, bo inaczej naliczą nam kary. Czy ktoś ma jeszcze jakieś pytania? Jak nie, to bierzemy się do roboty."

Efekt: System Whisper przepisze słowo w słowo Twoją mowę, a Llama 3 automatycznie stworzy z tego czystą agendę z podziałem na zadania dla Kasi, Janka oraz Ciebie wraz z terminami.
