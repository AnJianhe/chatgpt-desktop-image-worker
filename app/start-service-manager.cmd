@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath ($env:LOCALAPPDATA+'\ChatGPT-Worker\Python\pythonw.exe') -ArgumentList ([char]34+$env:LOCALAPPDATA+'\ChatGPT-Worker\image-automation\service_manager.py'+[char]34) -Verb RunAs"
