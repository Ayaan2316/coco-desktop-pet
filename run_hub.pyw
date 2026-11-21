r"""Starts the dashboard without a console: .venv\Scripts\pythonw.exe run_hub.pyw"""

from coco_hub.main import main

if __name__ in {"__main__", "__mp_main__"}:
    raise SystemExit(main())
