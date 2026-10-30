import os
from typing import Any, Dict, List, Optional
import aiosqlite
from app.config import settings

INIT_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT DEFAULT 'user',
    is_verified INTEGER DEFAULT 0,
    verify_token TEXT,
    daily_limit INTEGER,
    tokens_used INTEGER DEFAULT 0,
    sync_code TEXT UNIQUE,
    telegram_id INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS global_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    user_id INTEGER,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vacancies (
    vacancy_id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    title TEXT,
    company TEXT,
    url TEXT,
    score INTEGER,
    selected_resume TEXT,
    reasoning TEXT,
    cover_letter TEXT,
    status TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

def get_connection():
    os.makedirs(os.path.dirname(settings.DB_PATH), exist_ok=True)
    return aiosqlite.connect(settings.DB_PATH)

async def init_db():
    async with get_connection() as db:
        await db.executescript(INIT_SQL)
        migrations = [
            "ALTER TABLE users ADD COLUMN daily_limit INTEGER",
            "ALTER TABLE users ADD COLUMN tokens_used INTEGER DEFAULT 0",
            "ALTER TABLE users ADD COLUMN sync_code TEXT UNIQUE",
            "ALTER TABLE users ADD COLUMN telegram_id INTEGER"
        ]
        for mig in migrations:
            try:
                await db.execute(mig)
            except:
                pass
                
        default_global = {
            "openrouter_key": settings.OPENROUTER_API_KEY,
            "openrouter_model": "meta-llama/llama-3.3-70b-instruct",
            "min_score": "60",
            "daily_limit": "50",
            "global_career_legend": "Стандартный профиль инженера высокой надежности и инфраструктурного мониторинга (SRE / Systems Engineer).",
            "global_ai_instructions": "Пиши кратко (до 600 знаков), без шаблонных клише. Начинай сразу с релевантного опыта под стек вакансии."
        }
        for k, v in default_global.items():
            await db.execute("INSERT OR IGNORE INTO global_settings (key, value) VALUES (?, ?)", (k, v))

        async with db.execute("SELECT id FROM users WHERE email = ?", ("admin@worker.flow",)) as cursor:
            admin = await cursor.fetchone()
            if not admin:
                import hashlib
                pwd_hash = hashlib.sha256("Wf#9xK2!mP7vQ4zR".encode()).hexdigest()
                await db.execute(
                    "INSERT INTO users (email, password_hash, role, is_verified) VALUES (?, ?, 'admin', 1)",
                    ("admin@worker.flow", pwd_hash)
                )
                await db.commit()

async def get_global_settings() -> Dict[str, str]:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT key, value FROM global_settings") as cursor:
            rows = await cursor.fetchall()
            return {r["key"]: r["value"] for r in rows}

async def update_global_settings(cfg: Dict[str, str]):
    async with get_connection() as db:
        for k, v in cfg.items():
            await db.execute(
                "INSERT INTO global_settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v))
            )
        await db.commit()

async def get_settings(user_id: int) -> Dict[str, str]:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT key, value FROM settings WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return {r["key"]: r["value"] for r in rows}

async def update_settings(user_id: int, cfg: Dict[str, str]):
    async with get_connection() as db:
        for k, v in cfg.items():
            await db.execute(
                "INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?) ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value",
                (user_id, k, str(v))
            )
        await db.commit()

async def get_all_resumes(user_id: int) -> List[Dict[str, Any]]:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM resumes WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

async def save_resume(user_id: int, title: str, content: str):
    async with get_connection() as db:
        await db.execute("INSERT INTO resumes (user_id, title, content) VALUES (?, ?, ?)", (user_id, title, content))
        await db.commit()

async def delete_resume(user_id: int, resume_id: int):
    async with get_connection() as db:
        await db.execute("DELETE FROM resumes WHERE id = ? AND user_id = ?", (resume_id, user_id))
        await db.commit()

async def get_vacancy(v_id: str) -> Optional[Dict[str, Any]]:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vacancies WHERE vacancy_id = ?", (v_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def save_vacancy(
    vacancy_id: str, user_id: int, url: str, title: str, company: str, score: int, resume: str, reasoning: str, letter: str, status: str
):
    async with get_connection() as db:
        await db.execute(
            """
            INSERT INTO vacancies (vacancy_id, user_id, url, title, company, score, selected_resume, reasoning, cover_letter, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(vacancy_id) DO UPDATE SET
                score=excluded.score,
                selected_resume=excluded.selected_resume,
                reasoning=excluded.reasoning,
                cover_letter=excluded.cover_letter,
                status=excluded.status,
                updated_at=CURRENT_TIMESTAMP
            """,
            (vacancy_id, user_id, url, title, company, score, resume, reasoning, letter, status)
        )
        await db.commit()

async def get_stats(user_id: int):
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM vacancies WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)) as cursor:
            rows = [dict(r) for r in await cursor.fetchall()]

        today = rows[0]["updated_at"][:10] if rows else ""
        total = len(rows)
        applied = sum(1 for r in rows if r["status"] == "APPLIED")
        skipped = sum(1 for r in rows if r["status"] == "SKIPPED")
        today_applied = sum(1 for r in rows if r["status"] == "APPLIED" and str(r["updated_at"]).startswith(today))

        return {
            "total": total,
            "applied": applied,
            "skipped": skipped,
            "today_applied": today_applied,
            "vacancies": rows[:100]
        }

async def get_user_data(user_id: int) -> dict:
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            return dict(await cursor.fetchone())

async def add_user_tokens(user_id: int, tokens: int):
    async with get_connection() as db:
        await db.execute("UPDATE users SET tokens_used = tokens_used + ? WHERE id = ?", (tokens, user_id))
        await db.commit()

async def set_user_limit(user_id: int, limit: int):
    async with get_connection() as db:
        if limit < 0:
            await db.execute("UPDATE users SET daily_limit = NULL WHERE id = ?", (user_id,))
        else:
            await db.execute("UPDATE users SET daily_limit = ? WHERE id = ?", (limit, user_id))
        await db.commit()

async def link_telegram_account(sync_code: str, telegram_id: int) -> bool:
    async with get_connection() as db:
        async with db.execute("SELECT id FROM users WHERE sync_code = ?", (sync_code,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                return False
            await db.execute("UPDATE users SET telegram_id = ? WHERE id = ?", (telegram_id, user[0]))
            await db.commit()
            return True
