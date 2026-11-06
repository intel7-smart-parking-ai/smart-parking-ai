@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install --upgrade torch torchvision --index-url https://download.pytorch.org/whl/xpu
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
".venv\Scripts\python.exe" main.py --check-device
if errorlevel 1 goto fail
echo Check xpu_available above. Then run main.py --check-ocr to load and test OCR.
pause
exit /b 0
:fail
echo Installation failed. Review the error above.
pause
exit /b 1
