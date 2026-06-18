"""FastAPI application: upload SCORM packages, run the scormlib pipeline in
background jobs, browse results and rendered reports."""

import os
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from scormlib import llm as llm_mod
from scormlib.report_html import build_html_report

from .jobs import JobManager
from .ui import INDEX_HTML

MAX_UPLOAD_MB = int(os.environ.get('SCORMWEB_MAX_MB', '500'))


def create_app(data_dir=None, max_workers=None):
    data_dir = Path(data_dir or os.environ.get('SCORMWEB_DATA_DIR', 'output/web_jobs'))
    workers = int(max_workers or os.environ.get('SCORMWEB_WORKERS', '2'))

    app = FastAPI(title='SCORM AI Analyzer', version='2.0')
    manager = JobManager(data_dir, max_workers=workers)
    app.state.jobs = manager

    @app.get('/', response_class=HTMLResponse)
    def index():
        return INDEX_HTML

    @app.get('/healthz')
    def healthz():
        return {'ok': True}

    @app.get('/api/config')
    def config():
        return {
            'llm_available': llm_mod.llm_available('claude')
                             or llm_mod.llm_available('deepseek'),
            'llm_provider': 'claude' if llm_mod.llm_available('claude')
                            else ('deepseek' if llm_mod.llm_available('deepseek')
                                  else None),
            'max_upload_mb': MAX_UPLOAD_MB,
        }

    @app.post('/api/analyze')
    async def submit(files: list[UploadFile] = File(...),
                     play: bool = Form(True),
                     llm: bool = Form(False),
                     max_slides: int = Form(40)):
        jobs = []
        for upload in files:
            name = upload.filename or 'package.zip'
            if not name.lower().endswith('.zip'):
                raise HTTPException(400, f'{name}: only .zip packages are accepted')
            data = await upload.read()
            if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
                raise HTTPException(413, f'{name}: exceeds {MAX_UPLOAD_MB} MB limit')
            if not data[:4].startswith(b'PK'):
                raise HTTPException(400, f'{name}: not a valid zip file')
            options = {'play': play, 'llm': llm, 'max_slides': max_slides}
            jobs.append(manager.submit(data, name, options).to_public())
        return {'jobs': jobs}

    @app.get('/api/jobs')
    def list_jobs():
        return {'jobs': [j.to_public() for j in manager.list()]}

    @app.get('/api/jobs/{job_id}')
    def job_detail(job_id: str):
        job = manager.get(job_id)
        if not job:
            raise HTTPException(404, 'Unknown job')
        return job.to_public()

    @app.get('/api/jobs/{job_id}/result.json')
    def job_result(job_id: str):
        job = manager.get(job_id)
        path = manager.result_path(job_id)
        if not job or not path.exists():
            raise HTTPException(404, 'No result for this job')
        return FileResponse(path, media_type='application/json',
                            filename=f'{Path(job.filename).stem}_analysis.json')

    @app.get('/jobs/{job_id}/report', response_class=HTMLResponse)
    def job_report(job_id: str):
        path = manager.report_path(job_id)
        if not path.exists():
            raise HTTPException(404, 'No report for this job')
        return HTMLResponse(path.read_text(encoding='utf-8'))

    @app.get('/report', response_class=HTMLResponse)
    def combined_report():
        results = manager.done_results()
        if not results:
            return HTMLResponse(
                '<h2 style="font-family:sans-serif;">No completed analyses yet — '
                'upload a package first.</h2>', status_code=404)
        return HTMLResponse(build_html_report(
            results, report_title='SCORM Extraction Report — All Packages'))

    @app.exception_handler(Exception)
    async def on_error(_request, exc):
        return JSONResponse(status_code=500, content={'error': str(exc)})

    return app


def main():
    import uvicorn
    port = int(os.environ.get('SCORMWEB_PORT', os.environ.get('PORT', '8080')))
    uvicorn.run(create_app(), host=os.environ.get('SCORMWEB_HOST', '0.0.0.0'),
                port=port)


if __name__ == '__main__':
    main()
