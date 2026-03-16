"""
main.py — FastAPI Backend
===========================
Provides REST endpoints for search jobs and serves the web UI.
Manages long-running search tasks in the background.
"""

import uuid
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel
from fastapi import FastAPI, BackgroundTasks, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from core.agent import ProcurementAgent

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
log = logging.getLogger(__name__)

app = FastAPI(title="IT Procurement Agent API")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Template setup
templates = Jinja2Templates(directory="templates")

# In-memory job store
# Structure: { job_id: { "status": str, "progress": int, "message": str, "result": dict } }
jobs: Dict[str, Dict[str, Any]] = {}

# Agent instance
agent = ProcurementAgent()

class SearchRequest(BaseModel):
    request_text: str

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the web UI."""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/search")
async def start_search(req: SearchRequest, background_tasks: BackgroundTasks):
    """Start a new procurement search job."""
    if not req.request_text or len(req.request_text.strip()) < 5:
        raise HTTPException(status_code=400, detail="Request text too short.")

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "id": job_id,
        "status": "running",
        "progress": 0,
        "message": "Initializing procurement agent...",
        "result": None
    }

    background_tasks.add_task(run_search_job, job_id, req.request_text)
    
    return {"job_id": job_id}

@app.get("/api/status/{job_id}")
async def get_status(job_id: str):
    """Poll the status of a specific job."""
    if job_id not in jobs:
        raise HTTPException(status_code=404, detail="Job not found.")
    
    return jobs[job_id]

async def run_search_job(job_id: str, text: str):
    """Background task to run the procurement pipeline."""
    
    def on_progress(msg: str, pct: int):
        if job_id in jobs:
            jobs[job_id]["progress"] = pct
            jobs[job_id]["message"] = msg

    try:
        log.info(f"Starting job {job_id} for text: {text[:50]}...")
        result = await agent.run(text, on_progress=on_progress)
        
        if job_id in jobs:
            jobs[job_id]["status"] = "done"
            jobs[job_id]["progress"] = 100
            jobs[job_id]["message"] = "Procurement complete."
            jobs[job_id]["result"] = result
            
    except Exception as e:
        log.exception(f"Job {job_id} failed with error: {e}")
        if job_id in jobs:
            jobs[job_id]["status"] = "error"
            jobs[job_id]["message"] = f"Error: {str(e)}"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
