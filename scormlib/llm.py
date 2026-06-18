"""LLM enrichment layer — optional. Everything in scormlib works without it.

Providers: 'claude' (ANTHROPIC_API_KEY) and 'deepseek' (DEEPSEEK_API_KEY).
Model is configurable via SCORM_CLAUDE_MODEL / SCORM_DEEPSEEK_MODEL env vars."""

import json
import os

DEFAULT_CLAUDE_MODEL = os.environ.get('SCORM_CLAUDE_MODEL', 'claude-sonnet-4-6')
DEFAULT_DEEPSEEK_MODEL = os.environ.get('SCORM_DEEPSEEK_MODEL', 'deepseek-chat')

ENRICH_SCHEMA = """{
  "ai_summary": "2-3 sentence summary of what this course teaches",
  "inferred_skills": ["skill1", "skill2"],
  "target_audience": "who this course is designed for",
  "difficulty_level": "Beginner|Intermediate|Advanced",
  "estimated_duration_minutes": 0,
  "learning_objectives": ["objective1"],
  "category": "single best catalog category for this course",
  "content_quality_flags": ["flag1"],
  "ai_readiness_score": 0,
  "ai_readiness_notes": "what's missing or needs improvement"
}"""


def llm_available(provider='claude'):
    if provider == 'deepseek':
        return bool(os.environ.get('DEEPSEEK_API_KEY'))
    return bool(os.environ.get('ANTHROPIC_API_KEY'))


def parse_json_response(raw):
    """Robustly parse JSON out of an LLM reply (handles code fences, prose)."""
    raw = raw.strip()
    if raw.startswith('```'):
        raw = raw.split('\n', 1)[1] if '\n' in raw else raw
        raw = raw.rsplit('```', 1)[0]
        raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    # Find the first JSON object in the text
    decoder = json.JSONDecoder()
    for i, ch in enumerate(raw):
        if ch == '{':
            try:
                obj, _end = decoder.raw_decode(raw[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise ValueError(f'No JSON object found in LLM response: {raw[:200]}')


def complete(prompt, provider='claude', api_key=None, max_tokens=1500,
             images_b64=None):
    """Single-turn completion. images_b64: optional list of base64 PNG strings."""
    if provider == 'deepseek':
        import requests
        key = api_key or os.environ.get('DEEPSEEK_API_KEY')
        if not key:
            raise RuntimeError('DEEPSEEK_API_KEY not set')
        resp = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}',
                     'Content-Type': 'application/json'},
            json={'model': DEFAULT_DEEPSEEK_MODEL,
                  'messages': [{'role': 'user', 'content': prompt}],
                  'max_tokens': max_tokens, 'temperature': 0.3},
            timeout=90)
        resp.raise_for_status()
        return resp.json()['choices'][0]['message']['content']

    import anthropic
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    if images_b64:
        content = []
        for img in images_b64:
            content.append({'type': 'image',
                            'source': {'type': 'base64',
                                       'media_type': 'image/png', 'data': img}})
        content.append({'type': 'text', 'text': prompt})
    else:
        content = prompt
    response = client.messages.create(
        model=DEFAULT_CLAUDE_MODEL, max_tokens=max_tokens,
        messages=[{'role': 'user', 'content': content}])
    return response.content[0].text


def enrich(metadata, organizations, content, provider='claude', api_key=None,
           baseline_score=None, transcript=None):
    """Full enrichment: summary, skills, audience, objectives, category, score."""
    try:
        modules = []
        for org in organizations or []:
            stack = list(org.get('items', []))
            while stack:
                item = stack.pop(0)
                if item.get('title'):
                    modules.append(item['title'])
                stack = item.get('children', []) + stack

        sections = [
            'You are an L&D analyst. Analyze this SCORM course data and output ONLY valid JSON matching this schema:',
            ENRICH_SCHEMA,
            f"Course Title: {metadata.get('title', 'Unknown')}",
            f"Description: {metadata.get('description', '(none)')}",
            f"Keywords: {', '.join(metadata.get('keywords', [])) or '(none)'}",
            f"Modules: {', '.join(modules[:30]) or '(none)'}",
        ]
        if baseline_score is not None:
            sections.append(f'Deterministic baseline AI-readiness score: {baseline_score}/100')
        if content:
            if content.get('slide_titles'):
                sections.append('Slide titles: ' + '; '.join(content['slide_titles'][:40]))
            if content.get('quiz_questions'):
                sections.append('Quiz questions found: '
                                + ' | '.join(content['quiz_questions'][:15]))
            if content.get('text'):
                sections.append('Extracted content sample:\n' + content['text'][:4000])
        if transcript:
            sections.append('FULL VIDEO TRANSCRIPT (may be partial):\n' + transcript[:6000])

        raw = complete('\n\n'.join(sections), provider=provider, api_key=api_key)
        return parse_json_response(raw)
    except Exception as e:
        return {'error': f'LLM enrichment failed: {e}'}


def vision_analyze_slides(slides_data, course_title, provider='claude',
                          api_key=None, max_images=15):
    """Analyze captured slide screenshots with a vision model."""
    import base64
    from pathlib import Path

    try:
        images = []
        labels = []
        for slide in slides_data[:max_images]:
            path = slide.get('screenshot_path')
            if not path or not Path(path).exists():
                continue
            images.append(base64.standard_b64encode(
                Path(path).read_bytes()).decode())
            labels.append(f"[Slide {slide.get('index')} | "
                          f"type: {slide.get('interaction_type', 'standard')}]")
        if not images:
            return {'error': 'No screenshots available for vision analysis'}

        prompt = (
            'You are an L&D analyst reviewing screenshots of a SCORM e-learning '
            f'course titled "{course_title}". The slides are labeled: '
            + ', '.join(labels)
            + '\n\nAnalyze ALL slides and output ONLY valid JSON:\n'
            + ENRICH_SCHEMA.rstrip('}')
            + ',\n  "quiz_questions": ["question1"],\n'
              '  "slide_summaries": ["slide 1: ...", "slide 2: ..."]\n}')

        raw = complete(prompt, provider='claude', api_key=api_key,
                       max_tokens=2000, images_b64=images)
        return parse_json_response(raw)
    except Exception as e:
        return {'error': f'Vision analysis failed: {e}'}
