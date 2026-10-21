"""T-SAFE 중앙 서버 실행 진입점. 이 폴더에서 `python main_service.py` (= python run.py)."""
import os
import runpy

os.chdir(os.path.dirname(os.path.abspath(__file__)))
runpy.run_path("run.py", run_name="__main__")
