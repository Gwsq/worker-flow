import json
from typing import Any, Dict, List
import httpx


class LLMService:
    def __init__(self):
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    async def analyze_and_draft(
        self,
        api_key: str,
        model: str,
        vacancy_text: str,
        resumes: List[Dict[str, Any]],
        career_legend: str = "",
        ai_instructions: str = "",
        user_context: str = "",
        target_language: str = "Русский",
        tone_of_voice: str = "Профессиональный и уверенный",
    ) -> tuple[Dict[str, Any], int]:
        
        system_prompt = (
            "You are a universal Senior AI Recruiter and HR Director for a SaaS platform. Your expertise covers ALL business domains: sales, marketing, management, IT, design, law, and finance.\n"
            "Your task is to evaluate a candidate's resume against a job description using SEMANTIC and CONCEPTUAL analysis, avoiding primitive keyword matching.\n\n"
            "EVALUATION RULES (match_score from 0 to 100):\n"
            "1. Evaluate semantic analogies and skill transferability. Do not lower the score if a specific tool is missing but the candidate is proficient in its industry equivalent.\n"
            "2. Examples of semantic analogies across domains:\n"
            "   - Marketing: Experience with Facebook Ads or Google Ads transfers easily to Yandex.Direct or VK Ads (domain: traffic acquisition).\n"
            "   - Sales: Complex B2B sales experience transfers to Enterprise sales or account management.\n"
            "   - Management: Kanban is analogous to Scrum (Agile methodologies).\n"
            "   - IT/Engineering: Zabbix experience is fully covered by Grafana/Prometheus (domain: monitoring infrastructure).\n"
            "   - Design: Figma = Sketch (vector UI editors).\n"
            "3. Consider scale: if a candidate managed 40+ people, million-dollar budgets, or enterprise projects, and the vacancy requires a smaller scale, they are 100% capable.\n\n"
            "OUTPUT REQUIREMENTS (Return ONLY a valid JSON object):\n"
            "1. 'selected_resume_title' - choose the best matching resume from the pool.\n"
            "2. 'match_score' - relevance (0-100) factoring in semantic analogies.\n"
            "3. 'reasoning' - briefly explain your decision. If tools/names differ but are semantically analogous, explicitly mention it (e.g., 'Experience with Google Ads fully covers the requirement for Yandex.Direct').\n"
            "4. 'cover_letter' - a unique, confident cover letter. Gracefully smooth over tool differences by demonstrating the candidate's core domain expertise.\n"
            f"\nTONE OF VOICE (Стиль письма): {tone_of_voice}\nGLOBAL PLATFORM INSTRUCTIONS:\n{ai_instructions}\n\n"
            "CRITICAL LANGUAGE RULE: ALL generated text in the JSON values ('reasoning', 'cover_letter') MUST be strictly in {target_language}. Your internal reasoning is English, but your output must be translated."
        )
        
        if user_context:
            system_prompt += f"\nADDITIONAL USER CONTEXT (for regeneration or emphasis):\n{user_context}\n"

        system_prompt += (
            "\nExpected JSON format:\n"
            "{\n"
            '  "selected_resume_title": "resume_title",\n'
            '  "match_score": 85,\n'
            '  "reasoning": "Semantic reasoning in Russian",\n'
            '  "cover_letter": "Cover letter text in Russian"\n'
            "}"
        )

        user_content = (
            f"CANDIDATE PROFILE (LEGEND):\n{career_legend}\n\n"
            f"RESUME POOL:\n{json.dumps(resumes, ensure_ascii=False)}\n\n"
            f"JOB DESCRIPTION (VACANCY):\n{vacancy_text}"
        )

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model or "meta-llama/llama-3.3-70b-instruct",
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(self.url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise Exception(f"OpenRouter Error {resp.status_code}: {resp.text}")
            data = resp.json()
            return json.loads(data["choices"][0]["message"]["content"]), data.get("usage", {}).get("total_tokens", 0)

    async def build_ats_resume(
        self,
        api_key: str,
        model: str,
        target_role: str,
        raw_experience: str,
        target_vacancy: str = "",
    ) -> tuple[Dict[str, Any], int]:
        system_prompt = (
            "You are an elite Career Expert and Resume Architect for ANY profession (from marketing and sales to IT, medicine, and top management).\n"
            "Your task is to transform the user's raw experience or drafts into a perfect, professional, highly-selling resume optimized for modern Applicant Tracking Systems (ATS).\n\n"
            "RULES:\n"
            "1. Structure the experience using strong action verbs and measurable results (metrics, percentages, volumes, KPIs, ROI).\n"
            "2. Fix typos, stylistic errors, and make the text concise (remove fluff and clichés like 'responsible' or 'good communicator').\n"
            "3. If the user provides a single paragraph for 'AI-editing', improve it while preserving the original meaning and industry-specific context.\n\n"
            "CRITICAL LANGUAGE RULE: ALL generated text in the JSON values MUST be in RUSSIAN (or match the language of the user's raw input).\n\n"
            "Expected JSON format:\n"
            "{\n"
            '  "optimized_title": "Professional resume title",\n'
            '  "ats_score": 95,\n'
            '  "executive_summary": "Selling summary (3-4 sentences)",\n'
            '  "skills_tags": ["Skill 1", "Tool 2", "Methodology 3", "...up to 25 tags"],\n'
            '  "experience_blocks": [\n'
            "    {\n"
            '      "company_and_period": "Company / Period",\n'
            '      "role": "Job Title",\n'
            '      "bullets": ["Achievement with metric 1", "Area of responsibility 2"]\n'
            "    }\n"
            "  ]\n"
            "}"
        )

        user_content = (
            f"TARGET JOB TITLE:\n{target_role}\n\n"
            f"RAW EXPERIENCE DATA / PARAGRAPH TO EDIT:\n{raw_experience}\n"
        )

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model or "meta-llama/llama-3.3-70b-instruct",
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(self.url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise Exception(f"OpenRouter Error {resp.status_code}: {resp.text}")
            data = resp.json()
            return json.loads(data["choices"][0]["message"]["content"]), data.get("usage", {}).get("total_tokens", 0)

    async def solve_questions(
        self,
        api_key: str,
        model: str,
        questions: List[Dict[str, Any]],
        resumes: List[Dict[str, Any]],
        career_legend: str = "",
        salary: str = "",
        telegram: str = "",
    ) -> tuple[List[Dict[str, Any]], int]:
        system_prompt = (
            "You are filling out an employer's screening questionnaire on behalf of a candidate.\n"
            f"Salary expectations: {salary}. Contact info: {telegram}.\n"
            "Answer professionally based on the candidate's knowledge base and resume pool.\n"
            "CRITICAL LANGUAGE RULE: ALL answers MUST be in RUSSIAN (or match the language of the questions).\n\n"
            "Expected JSON format: {'answers': [{'id': '...', 'answer': '...'}]}"
        )
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model or "meta-llama/llama-3.3-70b-instruct",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Candidate Profile:\n{career_legend}\nResumes:\n{json.dumps(resumes, ensure_ascii=False)}\n\nQuestions:\n{json.dumps(questions, ensure_ascii=False)}"},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(self.url, headers=headers, json=payload)
            if resp.status_code != 200: return []
            data = resp.json()
            parsed = json.loads(data["choices"][0]["message"]["content"])
            return parsed.get("answers", parsed.get("questions", [])), data.get("usage", {}).get("total_tokens", 0)

    async def generate_hr_reply(
        self,
        api_key: str,
        model: str,
        hr_message: str,
        user_hint: str,
        career_legend: str,
        resumes: List[Dict[str, Any]],
        telegram: str,
        salary: str,
    ) -> tuple[str, int]:
        system_prompt = (
            "You are assisting a candidate in replying to a recruiter in a chat.\n"
            f"Contact info: {telegram}. Desired salary: {salary}.\n"
            "Write a polite, confident, and concise first-person reply.\n"
            "CRITICAL LANGUAGE RULE: The generated reply MUST be in RUSSIAN (or match the language of the recruiter's message)."
        )
        user_content = f"Candidate Profile:\n{career_legend}\nResumes:\n{json.dumps(resumes, ensure_ascii=False)}\n\nMessage from HR:\n{hr_message}\nUser's hint/wish for the reply: {user_hint}"
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        payload = {
            "model": model or "meta-llama/llama-3.3-70b-instruct",
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_content}],
            "temperature": 0.3,
        }
        async with httpx.AsyncClient(timeout=35.0) as client:
            resp = await client.post(self.url, headers=headers, json=payload)
            if resp.status_code != 200:
                raise Exception(f"OpenRouter Error {resp.status_code}")
            return resp.json()["choices"][0]["message"]["content"], resp.json().get("usage", {}).get("total_tokens", 0)


llm_service = LLMService()
