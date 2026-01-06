import sys
import os

if getattr(sys, 'frozen', False):
    app_dir = os.path.dirname(sys.executable)
else:
    app_dir = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, app_dir)

from ui.gui_main import main

if __name__ == "__main__":
    main()
