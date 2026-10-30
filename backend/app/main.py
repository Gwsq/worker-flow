import io
import os
import secrets
import zipfile
import random
import string
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
import aiosqlite
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse, HTMLResponse
from pydantic import BaseModel, EmailStr
from app.config import settings
from app.database import ( get_user_data, add_user_tokens, set_user_limit, link_telegram_account, 
    delete_resume, get_all_resumes, get_settings, get_stats,
    get_vacancy, init_db, save_resume, save_vacancy, update_settings, get_connection,
    get_global_settings, update_global_settings
)
from app.openrouter import llm_service
from app.auth import hash_password, verify_password, create_access_token, get_current_user, get_current_admin


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


app = FastAPI(title="Worker Flow SaaS", version="5.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class AnalyzeRequest(BaseModel):
    vacancy_id: str
    url: str
    title: str
    company: str
    full_text: str


class RegenerateRequest(BaseModel):
    vacancy_id: str
    user_context: str
    vacancy_text: Optional[str] = ""


class ReportStatusRequest(BaseModel):
    vacancy_id: str
    url: str
    title: str = ""
    company: str = ""
    score: int = 0
    selected_resume: str = ""
    reasoning: str = ""
    cover_letter: str = ""
    status: str


class QuestionnaireRequest(BaseModel):
    questions: List[Dict[str, Any]]


class HRReplyRequest(BaseModel):
    hr_message: str
    user_hint: str = ""


class ATSBuilderRequest(BaseModel):
    target_role: str
    raw_experience: str
    target_vacancy: str = ""


def render_html_file(filename: str) -> HTMLResponse:
    template_path = os.path.join(os.path.dirname(__file__), "templates", filename)
    with open(template_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(
        content=html_content,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}
    )


@app.get("/", response_class=HTMLResponse)
async def serve_root(request: Request):
    host = request.headers.get("host", "").lower()
    if host.startswith("admin."):
        return render_html_file("admin.html")
    return render_html_file("index.html")


@app.get("/admin", response_class=HTMLResponse)
async def serve_admin_portal():
    return render_html_file("admin.html")


@app.get("/api/v1/download-extension")
async def download_extension():
    ext_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "extension"))
    if not os.path.exists(ext_dir):
        raise HTTPException(status_code=404, detail="Директория расширения не найдена")

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(ext_dir):
            for file in files:
                if file.startswith("."): continue
                full_path = os.path.join(root, file)
                rel_path = os.path.relpath(full_path, ext_dir)
                zf.write(full_path, arcname=os.path.join("worker-flow-extension", rel_path))

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer, media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="worker-flow-extension.zip"'}
    )


# --- AUTH ---

@app.post("/api/v1/auth/register")
async def register(req: RegisterRequest, request: Request):
    async with get_connection() as db:
        async with db.execute("SELECT id FROM users WHERE email = ?", (req.email,)) as cursor:
            if await cursor.fetchone():
                raise HTTPException(status_code=400, detail="Пользователь уже зарегистрирован")

        verify_token = secrets.token_urlsafe(32)
        sync_code = "WF-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        pwd_hash = hash_password(req.password)

        cursor = await db.execute(
            "INSERT INTO users (email, password_hash, role, verify_token, is_verified, sync_code) VALUES (?, ?, 'user', ?, 0, ?)",
            (req.email, pwd_hash, verify_token, sync_code)
        )
        user_id = cursor.lastrowid

        default_cfg = {
            "salary": "160000", "telegram": "@onecappa",
            "search_query": "SRE OR Monitoring OR DevOps OR Системный инженер", "search_area": "1",
            "search_experience": "between3And6", "search_schedule": "remote", "pages_depth": "3",
            "allow_auto_apply": "1", "allow_hh_tests": "1", "allow_external_forms": "1",
            "pref_remote": "1", "pref_hybrid": "1", "pref_office": "0",
            "pref_standard": "1", "pref_shift": "1", "pref_night": "0",
            "stop_words": "1С, продажи, холодные звонки", "ignored_companies": "Рога и Копыта", "delay_min": "15", "delay_max": "45", "stealth_mode": "1", "target_language": "Русский", "cover_letter_tone": "Профессиональный и уверенный", "title_stop_words": "Junior, Стажер, Ассистент, Ученик"
        }
        for k, v in default_cfg.items():
            await db.execute("INSERT INTO settings (user_id, key, value) VALUES (?, ?, ?)", (user_id, k, v))
        await db.commit()

    base_url = f"{request.url.scheme}://{request.headers.get('host', 'localhost:8000')}"
    verify_link = f"{base_url}/api/v1/auth/verify?token={verify_token}"
    print(f"\n[ПОДТВЕРЖДЕНИЕ] {verify_link}\n")
    return {"status": "success", "message": "Проверьте консоль сервера для подтверждения.", "dev_verify_link": verify_link}


@app.get("/api/v1/auth/verify", response_class=HTMLResponse)
async def verify_email(token: str):
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE verify_token = ?", (token,)) as cursor:
            user = await cursor.fetchone()
            if not user:
                return "<h2>❌ Ссылка недействительна</h2><a href='/'>На главную</a>"
            await db.execute("UPDATE users SET is_verified = 1, verify_token = NULL WHERE id = ?", (user["id"],))
            await db.commit()
    return "<h2>✅ Почта подтверждена!</h2><a href='/'>Войти в систему</a>"


@app.post("/api/v1/auth/login")
async def user_login(req: LoginRequest):
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE email = ?", (req.email,)) as cursor:
            user = await cursor.fetchone()
            if not user or not verify_password(req.password, user["password_hash"]):
                raise HTTPException(status_code=400, detail="Неверный email или пароль")
            if user["is_verified"] == 0:
                raise HTTPException(status_code=403, detail="Аккаунт не подтвержден")
            token = create_access_token({"sub": user["id"], "role": user["role"]})
            return {"access_token": token, "token_type": "bearer", "role": user["role"], "email": user["email"]}


@app.post("/api/v1/auth/admin-login")
async def admin_login(req: LoginRequest):
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM users WHERE email = ?", (req.email,)) as cursor:
            user = await cursor.fetchone()
            if not user or not verify_password(req.password, user["password_hash"]) or user["role"] != "admin":
                raise HTTPException(status_code=401, detail="Неверные учетные данные администратора")
            token = create_access_token({"sub": user["id"], "role": "admin"})
            return {"access_token": token, "token_type": "bearer", "role": "admin", "email": user["email"]}



class BotLinkRequest(BaseModel):
    sync_code: str
    telegram_id: int

@app.post("/api/v1/bot/link")
async def bot_link_account(req: BotLinkRequest):
    # Этот эндпоинт будет дергать твой aiogram-бот
    success = await link_telegram_account(req.sync_code, req.telegram_id)
    if not success:
        raise HTTPException(status_code=404, detail="Неверный код синхронизации")
    return {"status": "linked"}


# --- PROMETHEUS EXPORTER ---
@app.get("/metrics")
async def get_prometheus_metrics():
    async with get_connection() as db:
        # Считаем пользователей
        async with db.execute("SELECT COUNT(id) FROM users") as cursor:
            users_total = (await cursor.fetchone())[0]
            
        # Считаем токены
        async with db.execute("SELECT SUM(tokens_used) FROM users") as cursor:
            tokens_total = (await cursor.fetchone())[0] or 0
            
        # Считаем резюме
        async with db.execute("SELECT COUNT(id) FROM resumes") as cursor:
            resumes_total = (await cursor.fetchone())[0]
            
        # Статистика по статусам откликов
        async with db.execute("SELECT status, COUNT(id) FROM vacancies GROUP BY status") as cursor:
            vacancies_stats = await cursor.fetchall()

    global_cfg = await get_global_settings()
    api_key = global_cfg.get("openrouter_api_key", "")
    
    openrouter_usage = None
    openrouter_limit = None
    
    if api_key:
        import httpx
        try:
            # Делаем быстрый запрос баланса (таймаут 2.5 сек, чтобы экспортер не тормозил)
            async with httpx.AsyncClient(timeout=2.5) as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/auth/key",
                    headers={"Authorization": f"Bearer {api_key}"}
                )
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    openrouter_usage = data.get("usage")
                    openrouter_limit = data.get("limit")
        except Exception as e:
            pass  # Если OpenRouter недоступен, просто пропускаем эту метрику

    # Формируем метрики в формате Prometheus / OpenMetrics
    metrics = []
    
    metrics.append("# HELP workerflow_users_total Total number of registered users")
    metrics.append("# TYPE workerflow_users_total gauge")
    metrics.append(f"workerflow_users_total {users_total}")
    
    metrics.append("# HELP workerflow_llm_tokens_total Total LLM tokens consumed across all users")
    metrics.append("# TYPE workerflow_llm_tokens_total counter")
    metrics.append(f"workerflow_llm_tokens_total {tokens_total}")
    
    metrics.append("# HELP workerflow_resumes_total Total resumes stored in the knowledge base")
    metrics.append("# TYPE workerflow_resumes_total gauge")
    metrics.append(f"workerflow_resumes_total {resumes_total}")
    
    metrics.append("# HELP workerflow_applications_total Total job applications processed grouped by status")
    metrics.append("# TYPE workerflow_applications_total gauge")
    for row in vacancies_stats:
        status = row[0] or "UNKNOWN"
        count = row[1]
        metrics.append(f'workerflow_applications_total{{status="{status}"}} {count}')

    if openrouter_usage is not None:
        metrics.append("# HELP workerflow_openrouter_usage_dollars Total spend on the current OpenRouter API key")
        metrics.append("# TYPE workerflow_openrouter_usage_dollars gauge")
        metrics.append(f"workerflow_openrouter_usage_dollars {openrouter_usage}")
        
    if openrouter_limit is not None:
        metrics.append("# HELP workerflow_openrouter_limit_dollars Hard limit on the current OpenRouter API key")
        metrics.append("# TYPE workerflow_openrouter_limit_dollars gauge")
        metrics.append(f"workerflow_openrouter_limit_dollars {openrouter_limit}")
        
        # Вычисляем остаток баланса до лимита
        remaining = float(openrouter_limit) - float(openrouter_usage)
        metrics.append("# HELP workerflow_openrouter_credits_remaining_dollars Remaining budget before limit is hit")
        metrics.append("# TYPE workerflow_openrouter_credits_remaining_dollars gauge")
        metrics.append(f"workerflow_openrouter_credits_remaining_dollars {remaining}")

    return Response(content="\n".join(metrics) + "\n", media_type="text/plain")

# --- ADMIN API ---


@app.get("/api/v1/admin/overview")
async def admin_overview(admin: dict = Depends(get_current_admin)):
    async with get_connection() as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT u.id, u.email, u.role, u.is_verified, u.created_at, u.daily_limit, u.tokens_used,
                   (SELECT count(*) FROM vacancies v WHERE v.user_id = u.id) as total_vacancies,
                   (SELECT count(*) FROM vacancies v WHERE v.user_id = u.id AND v.status = 'APPLIED') as applied_count,
                   (SELECT count(*) FROM resumes r WHERE r.user_id = u.id) as resumes_count
            FROM users u ORDER BY u.id DESC
        """) as cursor:
            users = [dict(r) for r in await cursor.fetchall()]

        async with db.execute("SELECT count(*) FROM vacancies WHERE status = 'APPLIED'") as c2:
            total_platform_applied = (await c2.fetchone())[0]

        async with db.execute("SELECT * FROM vacancies ORDER BY updated_at DESC LIMIT 30") as c3:
            recent_vacancies = [dict(r) for r in await c3.fetchall()]

        global_cfg = await get_global_settings()

        return {
            "admin_email": admin["email"],
            "total_users": len(users),
            "verified_users": sum(1 for u in users if u["is_verified"] == 1),
            "total_platform_applied": total_platform_applied,
            "global_settings": global_cfg,
            "users": users,
            "recent_vacancies": recent_vacancies
        }


@app.post("/api/v1/admin/settings")
async def admin_save_settings(payload: Dict[str, str], admin: dict = Depends(get_current_admin)):
    await update_global_settings(payload)
    return {"status": "updated"}


@app.post("/api/v1/admin/users/{user_id}/verify")
async def admin_verify_user(user_id: int, admin: dict = Depends(get_current_admin)):
    async with get_connection() as db:
        await db.execute("UPDATE users SET is_verified = 1, verify_token = NULL WHERE id = ?", (user_id,))
        await db.commit()
    return {"status": "verified"}


@app.delete("/api/v1/admin/users/{user_id}")
async def admin_delete_user(user_id: int, admin: dict = Depends(get_current_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Нельзя удалить себя")
    async with get_connection() as db:
        await db.execute("DELETE FROM vacancies WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM resumes WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM settings WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.commit()
    return {"status": "deleted"}


@app.post("/api/v1/admin/users/{user_id}/limit")
async def admin_set_limit(user_id: int, payload: dict, admin: dict = Depends(get_current_admin)):
    await set_user_limit(user_id, payload.get("limit", -1))
    return {"status": "updated"}

# --- USER API ---

@app.get("/api/v1/dashboard")
async def get_dashboard_data(current_user: dict = Depends(get_current_user)):
    stats = await get_stats(current_user["id"])
    cfg = await get_settings(current_user["id"])
    resumes = await get_all_resumes(current_user["id"])
    user_data = await get_user_data(current_user["id"])
    global_cfg = await get_global_settings()
    cfg["daily_limit"] = user_data["daily_limit"] if user_data["daily_limit"] is not None else global_cfg.get("daily_limit", 50)
    return {
        "user": {"id": current_user["id"], "email": current_user["email"], "sync_code": user_data.get("sync_code"), "telegram_id": user_data.get("telegram_id")},
        "stats": stats,
        "cfg": cfg,
        "resumes": resumes,
    }


@app.get("/api/v1/config")
async def get_client_config(current_user: dict = Depends(get_current_user)):
    cfg = await get_settings(current_user["id"])
    stats = await get_stats(current_user["id"])
    global_cfg = await get_global_settings()
    return {
        "status": "ok",
        "min_score": int(global_cfg.get("min_score", 60)),
        "daily_limit": int(global_cfg.get("daily_limit", 50)),
        "today_applied": stats.get("today_applied", 0),
        "salary": cfg.get("salary", "160000"),
        "telegram": cfg.get("telegram", "@onecappa"),
        "search": {
            "query": cfg.get("search_query", ""), "area": cfg.get("search_area", "1"),
            "experience": cfg.get("search_experience", "between3And6"),
            "schedule": cfg.get("search_schedule", "remote"), "pages_depth": int(cfg.get("pages_depth", 3)),
        },
        "security": {
            "delay_min": int(cfg.get("delay_min", 15)),
            "delay_max": int(cfg.get("delay_max", 45)),
            "stealth_mode": cfg.get("stealth_mode", "1") == "1",
        },
        "permissions": {
            "auto_apply": cfg.get("allow_auto_apply") == "1",
            "hh_tests": cfg.get("allow_hh_tests") == "1",
            "external_forms": cfg.get("allow_external_forms") == "1",
        },
        "title_stop_words": [w.strip().lower() for w in cfg.get("title_stop_words", "").split(",") if w.strip()],
        "target_language": cfg.get("target_language", "Русский"),
        "cover_letter_tone": cfg.get("cover_letter_tone", "Профессиональный и уверенный"),
        "stop_words": [w.strip().lower() for w in cfg.get("stop_words", "").split(",") if w.strip()],
        "ignored_companies": [c.strip().lower() for c in cfg.get("ignored_companies", "").split(",") if c.strip()],
    }


@app.post("/api/v1/build-ats-resume")
async def build_ats_resume_endpoint(req: ATSBuilderRequest, current_user: dict = Depends(get_current_user)):
    global_cfg = await get_global_settings()
    api_key = global_cfg.get("openrouter_key") or settings.OPENROUTER_API_KEY
    model = global_cfg.get("openrouter_model") or "meta-llama/llama-3.3-70b-instruct"

    if not api_key:
        raise HTTPException(status_code=400, detail="Администратор платформы не настроил OpenRouter API Key")

    try:
        res, tokens = await llm_service.build_ats_resume(
            api_key=api_key, model=model, target_role=req.target_role,
            raw_experience=req.raw_experience, target_vacancy=req.target_vacancy
        )
        await add_user_tokens(current_user["id"], tokens)
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/v1/check-duplicate")
async def check_duplicate(payload: Dict[str, str], current_user: dict = Depends(get_current_user)):
    v_id = payload.get("vacancy_id")
    if not v_id: return {"exists": False}
    existing = await get_vacancy(v_id)
    return {"exists": bool(existing), "status": existing["status"] if existing else None}


@app.post("/api/v1/analyze")
async def analyze_vacancy(req: AnalyzeRequest, current_user: dict = Depends(get_current_user)):
    existing = await get_vacancy(req.vacancy_id)
    if existing: return {"should_apply": False, "reason": f"Уже в базе ({existing['status']})"}

    cfg = await get_settings(current_user["id"])
    resumes = await get_all_resumes(current_user["id"])
    global_cfg = await get_global_settings()

    if not resumes:
        raise HTTPException(status_code=400, detail="Добавьте хотя бы одно резюме в Базу Знаний")

    stop_words = [w.strip().lower() for w in cfg.get("stop_words", "").split(",") if w.strip()]
    ignored_comps = [c.strip().lower() for c in cfg.get("ignored_companies", "").split(",") if c.strip()]
    title_stops = [w.strip().lower() for w in cfg.get("title_stop_words", "").split(",") if w.strip()]

    if any(ts in req.title.lower() for ts in title_stops):
        await save_vacancy(req.vacancy_id, current_user["id"], req.url, req.title, req.company, 0, "-", f"Минус-слово в названии", "", "SKIPPED")
        return {"should_apply": False, "score": 0, "reason": "Минус-слово в названии"}

    if any(c in req.company.lower() for c in ignored_comps):
        await save_vacancy(req.vacancy_id, current_user["id"], req.url, req.title, req.company, 0, "-", "Компания в ЧС", "", "SKIPPED")
        return {"should_apply": False, "score": 0, "reason": "Компания в ЧС"}

    found_stop = next((w for w in stop_words if w in req.full_text.lower()), None)
    if found_stop:
        await save_vacancy(req.vacancy_id, current_user["id"], req.url, req.title, req.company, 0, "-", f"Стоп-слово: {found_stop}", "", "SKIPPED")
        return {"should_apply": False, "score": 0, "reason": f"Стоп-слово: {found_stop}"}

    api_key = global_cfg.get("openrouter_key") or settings.OPENROUTER_API_KEY
    model = global_cfg.get("openrouter_model") or "meta-llama/llama-3.3-70b-instruct"

    if not api_key:
        raise HTTPException(status_code=400, detail="Администратор не указал OpenRouter API Key")

    eval_data, tokens = await llm_service.analyze_and_draft(
        api_key=api_key, model=model, vacancy_text=req.full_text,
        resumes=[{"title": r["title"], "content": r["content"]} for r in resumes],
        career_legend=global_cfg.get("global_career_legend", ""),
        ai_instructions=global_cfg.get("global_ai_instructions", ""),
        target_language=cfg.get("target_language", "Русский"),
        tone_of_voice=cfg.get("cover_letter_tone", "Профессиональный и уверенный")
    )
    await add_user_tokens(current_user["id"], tokens)

    min_score = int(global_cfg.get("min_score", 60))
    score = int(eval_data.get("match_score", 0))
    reasoning = eval_data.get("reasoning", "")
    selected_resume = eval_data.get("selected_resume_title", "")
    cover_letter = eval_data.get("cover_letter", "")

    status = "APPLIED" if score >= min_score else "SKIPPED"
    await save_vacancy(req.vacancy_id, current_user["id"], req.url, req.title, req.company, score, selected_resume, reasoning, cover_letter, status)

    if score < min_score:
        return {"should_apply": False, "score": score, "reason": f"Скор {score}% < {min_score}%"}

    return {"should_apply": True, "score": score, "selected_resume_title": selected_resume, "cover_letter": cover_letter, "reasoning": reasoning}


@app.post("/api/v1/regenerate")
async def regenerate_letter(req: RegenerateRequest, current_user: dict = Depends(get_current_user)):
    v = await get_vacancy(req.vacancy_id)
    if not v: raise HTTPException(status_code=404, detail="Вакансия не найдена")

    cfg = await get_settings(current_user["id"])
    resumes = await get_all_resumes(current_user["id"])
    global_cfg = await get_global_settings()

    api_key = global_cfg.get("openrouter_key") or settings.OPENROUTER_API_KEY
    model = global_cfg.get("openrouter_model") or "meta-llama/llama-3.3-70b-instruct"

    v_text = req.vacancy_text or f"Позиция: {v['title']}\nКомпания: {v['company']}"
    eval_data, tokens = await llm_service.analyze_and_draft(
        api_key=api_key, model=model, vacancy_text=v_text,
        resumes=[{"title": r["title"], "content": r["content"]} for r in resumes],
        career_legend=global_cfg.get("global_career_legend", ""),
        ai_instructions=global_cfg.get("global_ai_instructions", ""),
        user_context=req.user_context,
        target_language=cfg.get("target_language", "Русский"),
        tone_of_voice=cfg.get("cover_letter_tone", "Профессиональный и уверенный")
    )
    await add_user_tokens(current_user["id"], tokens)

    await save_vacancy(
        req.vacancy_id, current_user["id"], v["url"], v["title"], v["company"],
        int(eval_data.get("match_score", v["score"])), eval_data.get("selected_resume_title", v["selected_resume"]),
        eval_data.get("reasoning", ""), eval_data.get("cover_letter", ""), v["status"]
    )
    return eval_data


@app.post("/api/v1/solve-questions")
async def solve_questions(req: QuestionnaireRequest, current_user: dict = Depends(get_current_user)):
    cfg = await get_settings(current_user["id"])
    resumes = await get_all_resumes(current_user["id"])
    global_cfg = await get_global_settings()
    api_key = global_cfg.get("openrouter_key") or settings.OPENROUTER_API_KEY
    model = global_cfg.get("openrouter_model") or "meta-llama/llama-3.3-70b-instruct"

    return {"answers": await llm_service.solve_questions(
        api_key=api_key, model=model, questions=req.questions, resumes=resumes,
        career_legend=global_cfg.get("global_career_legend", ""), salary=cfg.get("salary", "160000"), telegram=f"TG: {cfg.get('telegram', '')} | WA: {cfg.get('whatsapp', '')} | Тел: {cfg.get('phone_contact', '')} | Email: {cfg.get('email_contact', '')} | Доп: {cfg.get('extra_contact', '')}"
    )}


@app.post("/api/v1/hr-reply")
async def generate_hr_reply_endpoint(req: HRReplyRequest, current_user: dict = Depends(get_current_user)):
    cfg = await get_settings(current_user["id"])
    resumes = await get_all_resumes(current_user["id"])
    global_cfg = await get_global_settings()
    api_key = global_cfg.get("openrouter_key") or settings.OPENROUTER_API_KEY
    model = global_cfg.get("openrouter_model") or "meta-llama/llama-3.3-70b-instruct"

    reply, tokens = await llm_service.generate_hr_reply(
        api_key=api_key, model=model, hr_message=req.hr_message, user_hint=req.user_hint,
        career_legend=global_cfg.get("global_career_legend", ""), resumes=resumes,
        telegram=f"TG: {cfg.get('telegram', '')} | WA: {cfg.get('whatsapp', '')} | Тел: {cfg.get('phone_contact', '')} | Email: {cfg.get('email_contact', '')} | Доп: {cfg.get('extra_contact', '')}", salary=cfg.get("salary", "160000")
    )
    await add_user_tokens(current_user["id"], tokens)
    return {"reply": reply}


@app.post("/api/v1/report")
async def report_status(req: ReportStatusRequest, current_user: dict = Depends(get_current_user)):
    await save_vacancy(
        req.vacancy_id, current_user["id"], req.url, req.title, req.company,
        req.score, req.selected_resume, req.reasoning, req.cover_letter, req.status
    )
    return {"status": "recorded"}


@app.post("/api/v1/settings")
async def save_settings_endpoint(payload: Dict[str, Any], current_user: dict = Depends(get_current_user)):
    await update_settings(current_user["id"], {k: str(v) for k, v in payload.items()})
    return {"status": "updated"}


@app.post("/api/v1/resumes")
async def add_resume_endpoint(payload: Dict[str, str], current_user: dict = Depends(get_current_user)):
    title = payload.get("title", "").strip()
    content = payload.get("content", "").strip()
    if title and content: await save_resume(current_user["id"], title, content)
    return {"status": "created"}


@app.delete("/api/v1/resumes/{resume_id}")
async def delete_resume_endpoint(resume_id: int, current_user: dict = Depends(get_current_user)):
    await delete_resume(current_user["id"], resume_id)
    return {"status": "deleted"}
