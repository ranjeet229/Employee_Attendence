# Employee Attendance and Analytics API

FastAPI and MongoDB API for employee punch-in/out, regularization audit history, and attendance analytics. MongoDB 6+ is required.

## Run

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# set MONGO_URI and MONGO_DB
python sample_seed.py
uvicorn app.main:app --port 8000
```

Open `http://127.0.0.1:8000/docs` for interactive API documentation.
