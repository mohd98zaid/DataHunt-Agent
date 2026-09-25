"""
InterviewPrepAgent — Steps 57-61 of the job search pipeline.

Generates comprehensive, tailored interview preparation packs from job descriptions
and company background. Evaluates practice user answers with actionable feedback.
"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from datahunt.logger import logger


class InterviewQuestion(BaseModel):
    """Structured question with preparation guidance."""
    question: str
    category: str        # "technical" | "behavioral" | "role_specific" | "company_specific"
    difficulty: str      # "standard" | "challenging" | "expert"
    why_asked: str       # What the interviewer evaluates with this question
    sample_answer: str   # STAR outline or technical principles to mention


class InterviewPrepPack(BaseModel):
    """Full interview preparation package for a candidate."""
    role_title: str
    company_name: str
    technical_questions: List[InterviewQuestion] = Field(default_factory=list)
    behavioral_questions: List[InterviewQuestion] = Field(default_factory=list)
    role_specific_questions: List[InterviewQuestion] = Field(default_factory=list)
    company_specific_questions: List[InterviewQuestion] = Field(default_factory=list)
    questions_for_interviewer: List[str] = Field(default_factory=list)


_PREP_PROMPT = """You are an executive hiring manager and technical interviewer.
Create an interview preparation guide for this target position:
Role: {role}
Company: {company}
Skills & Requirements: {skills}

Generate a structured JSON object with:
{{
  "technical_questions": [
    {{"question": "...", "category": "technical", "difficulty": "challenging", "why_asked": "...", "sample_answer": "Key technical concepts to cover..."}}
  ],
  "behavioral_questions": [
    {{"question": "...", "category": "behavioral", "difficulty": "standard", "why_asked": "...", "sample_answer": "STAR framework approach..."}}
  ],
  "role_specific_questions": [
    {{"question": "...", "category": "role_specific", "difficulty": "challenging", "why_asked": "...", "sample_answer": "..."}}
  ],
  "company_specific_questions": [
    {{"question": "...", "category": "company_specific", "difficulty": "standard", "why_asked": "...", "sample_answer": "..."}}
  ],
  "questions_for_interviewer": [
    "3-4 insightful questions the candidate should ask the hiring team"
  ]
}}
"""

_FEEDBACK_PROMPT = """You are a mock interview coach evaluating a candidate's answer.
Role: {role}
Question: {question}
Candidate's Answer: {answer}

Provide constructive feedback in JSON:
{{
  "score": 8, // out of 10
  "strengths": ["...", "..."],
  "areas_to_improve": ["...", "..."],
  "model_answer_revision": "How to refine the answer using the STAR method..."
}}
"""


class InterviewPrepAgent:
    """
    Steps 57-61: Generates tailored interview preparation packs and evaluates answers.
    """

    def __init__(self, gemini_client=None):
        self._client = gemini_client

    def prepare(
        self,
        role_title: str,
        company_name: str,
        skills: List[str] = None,
        job_description: str = ""
    ) -> InterviewPrepPack:
        """Generate comprehensive interview questions and preparation advice."""
        skills_str = ", ".join(skills or ["Software Architecture", "Problem Solving"])
        
        if self._client and getattr(self._client, "is_live", False):
            try:
                prompt = _PREP_PROMPT.format(
                    role=role_title,
                    company=company_name,
                    skills=skills_str
                )
                res = self._client._call_gemini_json(prompt, schema_description="interview prep JSON", stage="interview_prep")
                if isinstance(res, dict) and "technical_questions" in res:
                    return InterviewPrepPack(
                        role_title=role_title,
                        company_name=company_name,
                        technical_questions=[InterviewQuestion(**q) for q in res.get("technical_questions", [])],
                        behavioral_questions=[InterviewQuestion(**q) for q in res.get("behavioral_questions", [])],
                        role_specific_questions=[InterviewQuestion(**q) for q in res.get("role_specific_questions", [])],
                        company_specific_questions=[InterviewQuestion(**q) for q in res.get("company_specific_questions", [])],
                        questions_for_interviewer=res.get("questions_for_interviewer", [])
                    )
            except Exception as e:
                logger.warning(f"Interview prep LLM generation failed ({e}), using standard template")

        # Deterministic default pack
        return InterviewPrepPack(
            role_title=role_title,
            company_name=company_name,
            technical_questions=[
                InterviewQuestion(
                    question=f"Describe your end-to-end architecture and approach when deploying {skills_str[:30]} in production.",
                    category="technical",
                    difficulty="challenging",
                    why_asked="Assesses real-world systems architecture, tradeoffs, and reliability practices.",
                    sample_answer="Discuss modular design, automated testing, containerized deployment, observability, and failure recovery."
                ),
                InterviewQuestion(
                    question="How do you handle scaling bottlenecks and database/API latency under peak loads?",
                    category="technical",
                    difficulty="challenging",
                    why_asked="Tests understanding of caching (Redis), asynchronous queues, and database indexing.",
                    sample_answer="Break down profiling methodology, indexing, caching layers, and graceful degradation."
                )
            ],
            behavioral_questions=[
                InterviewQuestion(
                    question="Tell me about a time you had to deliver a critical project under tight deadlines with ambiguous requirements.",
                    category="behavioral",
                    difficulty="standard",
                    why_asked="Evaluates ownership, stakeholder communication, and iterative delivery.",
                    sample_answer="Use STAR: Situation, Task, Action (prioritized MVP, aligned with product), Result."
                ),
                InterviewQuestion(
                    question="Describe a disagreement with a team member or technical lead and how you resolved it.",
                    category="behavioral",
                    difficulty="standard",
                    why_asked="Tests emotional intelligence, collaborative problem-solving, and evidence-backed decision making.",
                    sample_answer="Focus on empathy, running quick benchmarks/POCs, and committing to team decisions."
                )
            ],
            role_specific_questions=[
                InterviewQuestion(
                    question=f"What are the most critical metrics you monitor when operating as a {role_title}?",
                    category="role_specific",
                    difficulty="standard",
                    why_asked="Verifies domain depth and business impact awareness.",
                    sample_answer="Connect technical KPIs (latency, error rates, model drift) to business outcomes."
                )
            ],
            company_specific_questions=[
                InterviewQuestion(
                    question=f"Why are you interested in joining {company_name} at this stage of its growth?",
                    category="company_specific",
                    difficulty="standard",
                    why_asked="Tests genuine company research and alignment with mission.",
                    sample_answer=f"Cite specific products, engineering challenges, or market achievements of {company_name}."
                )
            ],
            questions_for_interviewer=[
                f"What does success look like in the first 90 days for this {role_title} role?",
                "What is the team's deployment frequency and biggest technical hurdle this quarter?",
                "How does the engineering team prioritize technical debt versus feature velocity?"
            ]
        )

    def evaluate_answer(self, role_title: str, question: str, answer: str) -> Dict[str, Any]:
        """Provide automated feedback on a candidate's practice response."""
        if not answer.strip():
            return {"score": 0, "feedback": "Please provide an answer to receive feedback."}

        if self._client and getattr(self._client, "is_live", False):
            try:
                prompt = _FEEDBACK_PROMPT.format(role=role_title, question=question, answer=answer)
                res = self._client._call_gemini_json(prompt, schema_description="answer feedback JSON", stage="interview_eval")
                if isinstance(res, dict) and "score" in res:
                    return res
            except Exception as e:
                logger.warning(f"Answer evaluation LLM failed: {e}")

        # Deterministic feedback
        word_count = len(answer.split())
        score = 6 if word_count < 30 else (8 if word_count >= 80 else 7)
        return {
            "score": score,
            "strengths": ["Direct response to the prompt", "Clear communication"],
            "areas_to_improve": ["Include more concrete metrics or quantifiable outcomes", "Structure clearly with Situation, Action, Result"],
            "model_answer_revision": "Strengthen the answer by starting with the context, specifying the exact actions you owned, and ending with measurable impact."
        }
