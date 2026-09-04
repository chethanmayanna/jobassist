from contextlib import asynccontextmanager
from typing import Literal
import sqlite3
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from pydantic import BaseModel, EmailStr, Field
from .db import get_connection, init_db
from .security import ALGORITHM, SECRET_KEY, create_token, hash_password, verify_password

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield

app = FastAPI(title="Juncture API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    full_name: str = Field(min_length=2, max_length=100)
    role: Literal["seeker", "recruiter"] = "seeker"

class JobRequest(BaseModel):
    title: str = Field(min_length=2, max_length=150)
    company: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=20)
    location: str = Field(min_length=2, max_length=150)
    employment_type: str = "full-time"
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)

class User(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    role: str


def current_user(token: str = Depends(oauth2_scheme)) -> sqlite3.Row:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
    except (JWTError, KeyError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token") from error
    with get_connection() as connection:
        user = connection.execute("SELECT id, email, full_name, role FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


def require_role(role: str):
    def dependency(user: sqlite3.Row = Depends(current_user)) -> sqlite3.Row:
        if user["role"] != role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"{role} role required")
        return user
    return dependency

@app.get("/api/health")
def health():
    return {"status": "ok"}

@app.post("/api/auth/register", response_model=User, status_code=201)
def register(request: RegisterRequest):
    with get_connection() as connection:
        try:
            cursor = connection.execute("INSERT INTO users (email, password_hash, full_name, role) VALUES (?, ?, ?, ?)", (request.email, hash_password(request.password), request.full_name, request.role))
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=409, detail="Email already registered") from error
        return {"id": cursor.lastrowid, "email": request.email, "full_name": request.full_name, "role": request.role}

@app.post("/api/auth/login")
def login(form: OAuth2PasswordRequestForm = Depends()):
    with get_connection() as connection:
        user = connection.execute("SELECT * FROM users WHERE email = ?", (form.username,)).fetchone()
    if not user or not verify_password(form.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return {"access_token": create_token(user["id"], user["role"]), "token_type": "bearer", "user": {"id": user["id"], "email": user["email"], "full_name": user["full_name"], "role": user["role"]}}

@app.get("/api/auth/me", response_model=User)
def me(user: sqlite3.Row = Depends(current_user)):
    return dict(user)

@app.get("/api/jobs")
def list_jobs(search: str | None = None, location: str | None = None, user: sqlite3.Row = Depends(current_user)):
    query = "SELECT j.*, COUNT(a.id) AS application_count FROM jobs j LEFT JOIN applications a ON a.job_id = j.id WHERE j.status = 'active'"
    parameters: list[str] = []
    if search:
        query += " AND (j.title LIKE ? OR j.company LIKE ? OR j.description LIKE ?)"
        parameters.extend([f"%{search}%"] * 3)
    if location:
        query += " AND j.location LIKE ?"
        parameters.append(f"%{location}%")
    query += " GROUP BY j.id ORDER BY j.created_at DESC"
    with get_connection() as connection:
        return [dict(row) for row in connection.execute(query, parameters).fetchall()]

@app.post("/api/jobs", status_code=201)
def create_job(request: JobRequest, recruiter: sqlite3.Row = Depends(require_role("recruiter"))):
    with get_connection() as connection:
        cursor = connection.execute("INSERT INTO jobs (recruiter_id, title, company, description, location, employment_type, salary_min, salary_max) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (recruiter["id"], request.title, request.company, request.description, request.location, request.employment_type, request.salary_min, request.salary_max))
        job = connection.execute("SELECT * FROM jobs WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return dict(job)

@app.get("/api/recruiter/jobs")
def recruiter_jobs(recruiter: sqlite3.Row = Depends(require_role("recruiter"))):
    with get_connection() as connection:
        rows = connection.execute("SELECT j.*, COUNT(a.id) AS application_count FROM jobs j LEFT JOIN applications a ON a.job_id = j.id WHERE j.recruiter_id = ? GROUP BY j.id ORDER BY j.created_at DESC", (recruiter["id"],)).fetchall()
    return [dict(row) for row in rows]

@app.post("/api/jobs/{job_id}/applications", status_code=201)
def apply_to_job(job_id: int, seeker: sqlite3.Row = Depends(require_role("seeker"))):
    with get_connection() as connection:
        if not connection.execute("SELECT id FROM jobs WHERE id = ? AND status = 'active'", (job_id,)).fetchone():
            raise HTTPException(status_code=404, detail="Job not found")
        try:
            cursor = connection.execute("INSERT INTO applications (job_id, seeker_id) VALUES (?, ?)", (job_id, seeker["id"]))
        except sqlite3.IntegrityError as error:
            raise HTTPException(status_code=409, detail="You already applied to this job") from error
    return {"id": cursor.lastrowid, "job_id": job_id, "status": "submitted"}

@app.get("/api/applications")
def my_applications(seeker: sqlite3.Row = Depends(require_role("seeker"))):
    with get_connection() as connection:
        rows = connection.execute("SELECT a.*, j.title, j.company, j.location FROM applications a JOIN jobs j ON j.id = a.job_id WHERE a.seeker_id = ? ORDER BY a.created_at DESC", (seeker["id"],)).fetchall()
    return [dict(row) for row in rows]
