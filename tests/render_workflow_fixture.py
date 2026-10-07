"""Render the UI for mock browser tests; no desktop operations."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from server import make_app

if __name__=='__main__':
    destination=Path(sys.argv[1])
    with tempfile.TemporaryDirectory() as folder:
        app=make_app(Path(folder)/'config.json',validate_startup=False)
        try:destination.write_text(app.test_client().get('/').get_data(as_text=True),encoding='utf-8')
        finally:app.extensions['job_manager'].close()
