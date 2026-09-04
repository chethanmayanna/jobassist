# Juncture

A modular job marketplace prototype for job seekers and recruiters.

## Structure

- `frontend`: React + Vite dashboard UI
- `backend`: FastAPI API with SQLite persistence
- `backend/app/db.py`: database connection and schema initialization
- `backend/app/security.py`: password hashing and JWT token creation
- `backend/app/main.py`: API routes and role authorization

## Run locally

### Frontend

```powershell
Set-Location frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

### Backend

```powershell
Set-Location backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

API docs are available at `http://localhost:8000/docs`.

Set `JUNCTURE_SECRET_KEY` to a strong environment value before using the API outside local development.
